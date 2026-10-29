#!/usr/bin/env python3
# cpapi.py — CỔNG BẢO VỆ + API điều khiển điện thoại đám mây.
#  • Đứng TRƯỚC ws-scrcpy (cổng 8000): khách chỉ mở được máy CỦA MÌNH, admin mở được tất cả.
#  • API /cpapi/...: phím Home/Back/Đa nhiệm/âm lượng, tải file & APK lên, cài từ link,
#    chụp màn hình, mở link, quản lý ứng dụng, khởi động lại máy.
# Cloudflare tunnel: phone.blueroblox.shop → http://localhost:8010   (KHÔNG trỏ thẳng 8000 nữa)
# Cấu hình đọc chung ~/cloudphone.conf (SUPABASE_URL, SUPABASE_SERVICE_KEY, PUBLIC_BASE).
import base64, hashlib, hmac, http.client, ipaddress, json, os, re, secrets, shlex, shutil, socket
import subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
CONF = os.path.join(HOME, "cloudphone.conf")
LISTEN = ("127.0.0.1", 8010)
UPSTREAM = ("127.0.0.1", 8000)          # ws-scrcpy
WSNAME = "wsscrcpy"
UP_DIR = os.path.join(HOME, "redroid", "_uploads")
SECRET_FILE = os.path.join(HOME, ".cpapi_secret")
COOKIE = "cpauth"
COOKIE_TTL = 12 * 3600
MAX_CHUNK = 60 * 1024 * 1024            # Cloudflare giới hạn 100MB/lần gửi → chia nhỏ
MAX_FILE = 4 * 1024 ** 3                # tối đa 4GB / file
KEYS = {3: "Home", 4: "Back", 187: "Đa nhiệm", 24: "Tăng âm", 25: "Giảm âm", 164: "Tắt tiếng",
        26: "Nguồn", 82: "Menu", 224: "Đánh thức", 223: "Ngủ", 220: "Giảm sáng", 221: "Tăng sáng"}
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
PKG = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$")


def load_conf():
    c = {}
    try:
        for line in open(CONF, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); c[k.strip()] = v.strip()
    except FileNotFoundError:
        print("THIẾU file cấu hình:", CONF); sys.exit(1)
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "PUBLIC_BASE"):
        if not c.get(k): print("THIẾU", k, "trong", CONF); sys.exit(1)
    return c


CFG = load_conf()
SB = CFG["SUPABASE_URL"].rstrip("/")
KEY = CFG["SUPABASE_SERVICE_KEY"]
PUBLIC_HOST = urllib.parse.urlparse(CFG["PUBLIC_BASE"]).netloc or "phone.blueroblox.shop"
SELF_ORIGIN = "https://" + PUBLIC_HOST
ORIGINS = {"https://blueroblox.shop", "https://www.blueroblox.shop"} | \
          {o.strip().rstrip("/") for o in CFG.get("CPAPI_ORIGINS", "").split(",") if o.strip()}


def load_secret():
    try:
        s = open(SECRET_FILE, "rb").read().strip()
        if len(s) >= 32: return s
    except FileNotFoundError:
        pass
    s = secrets.token_hex(32).encode()
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, s); os.close(fd)
    return s


