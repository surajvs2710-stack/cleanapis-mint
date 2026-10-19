#!/usr/bin/env python3
"""GitHub signup attempt from CI (fresh cloud IP). Artifacts land in output/."""
import json, os, re, sys, time, secrets, string, traceback
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as CO
from selenium.webdriver.firefox.options import Options as FO
from selenium.webdriver.common.by import By

OUT = "output"
os.makedirs(OUT, exist_ok=True)
R = {"stage": "start"}


def save(name, data):
    try:
        open(os.path.join(OUT, name), "w").write(data if isinstance(data, str) else json.dumps(data, indent=1))
    except Exception:
        pass


def mailtm():
    doms = requests.get("https://api.mail.tm/domains", timeout=25).json()
    doms = doms.get("hydra:member") or []
    for d in doms[:6]:
        dom = d.get("domain")
        addr = "gh" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(9)) + "@" + dom
        pw = "Gh!" + secrets.token_urlsafe(10)
        try:
            requests.post("https://api.mail.tm/accounts", json={"address": addr, "password": pw}, timeout=25)
            tok = requests.post("https://api.mail.tm/token", json={"address": addr, "password": pw}, timeout=25).json()["token"]
            return addr, pw, tok
        except Exception as e:
            print("mailtm dom fail", dom, str(e)[:60], flush=True)
    raise RuntimeError("mailtm fail")


def fetch_code(tok, maxsec=300):
    t0 = time.time()
    while time.time() - t0 < maxsec:
        try:
            ms = requests.get("https://api.mail.tm/messages", headers={"Authorization": "Bearer " + tok}, timeout=25).json().get("hydra:member") or []
            for m in ms:
                blob = (str(m.get("subject")) + " " + str((m.get("from") or {}).get("address"))).lower()
                if "github" in blob:
                    det = requests.get("https://api.mail.tm/messages/" + str(m["id"]), headers={"Authorization": "Bearer " + tok}, timeout=25).json()
                    body = (det.get("text") or "") + " " + (det.get("html") or "")
                    codes = re.findall(r"\b(\d{6,8})\b", body)
                    if codes:
                        return codes[0]
        except Exception:
            pass
        time.sleep(12)
    return None


def blocked(d):
    try:
        src = d.page_source or ""
    except Exception:
        return True
    low = src.lower()
    return ("captcha-delivery" in src) or ("temporarily restricted" in low) or ("access to this page has been denied" in low)


def find(d, sels):
    for s in sels:
        try:
            for e in d.find_elements(By.CSS_SELECTOR, s):
                if e.is_displayed():
                    return e
        except Exception:
            pass
    return None


def click(d, words):
    try:
        for el in d.find_elements(By.XPATH, "//button|//input[@type='submit']|//a"):
            try:
                t = (el.text or el.get_attribute("value") or "").strip().lower()
                if t and any(w in t for w in words):
                    el.click()
                    return t
            except Exception:
                pass
    except Exception:
        pass
    return None


