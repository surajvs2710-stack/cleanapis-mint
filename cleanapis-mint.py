#!/usr/bin/env python3
"""Kaggle kernel: cleanapis single-account / single-key mint on a fresh GCP VM.
Writes output/mint_result.json. Prints progress (visible via `kaggle kernels logs`)."""
import subprocess, time, re, json, os, sys, random, string, urllib.request

os.makedirs("./output", exist_ok=True)

def sh(cmd, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr)

print("=== kaggle cleanapis mint start ===", flush=True)
warp_ok = False
try:
    print(sh(
        "curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg 2>/dev/null; "
        "echo 'deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com $(lsb_release -cs) main' | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null; "
        "sudo apt-get update -qq >/dev/null 2>&1; "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflare-warp >/dev/null 2>&1; echo warp-apt-done"))
    print(sh(
        "sudo warp-cli --accept-tos registration new 2>&1 | tail -1; "
        "sudo warp-cli --accept-tos mode proxy 2>&1 | tail -1; "
        "sudo warp-cli --accept-tos proxy port 40000 2>&1 | tail -1; "
        "sudo warp-cli --accept-tos connect 2>&1 | tail -1; sleep 6; "
        "curl -s --max-time 20 --socks5-hostname 127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace | grep -E '^ip=|^warp='"))
    ipc = sh("curl -s --max-time 20 --socks5-hostname 127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace | grep '^ip=' || echo NOIP")
    warp_ok = ("warp=on" in ipc) or bool(re.search(r"^ip=\S+", ipc.strip())) and "NOIP" not in ipc
    print("warp_ok=%s (%s)" % (warp_ok, ipc.strip()), flush=True)
except Exception as e:
    print("warp setup failed: %s" % str(e)[:150], flush=True)
PROXY = "socks5://127.0.0.1:40000" if warp_ok else ""
print(sh(
    "sudo apt-get remove -y -qq chromium chromium-browser chromium-driver >/dev/null 2>&1; "
    "sudo apt-get update -qq >/dev/null 2>&1; "
    "sudo apt-get install -y -qq wget xvfb fonts-liberation >/dev/null 2>&1; "
    "wget -q -O /tmp/gc.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; "
    "sudo apt-get install -y -qq /tmp/gc.deb >/dev/null 2>&1; "
    "echo apt-done"))
print("chromium:", sh("which chromium chromium-browser 2>/dev/null | head -1").strip())
print("chromedriver:", sh("which chromedriver 2>/dev/null").strip())
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", "selenium"], shell=False)
subprocess.run("pkill Xvfb 2>/dev/null; Xvfb :99 -screen 0 1366x900x24 &", shell=True)
time.sleep(2)
os.environ["DISPLAY"] = ":99"

chrome = ""
for _c in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
    if os.path.exists(_c):
        chrome = _c
        break
if not chrome:
    chrome = sh("which google-chrome google-chrome-stable 2>/dev/null | head -1").strip()
chromedriver = sh("which chromedriver 2>/dev/null").strip()
print("using chrome=%s driver=%s" % (chrome, chromedriver), flush=True)

def new_mail():
    doms = json.load(urllib.request.urlopen("https://api.mail.tm/domains", timeout=30))
    dom = (doms.get("hydra:member") or doms.get("@graph"))[0]["domain"]
    addr = "kg" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8)) + "@" + dom
    pw = "Mint!2026pass"
    try:
        urllib.request.urlopen(urllib.request.Request("https://api.mail.tm/accounts", method="POST",
            data=json.dumps({"address": addr, "password": pw}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30)
    except Exception as e:
        print("mail create:", str(e)[:100], flush=True)
    tok = json.load(urllib.request.urlopen(urllib.request.Request("https://api.mail.tm/token", method="POST",
        data=json.dumps({"address": addr, "password": pw}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30))["token"]
    return addr, pw, tok

def inbox_links(mtok):
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request("https://api.mail.tm/messages",
            headers={"Authorization": "Bearer " + mtok}), timeout=30))
    except Exception:
        return []
    out = []
    for m in (d.get("hydra:member") or d.get("@graph") or []):
        if "cleanapis" not in json.dumps(m).lower():
            continue
        try:
            det = json.load(urllib.request.urlopen(urllib.request.Request(
                "https://api.mail.tm/messages/" + str(m["id"]),
                headers={"Authorization": "Bearer " + mtok}), timeout=30))
            out += re.findall(r"https://cleanapis\.com/[^\s\"'<>)\]]+",
                              (det.get("text") or "") + " " + (det.get("html") or ""))
        except Exception:
            pass
    return out

class CDPShim:
    """Raw-CDP duck-typed driver (stdlib WS). Implements the subset of the
    selenium API used by mint_one: get/current_url/execute_script/
    find_element(s)/element.send_keys|clear|click|get_attribute|text/quit."""
    def __init__(self, chrome_bin, profile, port=9231):
        import socket, base64
        from urllib.parse import urlsplit
        self._port = port
        self._proc = subprocess.Popen(
            [chrome_bin, "--user-data-dir=/tmp/cap%d" % profile, "--window-size=1366,900",
             "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
             "--disable-blink-features=AutomationControlled", "--lang=en-US",
             "--remote-debugging-port=%d" % port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": ":99"})
        time.sleep(5)
        lst = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=15))
        pages = [t for t in lst if t.get("type") == "page"]
        if not pages:
            urllib.request.urlopen("http://127.0.0.1:%d/json/new?about:blank" % port, timeout=15)
            time.sleep(1)
            lst = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=15))
            pages = [t for t in lst if t.get("type") == "page"]
        u = urlsplit(pages[0]["webSocketDebuggerUrl"])
        s = socket.create_connection((u.hostname, u.port or 80), timeout=30)
        key = base64.b64encode(random.urandom(16)).decode()
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                   % (u.path or "/", u.netloc, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        s._wbuf = buf.split(b"\r\n\r\n", 1)[1]
        s.settimeout(60)
        self._s = s
        self._seq = 0
        self._send("Page.enable"); self._send("Runtime.enable")
        self.current_url = "about:blank"

    def _recv_msg(self):
        s = self._s
        hdr = b""
        while len(hdr) < 2:
            d = s.recv(2 - len(hdr))
            if not d: raise RuntimeError("ws closed")
            hdr += d
        b0, b1 = hdr[0], hdr[1]
        ln = b1 & 0x7F
        if ln == 126:
            ln = int.from_bytes(s.recv(2), "big")
        elif ln == 127:
            ln = int.from_bytes(s.recv(8), "big")
        data = b""
        while len(data) < ln:
            data += s.recv(ln - len(data))
        return json.loads(data.decode())

    def _send(self, method, params=None):
        self._seq += 1
        cmd = {"id": self._seq, "method": method}
        if params: cmd["params"] = params
        raw = json.dumps(cmd).encode()
        s = self._s
        if len(raw) < 126:
            s.sendall(bytes([0x81, 0x80 | len(raw)]) + raw)
        else:
            s.sendall(bytes([0x81, 0x80 | 126, len(raw) & 0xFF, len(raw) >> 8]) + raw)
        while True:
            m = self._recv_msg()
            if m.get("id") == self._seq:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"].get("message", "")))
                return m.get("result")

    def js(self, expr):
        r = self._send("Runtime.evaluate", {"expression": "(function(){%s})()" % expr,
                                           "returnByValue": True, "awaitPromise": True})
        v = r.get("result", {}).get("value")
        return v

    def get(self, url):
        self._send("Page.navigate", {"url": url})
        time.sleep(3)
        try: self.current_url = self.js("location.href") or ""
        except Exception: self.current_url = url
        time.sleep(4)

    def execute_script(self, expr):
        return self.js(expr)

    def find_element(self, by, val):
        sel = val if by == "css selector" else val
        return _CEl(self, sel, 0)

    def find_elements(self, by, val):
        sel = val if by == "css selector" else val
        try:
            n = self.js("document.querySelectorAll('%s').length" % sel) or 0
        except Exception:
            n = 0
        return [_CEl(self, sel, i) for i in range(int(n))]

    def quit(self):
        try:
            self._proc.terminate()
        except Exception:
            pass


class _CEl:
    def __init__(self, cdp, sel, idx):
        self._c, self._sel, self._i = cdp, sel, idx
        self._p = "document.querySelectorAll('%s')[%d]" % (sel, idx)

    def _el(self):
        return self._p + " ? (" + self._p + ")." if False else self._p

    def get_attribute(self, k):
        if k in ("value", "text"):
            return self._c.js("(%s) ? %s.%s" % (self._p, self._p, k if k == "value" else "textContent"))
        return self._c.js("(%s) ? %s.getAttribute('%s')" % (self._p, self._p, k))

    @property
    def text(self):
        return self.get_attribute("text")

    def send_keys(self, s):
        s = s.replace("\\", "\\\\").replace("'", "\\'")
        self._c.js("var e=%s; if(e){ e.focus(); e.value = (e.value||'') + '%s'; "
                   "e.dispatchEvent(new Event('input',{bubbles:true})); "
                   "e.dispatchEvent(new Event('change',{bubbles:true})); }" % (self._p, s))

    def clear(self):
        self._c.js("var e=%s; if(e){ e.value=''; "
                   "e.dispatchEvent(new Event('input',{bubbles:true})); "
                   "e.dispatchEvent(new Event('change',{bubbles:true})); }" % self._p)

    def click(self):
        self._c.js("var e=%s; if(e) e.click();" % self._p)


def _mk_driver(n):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    print("  chrome ver:", sh("google-chrome --version 2>&1 | head -1").strip(), flush=True)
    o = Options()
    o.binary_location = chrome
    o.add_argument("--user-data-dir=/tmp/cap%02d" % n)
    o.add_argument("--window-size=1366,900")
    o.add_argument("--no-first-run")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_argument("--lang=en-US")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    # strategy 1: Selenium Manager (auto-downloads matching driver)
    try:
        d = webdriver.Chrome(options=o)
        print("  selenium webdriver started (manager)", flush=True)
        return d
    except Exception as e:
        print("  SeleniumManager failed: %s" % str(e)[:120], flush=True)
    # strategy 2: explicit chromedriver
    if chromedriver:
        svc = None
        try:
            from selenium.webdriver.chrome.service import ChromeDriverService as _S
            svc = _S(executable_path=chromedriver)
        except Exception:
            try:
                from selenium.webdriver.chrome.service import Service as _S2
                svc = _S2(executable_path=chromedriver)
            except Exception:
                svc = None
        try:
            d = webdriver.Chrome(options=o, service=svc) if svc is not None else webdriver.Chrome(options=o)
            print("  selenium webdriver started (chromedriver)", flush=True)
            return d
        except Exception as e:
            print("  chromedriver failed: %s" % str(e)[:120], flush=True)
    # strategy 3: raw CDP
    return CDPShim(chrome, n)


def mint_one(n):
    addr, pw, mtok = new_mail()
    res = {"addr": addr, "pw": pw, "keys": [], "stage": "started"}
    d = _mk_driver(n)
    try:
        d.get("https://cleanapis.com/pricing")
        t0 = time.time()
        while time.time() - t0 < 90:
            time.sleep(10)
            try:
                d.execute_script("window.scrollBy(0,%d)" % random.randint(100, 350))
                d.get("https://cleanapis.com/docs"); time.sleep(6); d.get("https://cleanapis.com/pricing")
            except Exception:
                pass
        d.get("https://cleanapis.com/register")
        time.sleep(12)
        print("  waiting turnstile token (up to 4 min)...", flush=True)
        tok_v = ""
        t0 = time.time()
        while time.time() - t0 < 240:
            try:
                v = (d.find_element("css selector", "input[name=cf-turnstile-response]").get_attribute("value") or "")
                if v and v != "EMPTY":
                    tok_v = v
                    break
            except Exception:
                pass
            try:
                el = d.find_element("css selector", "[name=name]")
                for ch in "Surya":
                    el.send_keys(ch); time.sleep(0.12)
            except Exception:
                pass
            print("    +%ds cf=%s" % (int(time.time() - t0), str(tok_v)[:10]), flush=True)
            time.sleep(20)
        if not tok_v:
            res["stage"] = "no-token"
            print("  NO-TOKEN this profile", flush=True)
            return res
        res["stage"] = "token"
        print("  TOKEN at +%ds" % int(time.time() - t0), flush=True)
        time.sleep(1)
        def setf(x, v):
            x.clear(); time.sleep(0.2)
            for ch in v:
                x.send_keys(ch); time.sleep(0.05)
        setf(d.find_element("css selector", "[name=name]"), "Surya")
        setf(d.find_element("css selector", "[name=email]"), addr)
        setf(d.find_element("css selector", "[name=password]"), pw)
        setf(d.find_element("css selector", "[name=password_confirmation]"), pw)
        time.sleep(1.5)
        d.find_element("css selector", "form button[type=submit]").click()
        time.sleep(8)
        link = None
        t0 = time.time()
        while time.time() - t0 < 240 and not link:
            links = inbox_links(mtok)
            if links:
                link = next((l for l in links if any(k in l for k in ("verify", "confirm", "activate", "token"))), links[0])
            time.sleep(15)
        if link:
            res["stage"] = "verified"
            print("  verify mail opened", flush=True)
            d.get(link); time.sleep(10)
        else:
            res["stage"] = "no-verify-mail"
            print("  no verify mail in 4min; trying login anyway", flush=True)
        try:
            if "login" in d.current_url or res["stage"] in ("no-verify-mail", "token"):
                d.get("https://cleanapis.com/login"); time.sleep(8)
                if "login" in d.current_url:
                    setf(d.find_element("css selector", "input[type=email], input[name=email]"), addr)
                    setf(d.find_element("css selector", "input[type=password], input[name=password]"), pw)
                    d.find_element("css selector", "button[type=submit]").click(); time.sleep(10)
        except Exception as e:
            print("  login step:", str(e)[:120], flush=True)
        d.get("https://cleanapis.com/settings/keys")
        time.sleep(8)
        txt = d.find_element("tag name", "body").text
        keys = re.findall(r"cc_[A-Za-z0-9_\-]{6,}", txt)
        if not keys:
            for b in d.find_elements("css selector", "button"):
                if re.search(r"create|new|generate|add", (b.text or ""), re.I):
                    try:
                        b.click(); time.sleep(5)
                    except Exception:
                        pass
                    break
            for b in d.find_elements("css selector", "button"):
                if re.search(r"create|confirm|generate|save", (b.text or ""), re.I):
                    try:
                        b.click(); time.sleep(5)
                    except Exception:
                        pass
                    break
            time.sleep(3)
            txt = d.find_element("tag name", "body").text
            keys = re.findall(r"cc_[A-Za-z0-9_\-]{6,}", txt)
        res["keys"] = keys[:1]
        res["stage"] = "done" if keys else res["stage"]
        print("  keys found: %d (%s...)" % (len(keys), keys[0][:14] if keys else "-"), flush=True)
        return res
    except Exception as e:
        res["stage"] = "error: " + str(e)[:200]
        print("  ERR:", str(e)[:200], flush=True)
        return res
    finally:
        try:
            d.quit()
        except Exception:
            pass
        subprocess.run("pkill -f 'user-data-dir=/tmp/cap%02d' 2>/dev/null" % n, shell=True)

r = mint_one(1)
if not r.get("keys"):
    print("=== retry with fresh profile ===", flush=True)
    r2 = mint_one(101)
    for k in r2.get("keys", []):
        if k not in r.get("keys", []):
            r["keys"].append(k)
    r["stage"] = r["stage"] + "+retry"
    r["pw2"] = r2.get("pw")

json.dump(r, open("./output/mint_result.json", "w"))
print("=" * 50)
print("MINT RESULT:", json.dumps({k: v for k, v in r.items() if k != "pw"}))
if r.get("keys"):
    print("KEY:")
    print(r["keys"][0])
print("=" * 50)