SECRET = load_secret()
os.makedirs(UP_DIR, exist_ok=True)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------- Supabase ----------------
def sb_get(path, token=None):
    req = urllib.request.Request(SB + path, headers={"apikey": KEY, "Authorization": "Bearer " + (token or KEY)})
    with urllib.request.urlopen(req, timeout=15) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def sb_patch(path, body):
    req = urllib.request.Request(SB + path, data=json.dumps(body).encode(), method="PATCH",
                                 headers={"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json", "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=15) as r: r.read()


def fps_limits():
    try:
        p = (sb_get("/rest/v1/cloudphone_pricing?id=eq.1&select=min_fps,max_fps") or [{}])[0]
        return int(p.get("min_fps") or 10), int(p.get("max_fps") or 120)
    except Exception:
        return 10, 120


_ucache, _pcache = {}, {}
_busy = set()          # máy đang khởi động lại / đổi cấu hình


def whoami(token):
    """Kiểm tra token đăng nhập của khách (do Supabase cấp) → {uid, admin}."""
    if not token or len(token) > 4096: return None
    now = time.time(); c = _ucache.get(token)
    if c and c[1] > now: return c[0]
    try:
        u = sb_get("/auth/v1/user", token)
    except Exception:
        return None
    uid = u.get("id") if isinstance(u, dict) else None
    if not uid or not UUID.match(uid): return None
    try:
        rows = sb_get(f"/rest/v1/profiles?id=eq.{uid}&select=role,locked") or []
        p = rows[0] if rows else {}
    except Exception:
        p = {}
    if p.get("locked"): return None
    me = {"uid": uid, "admin": p.get("role") == "admin"}
    if len(_ucache) > 5000: _ucache.clear()
    _ucache[token] = (me, now + 300)
    return me


def phone_of(me, pid):
    if not UUID.match(pid or ""): return None
    rows = sb_get(f"/rest/v1/cloud_phones?id=eq.{pid}&select=*") or []
    if not rows: return None
    p = rows[0]
    if not me["admin"] and p.get("user_id") != me["uid"]: return None
    return p


def port_owners(port):
    now = time.time(); c = _pcache.get(port)
    if c and c[1] > now: return c[0]
    rows = sb_get(f"/rest/v1/cloud_phones?port=eq.{int(port)}&status=eq.active&select=user_id") or []
    owners = {r["user_id"] for r in rows}
    _pcache[port] = (owners, now + 20)
    return owners


# ---------------- Cookie ký HMAC (cho trình điều khiển) ----------------
def _mac(raw):
    return hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()[:40]


def sign(d):
    raw = base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
    return raw + "." + _mac(raw)


def verify(v):
    try:
        raw, mac = v.rsplit(".", 1)
        if not hmac.compare_digest(mac, _mac(raw)): return None
        d = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return d if d.get("exp", 0) > time.time() else None
    except Exception:
        return None


# ---------------- ADB ----------------
def adb(port, args, timeout=60):
    serial = f"127.0.0.1:{int(port)}"
    cmd = ["docker", "exec", WSNAME, "adb", "-s", serial] + args
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    err = (r.stderr or b"").decode(errors="ignore")
    if r.returncode != 0 and ("not found" in err or "offline" in err):
        subprocess.run(["docker", "exec", WSNAME, "adb", "connect", serial], capture_output=True, timeout=20)
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r


def shell(port, cmd, timeout=60):
    return adb(port, ["shell", cmd], timeout)


def out(r):
    return ((r.stdout or b"") + (r.stderr or b"")).decode(errors="ignore").strip()


# ---------------- Việc chạy nền (cài APK, tải link, khởi động lại) ----------------
JOBS, JLOCK = {}, threading.Lock()


def new_job(owner, title):
    jid = secrets.token_hex(8)
    with JLOCK:
        for k in [k for k, v in JOBS.items() if time.time() - v["t"] > 3600]: JOBS.pop(k, None)
        JOBS[jid] = {"owner": owner, "state": "running", "title": title, "msg": "Đang xử lý…", "t": time.time()}
    return jid


def run_job(jid, fn):
    def go():
        try:
            JOBS[jid].update(state="done", msg=fn(jid))
        except Exception as e:
            JOBS[jid].update(state="error", msg=str(e)[:400])
            log("job lỗi:", e)
    threading.Thread(target=go, daemon=True).start()


def safe_name(n):
    n = os.path.basename((n or "").replace("\\", "/")).strip()
    n = re.sub(r"[^\w.\-() \[\]+]", "_", n)[:120].strip(" .")
    return n or ("file_" + secrets.token_hex(3))


def deliver(port, path, name, jid=None):
    """File đã nằm trên server → đưa vào máy: APK thì cài, file khác vào thư mục Download."""
    tmp = "/tmp/cpup_" + secrets.token_hex(6)
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    try:
        if jid: JOBS[jid]["msg"] = "Đang chép vào máy…"
        r = subprocess.run(["docker", "cp", path, f"{WSNAME}:{tmp}"], capture_output=True, timeout=900)
        if r.returncode: raise RuntimeError("Không chép được file: " + out(r)[-200:])
        if ext == "apk":
            if jid: JOBS[jid]["msg"] = "Đang cài ứng dụng…"
            r = adb(port, ["install", "-r", "-g", tmp], timeout=1200)
            o = out(r)
            if "Success" not in o: raise RuntimeError("Cài APK lỗi: " + o[-300:])
            return f"Đã cài xong {name}"
        dest = "/sdcard/Download/" + name
        r = adb(port, ["push", tmp, dest], timeout=1200)
        if r.returncode: raise RuntimeError("Không chép được file vào máy: " + out(r)[-200:])
        shell(port, "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d " + shlex.quote("file://" + dest), 30)
        return f"Đã lưu vào thư mục Download: {name}"
    finally:
        subprocess.run(["docker", "exec", WSNAME, "rm", "-f", tmp], capture_output=True, timeout=60)
        try: os.remove(path)
        except OSError: pass


def public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme not in ("http", "https") or not public_host(u.hostname or ""):
            raise urllib.error.URLError("Link chuyển hướng tới địa chỉ không hợp lệ")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, jid):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or not public_host(u.hostname):
        raise RuntimeError("Link không hợp lệ (chỉ nhận link http/https công khai).")
    opener = urllib.request.build_opener(SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 11) CloudPhone"})
    with opener.open(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total > MAX_FILE: raise RuntimeError("File quá lớn (tối đa 4GB).")
        cd = r.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        name = safe_name(urllib.parse.unquote(m.group(1)) if m else urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(r.geturl()).path)))
        if "android.package-archive" in (r.headers.get("Content-Type") or "") and not name.lower().endswith(".apk"):
            name += ".apk"
        path = os.path.join(UP_DIR, "dl_" + secrets.token_hex(6))
        got = 0
        with open(path, "wb") as f:
            while True:
                b = r.read(1024 * 1024)
                if not b: break
                got += len(b)
                if got > MAX_FILE: f.close(); os.remove(path); raise RuntimeError("File quá lớn (tối đa 4GB).")
                f.write(b)
                JOBS[jid]["msg"] = f"Đang tải về server… {got // 1048576}MB" + (f"/{total // 1048576}MB" if total else "")
    return path, name