def curl_check():
    try:
        r = requests.get("https://github.com/signup", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"}, timeout=30)
        return {"code": r.status_code, "len": len(r.text), "user_email": "user[email]" in r.text, "captcha": "captcha-delivery" in r.text}
    except Exception as e:
        return {"err": str(e)[:100]}


def try_driver(kind, addr, pw, user, mailtok):
    res = {"kind": kind, "pages": []}
    if kind == "chrome":
        o = CO()
        for cb in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"):
            if os.path.exists(cb):
                o.binary_location = cb
                break
        o.add_argument("--no-sandbox")
        o.add_argument("--disable-dev-shm-usage")
        o.add_argument("--window-size=1366,900")
        o.add_argument("--disable-blink-features=AutomationControlled")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        d = webdriver.Chrome(options=o)
    elif kind == "firefox":
        if not os.path.exists("/usr/bin/firefox"):
            return {"kind": kind, "verdict": "no-firefox-binary"}
        o = FO()
        o.binary_location = "/usr/bin/firefox"
        d = webdriver.Firefox(options=o)
    else:
        return {"kind": kind, "verdict": "unknown-kind"}
    try:
        d.set_page_load_timeout(90)
        try:
            d.get("https://github.com/")
            time.sleep(4)
        except Exception:
            pass
        try:
            d.get("https://github.com/signup")
        except Exception as e:
            res["goto_err"] = str(e)[:100]
        form = False
        t0 = time.time()
        while time.time() - t0 < 40:
            time.sleep(3)
            if find(d, ['input#email', 'input[name="user[email]"]', 'input[type="email"]']):
                form = True
                break
            try:
                d.execute_script("return 1")
            except Exception:
                break
        tag = "form" if form else ("blocked" if blocked(d) else "unknown")
        res["pages"].append({"url": (d.current_url or "")[:80], "verdict": tag})
        save("page_%s_1.html" % kind, (d.page_source or "")[:200000])
        if tag != "form":
            res["verdict"] = tag
            return res
        gh_user = user
        for step in range(8):
            src = d.page_source or ""
            save("page_%s_step%d.html" % (kind, step), src[:250000])
            if ("dashboard" in (d.current_url or "")) or ("Signed in as" in src):
                break
            el = find(d, ['input#launch_code', 'input[name="launch_code"]', 'input[autocomplete="one-time-code"]'])
            if el and not el.get_attribute("value"):
                code = fetch_code(mailtok)
                res.setdefault("code", code)
                if not code:
                    res["verdict"] = "no-email-code"
                    return res
                el.send_keys(code)
                click(d, ["verify", "continue", "submit"])
                time.sleep(6)
                continue
            e = find(d, ['input#email', 'input[name="user[email]"]', 'input[type="email"]'])
            if e and not e.get_attribute("value"):
                e.send_keys(addr)
            e = find(d, ['input#password', 'input[name="user[password]"]', 'input[type="password"]'])
            if e and not e.get_attribute("value"):
                e.send_keys(pw)
            e = find(d, ['input#login', 'input[name="user[login]"]', 'input[name="login"]'])
            if e and not e.get_attribute("value"):
                e.send_keys(gh_user)
            c = click(d, ["create account", "create an account", "sign up", "continue", "verify"])
            if not c:
                res["verdict"] = "stuck-step-%d" % step
                return res
            time.sleep(6)
        ok = ("dashboard" in (d.current_url or "")) or ("Signed in as" in (d.page_source or ""))
        res["verdict"] = "SIGNUP-OK" if ok else "unknown-end"
        res["final_url"] = (d.current_url or "")[:90]
        return res
    finally:
        try:
            d.quit()
        except Exception:
            pass


def main():
    R["curl"] = curl_check()
    print("curl:", R["curl"], flush=True)
    addr, mpw, mailtok = mailtm()
    R["email"] = addr
    user = "hx" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(9))
    pw = "Gh!" + secrets.token_urlsafe(12)
    R["user"] = user
    print("attempting with", addr, user, flush=True)
    for kind in ("chrome", "firefox"):
        try:
            r = try_driver(kind, addr, pw, user, mailtok)
            R[kind] = r
            print(kind, "verdict:", r.get("verdict"), flush=True)
            if r.get("verdict") == "SIGNUP-OK":
                save("signup_result.json", {"email": addr, "mailpw": mpw, "mailtok": mailtok, "user": user, "pw": pw, "via": kind})
                break
        except Exception as e:
            R[kind + "_err"] = (str(e)[:200] + " | " + traceback.format_exc()[:300])
            print(kind, "err:", str(e)[:200], flush=True)
    save("gh_result.json", R)
    short = {}
    for k, v in R.items():
        if isinstance(v, dict) and "pages" in v:
            v = {kk: vv for kk, vv in v.items() if kk != "pages"}
        short[k] = v
    print("FINAL:", json.dumps(short)[:1000], flush=True)


if __name__ == "__main__":
    main()