def wait_ready(container, port):
    t0 = time.time()
    while time.time() - t0 < 240:
        r = subprocess.run(["docker", "exec", container, "/system/bin/getprop", "sys.boot_completed"], capture_output=True, timeout=20)
        if (r.stdout or b"").strip() == b"1": break
        time.sleep(5)
    for _ in range(6):
        subprocess.run(["docker", "exec", WSNAME, "adb", "connect", f"127.0.0.1:{port}"], capture_output=True, timeout=20)
        r = adb(port, ["get-state"], 20)
        if (r.stdout or b"").strip() == b"device": return True
        time.sleep(4)
    return False


HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 120
    server_version = "cpapi"

    def log_message(self, fmt, *args):
        pass

    # ---------- tiện ích ----------
    def cors(self):
        o = self.headers.get("Origin", "")
        if o in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def send_json(self, code, obj, extra=None):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []): self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def deny(self, code, msg):
        b = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)
        self.close_connection = True

    def body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit: raise ValueError("Dữ liệu quá lớn")
        return self.rfile.read(n) if n else b""

    def jbody(self):
        try:
            return json.loads(self.body(65536) or b"{}")
        except Exception:
            return {}

    def cookie_auth(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE: return verify(v)
        return None

    # ---------- định tuyến ----------
    def do_OPTIONS(self):
        if self.path.startswith("/cpapi/"):
            self.send_response(204)
            self.cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.proxy()

    def do_GET(self):
        self.api() if self.path.startswith("/cpapi/") else self.proxy()

    def do_HEAD(self):
        self.proxy()

    def do_POST(self):
        self.api() if self.path.startswith("/cpapi/") else self.proxy()

    # ---------- API ----------
    def api(self):
        try:
            self._api()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": "Máy phản hồi quá lâu, thử lại."})
        except Exception as e:
            log("API lỗi:", repr(e))
            self.send_json(500, {"error": "Lỗi máy chủ: " + str(e)[:200]})

    def _api(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p][1:]
        if self.headers.get("Origin") and self.headers.get("Origin") not in ORIGINS:
            return self.send_json(403, {"error": "Nguồn không được phép."})
        tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        me = whoami(tok)
        if not me: return self.send_json(401, {"error": "Phiên đăng nhập hết hạn — tải lại trang và đăng nhập lại."})

        if parts == ["session"] and self.command == "POST":
            val = sign({"u": me["uid"], "a": me["admin"], "exp": int(time.time()) + COOKIE_TTL})
            ck = f"{COOKIE}={val}; Path=/; Max-Age={COOKIE_TTL}; Secure; HttpOnly; SameSite=None"
            return self.send_json(200, {"ok": True, "admin": me["admin"]}, [("Set-Cookie", ck)])

        if parts and parts[0] == "job":
            j = JOBS.get(q.get("id", ""))
            if not j or (j["owner"] != me["uid"] and not me["admin"]): return self.send_json(404, {"error": "Không có việc này."})
            return self.send_json(200, {k: j[k] for k in ("state", "msg", "title")})

        if len(parts) != 2: return self.send_json(404, {"error": "Không có chức năng này."})
        pid, act = parts
        p = phone_of(me, pid)
        if not p: return self.send_json(404, {"error": "Không tìm thấy máy của bạn."})
        port, cont = p.get("port"), p.get("container")
        if act == "info":
            st = out(adb(port, ["get-state"], 15)) if port and p["status"] == "active" else ""
            return self.send_json(200, {"name": p["name"], "ram": p["ram"], "cpu": p["cpu"], "res": p.get("res"),
                                        "status": p["status"], "expires_at": p.get("expires_at"), "online": st == "device",
                                        "fps": p.get("fps") or 30, "fps_range": fps_limits(), "busy": pid in _busy})
        if p["status"] != "active" or not port:
            return self.send_json(409, {"error": "Máy chưa chạy (đang tạo, tạm dừng hoặc đã huỷ)."})

        if act == "key" and self.command == "POST":
            code = int(self.jbody().get("code") or 0)
            if code not in KEYS: return self.send_json(400, {"error": "Phím không hợp lệ."})
            shell(port, f"input keyevent {code}", 15)
            return self.send_json(200, {"ok": True})

        if act == "text" and self.command == "POST":
            t = str(self.jbody().get("text") or "")[:2000]
            if not t: return self.send_json(400, {"error": "Chưa nhập chữ."})
            if any(ord(ch) > 126 or (ord(ch) < 32 and ch != "\n") for ch in t):
                return self.send_json(400, {"error": "Chỉ dán được chữ KHÔNG DẤU. Chữ có dấu hãy gõ trực tiếp trên màn hình máy."})
            for i, line in enumerate(t.split("\n")):
                if i: shell(port, "input keyevent 66", 10)
                if line: shell(port, "input text " + shlex.quote(line.replace("%", "").replace(" ", "%s")), 30)
            return self.send_json(200, {"ok": True})

        if act == "url" and self.command == "POST":
            link = str(self.jbody().get("url") or "").strip()
            if not re.match(r"^https?://\S+$", link) or len(link) > 2000: return self.send_json(400, {"error": "Link phải bắt đầu bằng http:// hoặc https://"})
            r = shell(port, "am start -a android.intent.action.VIEW -d " + shlex.quote(link), 20)
            return self.send_json(200, {"ok": True, "out": out(r)[-200:]})

        if act == "rotate" and self.command == "POST":
            d = 1 if self.jbody().get("dir") else 0
            shell(port, f"settings put system accelerometer_rotation 0; settings put system user_rotation {d}", 15)
            return self.send_json(200, {"ok": True})

        if act == "shot":
            r = adb(port, ["exec-out", "screencap", "-p"], 30)
            if r.returncode or not r.stdout.startswith(b"\x89PNG"): return self.send_json(500, {"error": "Không chụp được màn hình."})
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(r.stdout)))
            self.end_headers()
            return self.wfile.write(r.stdout)

        if act == "apps":
            r = shell(port, "pm list packages -3", 30)
            pk = sorted(l.split(":", 1)[1].strip() for l in out(r).splitlines() if l.startswith("package:"))
            return self.send_json(200, {"apps": pk})

        if act in ("launch", "stop", "uninstall", "clear") and self.command == "POST":
            pkg = str(self.jbody().get("pkg") or "")
            if not PKG.match(pkg): return self.send_json(400, {"error": "Tên ứng dụng không hợp lệ."})
            if act == "launch": r = shell(port, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1", 20)
            elif act == "stop": r = shell(port, f"am force-stop {pkg}", 15)
            elif act == "clear": r = shell(port, f"pm clear {pkg}", 30)
            else: r = adb(port, ["uninstall", pkg], 60)
            return self.send_json(200, {"ok": True, "out": out(r)[-200:]})

        if act == "upload" and self.command == "POST":
            upid = q.get("id", "")
            if not re.match(r"^[a-z0-9]{8,40}$", upid): return self.send_json(400, {"error": "Mã tải lên không hợp lệ."})
            name = safe_name(q.get("name", ""))
            total, off = int(q.get("total") or 0), int(q.get("off") or 0)
            if total <= 0 or total > MAX_FILE: return self.send_json(400, {"error": "File rỗng hoặc lớn hơn 4GB."})
            path = os.path.join(UP_DIR, f"{pid}_{upid}.part")
            if off == 0:                         # dọn file tải dở bị bỏ quá 6 giờ
                for f in os.listdir(UP_DIR):
                    fp = os.path.join(UP_DIR, f)
                    try:
                        if time.time() - os.path.getmtime(fp) > 6 * 3600: os.remove(fp)
                    except OSError: pass
            have = os.path.getsize(path) if os.path.exists(path) else 0
            if off == 0 and have == 0 and shutil.disk_usage(UP_DIR).free < total + 3 * 1024 ** 3:
                return self.send_json(507, {"error": "Server tạm hết dung lượng, thử lại sau."})
            if off != have: return self.send_json(409, {"error": "Lệch vị trí", "size": have})
            data = self.body(MAX_CHUNK)
            if have + len(data) > total: return self.send_json(400, {"error": "Dữ liệu vượt kích thước file."})
            with open(path, "ab") as f: f.write(data)
            have += len(data)
            if have < total: return self.send_json(200, {"size": have})
            jid = new_job(me["uid"], name)
            run_job(jid, lambda j: deliver(port, path, name, j))
            return self.send_json(200, {"size": have, "job": jid})

        if act == "fromurl" and self.command == "POST":
            link = str(self.jbody().get("url") or "").strip()
            if not re.match(r"^https?://\S+$", link) or len(link) > 2000: return self.send_json(400, {"error": "Link phải bắt đầu bằng http:// hoặc https://"})
            jid = new_job(me["uid"], link[-60:])
            def work(j):
                path, name = download(link, j)
                JOBS[j]["title"] = name
                return deliver(port, path, name, j)
            run_job(jid, work)
            return self.send_json(200, {"job": jid})

        if act == "display" and self.command == "POST":
            b = self.jbody(); lo, hi = fps_limits()
            try: fps = int(b.get("fps") or p.get("fps") or 30)
            except Exception: return self.send_json(400, {"error": "FPS không hợp lệ."})
            res = re.sub(r"\s", "", str(b.get("res") or p.get("res") or "720x1280")).lower().replace("*", "x").replace("×", "x")
            m = re.match(r"^(\d{3,4})x(\d{3,4})$", res)
            if not (lo <= fps <= hi): return self.send_json(400, {"error": f"FPS phải từ {lo} đến {hi}."})
            if not m or not all(320 <= int(v) <= 4096 for v in m.groups()): return self.send_json(400, {"error": "Độ phân giải phải dạng rộng x cao, VD 1080x1920."})
            if pid in _busy: return self.send_json(409, {"error": "Máy đang được đổi cấu hình, đợi chút."})
            jid = new_job(me["uid"], f"{res} · {fps} FPS")
            def work(j):
                _busy.add(pid)
                try:
                    JOBS[j]["msg"] = "Đang áp dụng và khởi động lại máy (1–3 phút)…"
                    r = subprocess.run(["python3", os.path.join(HOME, "phonectl.py"), "reconfig", cont, "--fps", str(fps), "--res", res],
                                       capture_output=True, text=True, timeout=600)
                    if r.returncode: raise RuntimeError("Đổi cấu hình lỗi: " + (r.stdout + r.stderr)[-300:])
                    sb_patch(f"/rest/v1/cloud_phones?id=eq.{pid}", {"fps": fps, "res": res})
                    return f"Đã đổi sang {res} · {fps} FPS. Dữ liệu vẫn giữ nguyên."
                finally:
                    _busy.discard(pid)
            run_job(jid, work)
            return self.send_json(200, {"job": jid})

        if act == "reboot" and self.command == "POST":
            if not cont: return self.send_json(409, {"error": "Không xác định được máy."})
            jid = new_job(me["uid"], "Khởi động lại")
            def work(j):
                subprocess.run(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{port}"], capture_output=True, timeout=20)
                r = subprocess.run(["docker", "restart", cont], capture_output=True, timeout=120)
                if r.returncode: raise RuntimeError("Không khởi động lại được: " + out(r)[-200:])
                JOBS[j]["msg"] = "Đang khởi động Android (1–3 phút)…"
                return "Máy đã khởi động lại xong." if wait_ready(cont, port) else "Máy đã khởi động, đang kết nối lại…"
            run_job(jid, work)
            return self.send_json(200, {"job": jid})

        return self.send_json(404, {"error": "Không có chức năng này."})

    # ---------- Cổng bảo vệ trước ws-scrcpy ----------
    def proxy(self):
        try:
            self._proxy()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log("proxy lỗi:", repr(e))
            try: self.deny(502, "Trình điều khiển tạm thời không phản hồi.")
            except Exception: pass

    def _proxy(self):
        auth = self.cookie_auth()
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            origin = self.headers.get("Origin", "")
            if origin and origin != SELF_ORIGIN: return self.deny(403, "Nguồn không được phép.")
            if not auth: return self.deny(401, "Hãy mở máy từ trang blueroblox.shop.")
            if not auth.get("a"):
                if q.get("action") != "proxy-adb" or q.get("remote") != "tcp:8886": return self.deny(403, "Không được phép.")
                m = re.match(r"^127\.0\.0\.1:(\d{4,5})$", q.get("udid", ""))
                if not m or auth.get("u") not in port_owners(int(m.group(1))): return self.deny(403, "Đây không phải máy của bạn.")
            return self.pipe_ws()
        if self.command not in ("GET", "HEAD") and not (auth and auth.get("a")):
            return self.deny(403, "Không được phép.")
        return self.pipe_http()

    def pipe_http(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else None
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"cookie", "host", "content-length"}}
        hdr["Host"] = "%s:%d" % UPSTREAM
        c = http.client.HTTPConnection(*UPSTREAM, timeout=60)
        c.request(self.command, self.path, body=data, headers=hdr)
        r = c.getresponse()
        body = r.read()
        c.close()
        self.send_response(r.status, r.reason)
        for k, v in r.getheaders():
            if k.lower() not in HOP | {"content-length"}: self.send_header(k, v)
        if "text/html" in (r.getheader("Content-Type") or ""):
            self.send_header("Content-Security-Policy", "frame-ancestors " + " ".join(sorted(ORIGINS | {SELF_ORIGIN})))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def pipe_ws(self):
        up = socket.create_connection(UPSTREAM, timeout=10)
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for k, v in self.headers.items():
            if k.lower() == "cookie": continue
            lines.append(f"{k}: {'%s:%d' % UPSTREAM if k.lower() == 'host' else v}")
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        up.settimeout(None)
        cli = self.connection
        cli.settimeout(None)
        self.close_connection = True

        def client_to_up():
            try:
                while True:
                    d = self.rfile.read1(65536)
                    if not d: break
                    up.sendall(d)
            except Exception:
                pass
            finally:
                try: up.shutdown(socket.SHUT_WR)
                except Exception: pass

        t = threading.Thread(target=client_to_up, daemon=True)
        t.start()
        try:
            while True:
                d = up.recv(65536)
                if not d: break
                cli.sendall(d)
        except Exception:
            pass
        finally:
            try: up.close()
            except Exception: pass
            try: cli.shutdown(socket.SHUT_RDWR)
            except Exception: pass
        t.join(timeout=3)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    for f in os.listdir(UP_DIR):          # dọn file tải dở từ lần chạy trước
        try: os.remove(os.path.join(UP_DIR, f))
        except OSError: pass
    log(f"cpapi chạy ở {LISTEN[0]}:{LISTEN[1]} → bảo vệ ws-scrcpy {UPSTREAM[1]}, trang chủ {SELF_ORIGIN}")
    Server(LISTEN, H).serve_forever()
