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
        26: "Nguồn", 82: "Menu", 279: "Dán", 278: "Copy", 277: "Cắt", 224: "Đánh thức", 223: "Ngủ", 220: "Giảm sáng", 221: "Tăng sáng"}
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
# Đường thẳng (không qua Cloudflare): FAST_BASE=https://fast.blueroblox.shop:8443 trong ~/cloudphone.conf (do fast-route.sh ghi)
FAST_ORIGIN = (CFG.get("FAST_BASE") or "").strip().rstrip("/")
SELF_ORIGINS = {SELF_ORIGIN} | ({FAST_ORIGIN} if FAST_ORIGIN else set())
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


_mcache = {"t": 0, "v": {"maint": False}}
def maint():
    """Trạng thái bảo trì (đọc lại mỗi 20 giây)."""
    if time.time() - _mcache["t"] > 20:
        try:
            req = urllib.request.Request(SB + "/rest/v1/rpc/cloudphone_status", data=b"{}", method="POST",
                                         headers={"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r: _mcache["v"] = json.loads(r.read().decode() or "{}")
        except Exception:
            pass
        _mcache["t"] = time.time()
    return _mcache["v"]


def maint_text():
    m = maint()
    return "Hệ thống đang bảo trì" + (": " + m["maint_msg"] if m.get("maint_msg") else "") + ". Vui lòng quay lại sau."


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


def is_locked(p):
    if not p.get("locked"): return False
    u = p.get("locked_until")
    if not u: return True
    try:
        return datetime_parse(u) > time.time()
    except Exception:
        return True


def datetime_parse(s):
    import datetime as _dt
    return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def lock_text(p):
    u = p.get("locked_until")
    when = ""
    if u:
        import datetime as _dt
        vn = _dt.timezone(_dt.timedelta(hours=7))          # giờ Việt Nam (server chạy giờ UTC)
        when = " đến " + _dt.datetime.fromtimestamp(datetime_parse(u), vn).strftime("%H:%M %d/%m/%Y")
    return "Máy đang bị khoá" + (when or " vĩnh viễn") + (": " + p["lock_reason"] if p.get("lock_reason") else "") + ". Liên hệ hỗ trợ nếu cần."


_scache = {}
def share_users(pid):
    """Người đang được chủ máy chia sẻ quyền điều khiển (bảng cloud_phone_shares, SQL v9). Chưa chạy v9 → rỗng."""
    now = time.time(); c = _scache.get(pid)
    if c and c[1] > now: return c[0]
    try:
        rows = sb_get(f"/rest/v1/cloud_phone_shares?phone_id=eq.{pid}&kind=eq.share&revoked=is.false&grantee_id=not.is.null"
                      f"&select=grantee_id,expires_at") or []
        users = {r["grantee_id"] for r in rows if not r.get("expires_at") or datetime_parse(r["expires_at"]) > now}
    except Exception:
        users = set()
    if len(_scache) > 5000: _scache.clear()
    _scache[pid] = (users, now + 20)
    return users


def phone_of(me, pid):
    if not UUID.match(pid or ""): return None
    rows = sb_get(f"/rest/v1/cloud_phones?id=eq.{pid}&select=*") or []
    if not rows: return None
    p = rows[0]
    if not me["admin"] and p.get("user_id") != me["uid"] and me["uid"] not in share_users(pid): return None
    return p


def port_owners(port):
    now = time.time(); c = _pcache.get(port)
    if c and c[1] > now: return c[0]
    rows = sb_get(f"/rest/v1/cloud_phones?port=eq.{int(port)}&status=eq.active&locked=is.false&select=id,user_id") or []
    owners = {r["user_id"] for r in rows}
    for r in rows: owners |= share_users(r["id"])          # người được chia sẻ cũng xem/điều khiển được
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
VIEWERS, VLOCK = {}, threading.Lock()      # cổng máy → nơi đang xem (mã trang cpv)


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
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    ext = re.sub(r"[^a-z0-9]", "", ext)[:10]
    tmp = "/tmp/cpup_" + secrets.token_hex(6) + ("." + ext if ext else "")   # adb install bắt buộc tên file đuôi .apk
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



# ---------------- Admin: cài 1 APK vào nhiều máy cùng lúc ----------------
APK_EXT = (".apk", ".apks", ".apkm", ".xapk")
ADMIN_FILES = {}          # mã file → (đường dẫn, tên)


def apk_min_sdk(path):
    """Đọc minSdkVersion (bản Android thấp nhất app cần) từ file .apk / gói .apks. Không đọc được → 0 (coi như cài được)."""
    import zipfile, struct
    try:
        with zipfile.ZipFile(path) as z:
            data = None
            if "AndroidManifest.xml" in z.namelist(): data = z.read("AndroidManifest.xml")
            else:
                ns = [n for n in z.namelist() if n.lower().endswith(".apk") and "/" not in n.strip("/")]
                ns.sort(key=lambda n: (0 if n.lower() in ("base.apk",) else 1 if "split" not in n.lower() else 2, n))
                if ns:
                    import io
                    with zipfile.ZipFile(io.BytesIO(z.read(ns[0]))) as b: data = b.read("AndroidManifest.xml")
        if not data: return 0
        strs, resmap, off = [], [], 8
        while off + 8 <= len(data):
            typ, hsz, sz = struct.unpack_from("<HHI", data, off)
            if sz < 8: break
            if typ == 0x0001:                                  # bảng chữ
                cnt, _, flags, sstart = struct.unpack_from("<IIII", data, off + 8)
                utf8 = flags & 0x100
                for i in range(cnt):
                    p = off + struct.unpack_from("<I", data, off + hsz + i * 4)[0] + sstart
                    if utf8:
                        n = data[p]; p += 2 if n & 0x80 else 1
                        n = data[p]; q = p + (2 if n & 0x80 else 1)
                        if n & 0x80: n = ((n & 0x7f) << 8) | data[p + 1]
                        strs.append(data[q:q + n].decode("utf-8", "replace"))
                    else:
                        n = struct.unpack_from("<H", data, p)[0]
                        strs.append(data[p + 2:p + 2 + n * 2].decode("utf-16le", "replace"))
            elif typ == 0x0180:
                resmap = list(struct.unpack_from("<%dI" % ((sz - 8) // 4), data, off + 8))
            elif typ == 0x0102:                                # thẻ mở
                name = struct.unpack_from("<I", data, off + 20)[0]
                if 0 <= name < len(strs) and strs[name] == "uses-sdk":
                    astart, asize, acnt = struct.unpack_from("<HHH", data, off + 24)
                    for i in range(acnt):
                        a = off + 16 + astart + i * asize
                        an = struct.unpack_from("<I", data, a + 4)[0]
                        dtype, val = data[a + 15], struct.unpack_from("<I", data, a + 16)[0]
                        if (an < len(resmap) and resmap[an] == 0x0101020c) or (an < len(strs) and strs[an] == "minSdkVersion"):
                            return val if dtype in (0x10, 0x11) else int(strs[val]) if dtype == 0x03 and strs[val].isdigit() else 0
                    return 1
            off += sz
    except Exception:
        return 0
    return 0

_MINSDK = {}
def min_sdk(path):
    k = (path, os.path.getmtime(path))
    if k not in _MINSDK: _MINSDK[k] = apk_min_sdk(path)
    return _MINSDK[k]

def pre_dir():
    import glob
    for d in [os.path.join(os.path.dirname(os.path.abspath(__file__)), "apk-cai-san"), os.path.join(HOME, "apk-cai-san")] + sorted(glob.glob("/home/*/apk-cai-san")):
        if os.path.isdir(d): return d
    d = os.path.join(HOME, "apk-cai-san"); os.makedirs(d, exist_ok=True); return d


def stage_pkg(path, name):
    """Chép gói vào container điều khiển 1 lần → trả về danh sách file .apk bên trong (để cài cho nhiều máy)."""
    import zipfile, tempfile
    tag = secrets.token_hex(5)
    if name.lower().endswith(".apk"):
        dst = f"/tmp/bulk_{tag}.apk"
        r = subprocess.run(["docker", "cp", path, f"{WSNAME}:{dst}"], capture_output=True, timeout=900)
        if r.returncode: raise RuntimeError("Không chép được file: " + out(r)[-200:])
        return [dst], dst
    d = tempfile.mkdtemp(prefix="bulk_", dir=UP_DIR)
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".apk") and "/" not in n.strip("/")]
            if not names: raise RuntimeError("Gói không có file .apk bên trong.")
            for n in names: z.extract(n, d)
        dst = f"/tmp/bulk_{tag}"
        r = subprocess.run(["docker", "cp", d, f"{WSNAME}:{dst}"], capture_output=True, timeout=900)
        if r.returncode: raise RuntimeError("Không chép được file: " + out(r)[-200:])
        return [f"{dst}/{n}" for n in names], dst
    except zipfile.BadZipFile:
        raise RuntimeError("File gói hỏng hoặc bị mã hoá.")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def bulk_install(jid, fid, ids, every, keep):
    path, name = ADMIN_FILES.get(fid, (None, None))
    if not path or not os.path.exists(path): raise RuntimeError("File đã hết hạn, tải lên lại.")
    q = "cloud_phones?status=eq.active&select=id,name,port,container" + ("" if every else "&id=in.(" + ",".join(ids) + ")")
    rows = [r for r in (sb_get("/rest/v1/" + q) or []) if r.get("port")]
    if not rows: raise RuntimeError("Không có máy đang chạy nào trong danh sách.")
    if keep:
        shutil.copy(path, os.path.join(pre_dir(), name)); JOBS[jid]["msg"] = "Đã thêm vào app cài sẵn cho máy mới…"
    files, dst = stage_pkg(path, name)
    ok, bad, lock = [], [], threading.Lock()
    def one(r):
        args = (["install", "-r", "-g", files[0]] if len(files) == 1 else ["install-multiple", "-r", "-g"] + files)
        try:
            res = adb(r["port"], args, timeout=900); o = out(res)
            good = "Success" in o
        except Exception as e:
            good, o = False, str(e)
        if good and keep:                                    # đánh dấu "đã cài sẵn" (qua adb: chạy được cả máy thường lẫn máy GPU)
            try: adb(r["port"], ["shell", "touch /data/local/tmp/.cp_pre_" + re.sub(r"[^\w.-]", "_", name)], timeout=60)
            except Exception: pass
        with lock:
            (ok if good else bad).append(r["name"] if good else f"{r['name']}: {o[-120:]}")
            JOBS[jid]["msg"] = f"Đang cài {len(ok) + len(bad)}/{len(rows)} máy… (lỗi {len(bad)})"
            JOBS[jid]["done"], JOBS[jid]["total"] = len(ok) + len(bad), len(rows)
    try:
        ths = []
        for r in rows:                                        # cài song song tối đa 4 máy
            while sum(t.is_alive() for t in ths) >= 4: time.sleep(0.3)
            t = threading.Thread(target=one, args=(r,), daemon=True); t.start(); ths.append(t)
        for t in ths: t.join()
    finally:
        subprocess.run(["docker", "exec", WSNAME, "rm", "-rf", dst], capture_output=True, timeout=60)
    JOBS[jid]["fails"] = bad[:50]
    return f"Đã cài {name} cho {len(ok)}/{len(rows)} máy" + (f" · lỗi {len(bad)} máy" if bad else "") + (" · máy mới tạo sẽ tự có" if keep else "")

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
    while time.time() - t0 < 300:             # hỏi qua adb → dùng được cho cả máy thường (redroid) lẫn máy GPU
        try:
            subprocess.run(["docker", "exec", WSNAME, "adb", "connect", f"127.0.0.1:{port}"], capture_output=True, timeout=20)
            r = adb(port, ["shell", "getprop", "sys.boot_completed"], 20)
            if (r.stdout or b"").strip() == b"1": break
        except Exception: pass
        time.sleep(5)
    for _ in range(6):
        subprocess.run(["docker", "exec", WSNAME, "adb", "connect", f"127.0.0.1:{port}"], capture_output=True, timeout=20)
        r = adb(port, ["get-state"], 20)
        if (r.stdout or b"").strip() == b"device": return True
        time.sleep(4)
    return False


# Đoạn chèn vào trang ws-scrcpy: hình khít khung + độ nét/FPS + báo trạng thái + nhận lệnh phím
INJECT_JS = '/* Chèn vào trang ws-scrcpy (qua cpapi): hình khít khung, độ nét/FPS theo máy, báo trạng thái, nhận lệnh phím từ trang blueroblox. */\n(function () {\n  "use strict";\n  var PARENTS = ["https://blueroblox.shop", "https://www.blueroblox.shop"];\n  var H = new URLSearchParams(location.hash.replace(/^#!?/, ""));\n  if (H.get("action") !== "stream") return;\n  if (H.get("cpraw") === "1") {          // chế độ gốc: chỉ báo "trang đã tải xong", không đụng gì vào ws-scrcpy\n    if (parent !== window) for (var j = 0; j < PARENTS.length; j++) { try { parent.postMessage({ cp: 1, n: H.get("cpn") || "", t: "boot", raw: 1 }, PARENTS[j]); } catch (e) {} }\n    return;\n  }\n  document.documentElement.classList.add("cp-embed");\n  var Q = H.get("cpq") || "bal";\n  var FPS = Math.max(10, Math.min(120, +H.get("cpfps") || 30));\n  var RW = +H.get("cpw") || 720, RH = +H.get("cph") || 1280;\n  //            cạnh dài tối đa, bitrate, FPS tối đa\n  // Máy ảo nén video bằng CPU → hình càng to / FPS càng cao càng trễ. Mặc định (bal) ưu tiên ÍT TRỄ như UgPhone.\n  var PRE = { eco: [720, 1200000, 24], bal: [960, 2500000, 30], hd: [1280, 5000000, 60] }[Q] || [960, 2500000, 30];\n  var M = Math.min(PRE[0], Math.max(RW, RH)); M -= M % 8;\n  var F = Math.min(FPS, PRE[2]);\n  var VS = JSON.stringify({ bitrate: PRE[1], maxFps: F, frameRate: F, iFrameInterval: 2, bounds: { width: M, height: M },\n    maxSize: M, sendFrameMeta: false, lockedVideoOrientation: -1, displayId: 0, crop: null });\n  var KEY = /^\\w+:127\\.0\\.0\\.1:\\d+/;\n  try {   // cài đặt hình do trang blueroblox quyết định (ws-scrcpy mặc định chỉ 480px · 24 FPS · 0.5 Mbps)\n    var gi = Storage.prototype.getItem, si = Storage.prototype.setItem;\n    Storage.prototype.getItem = function (k) { if (typeof k === "string" && KEY.test(k)) return /:fit$/.test(k) ? "false" : VS; return gi.apply(this, arguments); };\n    Storage.prototype.setItem = function (k) { if (typeof k === "string" && KEY.test(k)) return; return si.apply(this, arguments); };\n  } catch (e) {}\n\n  var SID = H.get("cpn") || "";\n  function post(m) {\n    m.cp = 1; m.n = SID;\n    try { if (window.BRApp && BRApp.onEvent) BRApp.onEvent(JSON.stringify(m)); } catch (e) {}   // app Android BlueRoblox\n    if (parent === window) return;\n    for (var i = 0; i < PARENTS.length; i++) { try { parent.postMessage(m, PARENTS[i]); } catch (e) {} }\n  }\n\n  // Theo dõi luồng hình: có dữ liệu → báo "ok"; rớt → báo "closed" để trang ngoài tự nối lại\n  var NWS = window.WebSocket, bytes = 0, last = 0, okSent = false;\n  function W(url, proto) {\n    var ws = arguments.length > 1 ? new NWS(url, proto) : new NWS(url);\n    if (/action=proxy-adb/.test(String(url))) {\n      ws.addEventListener("open", function () { post({ t: "open" }); });\n      ws.addEventListener("message", function (e) {\n        var d = e.data; bytes += (d && (d.byteLength || d.size || d.length)) || 0;\n        if (!okSent && bytes > 30000) { okSent = true; post({ t: "ok" }); }\n      });\n      ws.addEventListener("close", function (e) { post({ t: "closed", code: e.code, ok: okSent }); });\n    }\n    return ws;\n  }\n  W.prototype = NWS.prototype; W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;\n  window.WebSocket = W;\n  setInterval(function () { var kb = (bytes - last) * 8 / 2000; last = bytes; post({ t: "stat", kbps: Math.round(kb) }); }, 2000);\n\n  // Hình luôn khít khung, đúng tỉ lệ (không viền đen thừa → chạm chính xác)\n  var lw = 0, lh = 0;\n  function layout() {\n    var v = document.querySelector(".video"), c = document.querySelector(".touch-layer");\n    if (!v || !c || !c.width || !c.height) return;\n    if (!okSent) return;          // chưa có hình thật → canvas còn kích thước mặc định (300x150), đừng báo sai khung\n    var W0 = innerWidth, H0 = innerHeight, r = c.width / c.height, w = W0, h = w / r;\n    if (h > H0) { h = H0; w = h * r; }\n    var L = Math.round((W0 - w) / 2) + "px", T = Math.round((H0 - h) / 2) + "px", WW = Math.round(w) + "px", HH = Math.round(h) + "px";\n    if (v.style.width !== WW || v.style.height !== HH || v.style.left !== L || v.style.top !== T) {\n      var st = { position: "fixed", left: L, top: T, width: WW, height: HH, margin: "0", flex: "none" };\n      for (var k in st) v.style.setProperty(k, st[k], "important");\n    }\n    if (c.width !== lw || c.height !== lh) { lw = c.width; lh = c.height; post({ t: "size", w: lw, h: lh }); }\n  }\n  setInterval(layout, 300); addEventListener("resize", layout);\n\n  // Bộ nhớ copy (clipboard) giữa máy ảo và máy khách — dùng ô "clipboard" có sẵn của ws-scrcpy (đã ẩn)\n  function clipEls() {\n    var m = document.querySelector(".more-box"); if (!m) return null;\n    var ta = m.querySelector("textarea"), bs = m.querySelectorAll("button"), set = null, get = null;\n    for (var i = 0; i < bs.length; i++) {\n      var t = (bs[i].textContent || "").trim().toLowerCase();\n      if (t === "set clipboard") set = bs[i]; else if (t === "get clipboard") get = bs[i];\n    }\n    return ta && set ? { ta: ta, set: set, get: get } : null;\n  }\n  var lastClip = null, clipCap = false;\n  setInterval(function () {\n    var e = clipEls(); if (!e) return;\n    if (!clipCap) { clipCap = true; lastClip = e.ta.value; post({ t: "clipcap" }); return; }\n    var v = e.ta.value;\n    if (v !== lastClip) { lastClip = v; if (v) post({ t: "clip", text: v }); }   // máy ảo vừa copy chữ → gửi ra ngoài\n  }, 400);\n  function clipSet(text) {\n    var e = clipEls(); if (!e) return false;\n    lastClip = String(text); e.ta.value = lastClip; e.set.click(); return true;\n  }\n  function clipGet() {\n    var e = clipEls(); if (!e || !e.get) return false;\n    lastClip = ""; e.ta.value = ""; e.get.click(); return true;\n  }\n  window.__cp = { clipSet: clipSet, clipGet: clipGet, clipCap: function () { return !!clipEls(); } };\n  // Ctrl+V khi đang bấm vào màn hình máy: dán chữ của máy khách (có dấu) thay vì bộ nhớ copy cũ của máy ảo\n  addEventListener("keydown", function (e) {\n    if ((e.ctrlKey || e.metaKey) && (e.key === "v" || e.key === "V") && clipEls()) e.stopImmediatePropagation();\n  }, true);\n  document.addEventListener("paste", function (e) {\n    var t = e.clipboardData && e.clipboardData.getData("text/plain");\n    if (!t || !clipSet(t)) return;\n    e.preventDefault();\n    setTimeout(function () { post({ t: "pasteKey" }); }, 200);\n  }, true);\n\n  // Lệnh từ trang blueroblox: bấm phím qua đường hình (tức thì)\n  var TITLES = { back: "Back", home: "Home", recents: "Overview", volup: "Volume up", voldown: "Volume down", power: "Power" };\n  addEventListener("message", function (e) {\n    if (PARENTS.indexOf(e.origin) < 0 || !e.data || !e.data.cpcmd) return;\n    var d = e.data;\n    if (d.cpcmd === "key") {\n      var b = document.querySelector(\'button[title="\' + TITLES[d.key] + \'"]\');\n      if (!b) return post({ t: "nokey", key: d.key, id: d.id });\n      b.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));\n      setTimeout(function () { b.dispatchEvent(new MouseEvent("mouseup", { bubbles: true })); }, 50);\n      post({ t: "keyok", id: d.id });\n    } else if (d.cpcmd === "clipset") {\n      post({ t: "clipset", id: d.id, ok: clipSet(d.text || "") });\n    } else if (d.cpcmd === "clipget") {\n      post({ t: "clipget", id: d.id, ok: clipGet() });\n    }\n  });\n  // Bàn phím máy tính: tự bật "Capture keyboard" của ws-scrcpy (khách gõ phím thật, khỏi bấm bàn phím ảo)\n  //   + bấm vào màn hình máy là khung hình nhận phím ngay\n  var kbTry = 0, kbT = setInterval(function () {\n    var cb = document.querySelector(\'input[id^="input_capture_keyboard_"]\');\n    if (cb) { if (!cb.checked) cb.click(); if (cb.checked) { clearInterval(kbT); post({ t: "kb" }); } }\n    if (++kbTry > 120) clearInterval(kbT);\n  }, 500);\n  function grab() { try { window.focus(); if (document.activeElement && document.activeElement !== document.body && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) document.activeElement.blur(); } catch (e) {} }\n  addEventListener("mousedown", grab, true); addEventListener("touchstart", grab, true);\n  // ===== Thao tác nâng cao (giống LDPlayer / Multi): ĐỒNG BỘ nhiều máy · GHI MACRO · GÁN PHÍM CHƠI GAME =====\n  var REC = false, SYNC = false, KMEDIT = false, KM = null, lastMv = 0;\n  function layer() { return document.querySelector(".touch-layer"); }\n  function relp(cx, cy) { var c = layer(); if (!c) return null; var r = c.getBoundingClientRect(); if (!r.width || !r.height) return null; return { x: (cx - r.left) / r.width, y: (cy - r.top) / r.height }; }\n  function capture(e) {                     // thao tác THẬT của người dùng → báo trang ngoài (để đồng bộ / ghi macro / chọn vị trí phím)\n    if (!e.isTrusted || !(REC || SYNC || KMEDIT)) return;\n    var c = layer(); if (!c || e.target !== c) return;\n    var p, k;\n    if (e.type.indexOf("touch") === 0) { var t = e.changedTouches[0]; if (!t) return; p = relp(t.clientX, t.clientY); k = { touchstart: "down", touchmove: "move", touchend: "up", touchcancel: "up" }[e.type]; }\n    else { if (e.type === "mousemove" && !(e.buttons & 1)) return; p = relp(e.clientX, e.clientY); k = { mousedown: "down", mousemove: "move", mouseup: "up" }[e.type]; }\n    if (!p || !k) return;\n    if (KMEDIT) { if (k === "down") post({ t: "kmpick", x: p.x, y: p.y }); e.preventDefault(); e.stopImmediatePropagation(); return; }\n    if (k === "move") { var now = Date.now(); if (now - lastMv < 30) return; lastMv = now; }\n    post({ t: "in", k: k, x: p.x, y: p.y, id: 0 });\n  }\n  ["mousedown", "mousemove", "mouseup", "touchstart", "touchmove", "touchend", "touchcancel"].forEach(function (n) { addEventListener(n, capture, true); });\n  var TT = {};\n  function fire(k, x, y, id) {              // tạo thao tác chạm giả (nhiều ngón) đúng vị trí tương đối trên màn hình máy\n    var c = layer(); if (!c) return; var r = c.getBoundingClientRect(); var cx = r.left + x * r.width, cy = r.top + y * r.height; id = id || 0;\n    if (window.TouchEvent && window.Touch) {\n      try {\n        var t = new Touch({ identifier: id + 1, target: c, clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx, screenY: cy, radiusX: 2, radiusY: 2, force: 1 });\n        if (k === "up") delete TT[id]; else TT[id] = t;\n        var all = Object.keys(TT).map(function (i) { return TT[i]; });\n        c.dispatchEvent(new TouchEvent({ down: "touchstart", move: "touchmove", up: "touchend" }[k], { touches: all, targetTouches: all, changedTouches: [t], bubbles: true, cancelable: true }));\n        return;\n      } catch (er) {}\n    }\n    c.dispatchEvent(new MouseEvent({ down: "mousedown", move: "mousemove", up: "mouseup" }[k], { clientX: cx, clientY: cy, bubbles: true, cancelable: true, button: 0, buttons: k === "up" ? 0 : 1 }));\n  }\n  // Gán phím: KM = { "KeyJ": {x,y}, ..., __joy: {x,y,r, keys:{KeyW:[0,-1],...}} }\n  var held = {}, slot = {}, joyOn = false;\n  function joyUpdate() {\n    var j = KM && KM.__joy; if (!j) return; var dx = 0, dy = 0;\n    for (var kk in j.keys) if (held[kk]) { dx += j.keys[kk][0]; dy += j.keys[kk][1]; }\n    if (!dx && !dy) { if (joyOn) { fire("up", j.x, j.y, 9); joyOn = false; } return; }\n    var c = layer(), ar = 1; if (c) { var r = c.getBoundingClientRect(); ar = r.width / (r.height || 1); }\n    var l = Math.sqrt(dx * dx + dy * dy), tx = j.x + dx / l * j.r, ty = j.y + dy / l * j.r * ar;\n    if (!joyOn) { fire("down", j.x, j.y, 9); joyOn = true; }\n    fire("move", tx, ty, 9);\n  }\n  addEventListener("keydown", function (e) {\n    if (!KM) return; var j = KM.__joy, m = KM[e.code];\n    if (!(m || (j && j.keys[e.code]))) return;\n    e.preventDefault(); e.stopImmediatePropagation(); if (e.repeat) return;\n    if (j && j.keys[e.code]) { held[e.code] = true; joyUpdate(); return; }\n    var id = 1; while (Object.keys(slot).some(function (s) { return slot[s] === id; })) id++;\n    slot[e.code] = id; fire("down", m.x, m.y, id);\n  }, true);\n  addEventListener("keyup", function (e) {\n    if (!KM) return; var j = KM.__joy, m = KM[e.code];\n    if (!(m || (j && j.keys[e.code]))) return;\n    e.preventDefault(); e.stopImmediatePropagation();\n    if (j && j.keys[e.code]) { held[e.code] = false; joyUpdate(); return; }\n    if (slot[e.code]) { fire("up", m.x, m.y, slot[e.code]); delete slot[e.code]; }\n  }, true);\n  addEventListener("blur", function () { held = {}; joyUpdate(); for (var s in slot) { var m = KM && KM[s]; if (m) fire("up", m.x, m.y, slot[s]); } slot = {}; });\n  addEventListener("message", function (e) {\n    if (PARENTS.indexOf(e.origin) < 0 || !e.data || !e.data.cpcmd) return;\n    var d = e.data;\n    if (d.cpcmd === "in") fire(d.k, +d.x, +d.y, +d.id || 0);\n    else if (d.cpcmd === "sync") SYNC = !!d.on;\n    else if (d.cpcmd === "rec") REC = !!d.on;\n    else if (d.cpcmd === "kmedit") KMEDIT = !!d.on;\n    else if (d.cpcmd === "km") { KM = d.map && Object.keys(d.map).length ? d.map : null; held = {}; slot = {}; }\n  });\n  post({ t: "boot" });\n})();\n'.encode()
INJECT_CSS = 'html.cp-embed,html.cp-embed body{margin:0!important;padding:0!important;width:100%!important;height:100%!important;overflow:hidden!important;background:#000!important}html.cp-embed .control-buttons-list,html.cp-embed .more-box{display:none!important}html.cp-embed .device-view{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;background:#000!important}html.cp-embed .video{position:relative;flex:1 1 auto;min-width:0;min-height:0;width:100%;height:100%;background:#000}html.cp-embed .video-layer,html.cp-embed .touch-layer{position:absolute!important;left:0!important;top:0!important;width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;object-fit:contain!important}'
INJECT_TAG = ("<style>" + INJECT_CSS + "</style><script src=\"/__cp/inject.js?v=10\"></script>").encode()

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
        if self.path.startswith("/__cp/inject.js"):
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(INJECT_JS)))
            self.end_headers()
            return self.wfile.write(INJECT_JS)
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
            return self.send_json(200, {k: j[k] for k in ("state", "msg", "title", "file", "done", "total", "fails") if k in j})

        if parts and parts[0] == "admin":
            if not me["admin"]: return self.send_json(403, {"error": "Chỉ admin."})
            act = parts[1] if len(parts) > 1 else ""
            if act == "upload" and self.command == "POST":
                upid = q.get("id", ""); name = safe_name(q.get("name", ""))
                if not re.match(r"^[a-z0-9]{8,40}$", upid): return self.send_json(400, {"error": "Mã tải lên không hợp lệ."})
                if not name.lower().endswith(APK_EXT): return self.send_json(400, {"error": "Chỉ nhận file .apk / .apks / .apkm / .xapk"})
                total, off = int(q.get("total") or 0), int(q.get("off") or 0)
                if total <= 0 or total > MAX_FILE: return self.send_json(400, {"error": "File rỗng hoặc lớn hơn 4GB."})
                path = os.path.join(UP_DIR, f"admin_{upid}.part")
                have = os.path.getsize(path) if os.path.exists(path) else 0
                if off != have: return self.send_json(409, {"error": "Lệch vị trí", "size": have})
                data = self.body(MAX_CHUNK)
                if have + len(data) > total: return self.send_json(400, {"error": "Dữ liệu vượt kích thước file."})
                with open(path, "ab") as f: f.write(data)
                have += len(data)
                if have < total: return self.send_json(200, {"size": have})
                ADMIN_FILES[upid] = (path, name)
                return self.send_json(200, {"size": have, "file": upid})
            if act == "fromurl" and self.command == "POST":
                link = str(self.jbody().get("url") or "").strip()
                if not re.match(r"^https?://\S+$", link) or len(link) > 2000: return self.send_json(400, {"error": "Link phải bắt đầu bằng http:// hoặc https://"})
                jid = new_job(me["uid"], link[-60:])
                def work(j):
                    path, name = download(link, j)
                    if not name.lower().endswith(APK_EXT): os.remove(path); raise RuntimeError("Link không phải file APK.")
                    fid = secrets.token_hex(8); ADMIN_FILES[fid] = (path, name); JOBS[j]["file"] = fid; JOBS[j]["title"] = name
                    return "Đã tải " + name
                run_job(jid, work)
                return self.send_json(200, {"job": jid})
            if act == "install" and self.command == "POST":
                b = self.jbody(); fid = str(b.get("file") or "")
                ids = [i for i in (b.get("ids") or []) if UUID.match(str(i))][:500]
                every = bool(b.get("all"))
                if fid not in ADMIN_FILES: return self.send_json(400, {"error": "File đã hết hạn, tải lên lại."})
                if not every and not ids: return self.send_json(400, {"error": "Chưa chọn máy nào."})
                jid = new_job(me["uid"], ADMIN_FILES[fid][1])
                run_job(jid, lambda j: bulk_install(j, fid, ids, every, bool(b.get("keep"))))
                return self.send_json(200, {"job": jid})
            if act == "rootall" and self.command == "POST":       # admin: root lại toàn bộ máy chưa root
                jid = new_job(me["uid"], "Root tất cả máy")
                def work(j):
                    JOBS[j]["msg"] = "Đang root các máy chưa root (mỗi máy khởi động lại ~1 phút)…"
                    r = subprocess.run(["python3", os.path.join(HOME, "phonectl.py"), "rootfix", "--all", "--force"], capture_output=True, text=True, timeout=7200)
                    ls = [l for l in (r.stdout or "").splitlines() if l.startswith("ROOT_")]
                    ok = sum(l.startswith("ROOT_OK") for l in ls); bad = [l.split(" ", 1)[1] for l in ls if l.startswith(("ROOT_NO", "ROOT_WAIT"))]
                    return f"Đã root {ok} máy." + (" Chưa được: " + "; ".join(bad[:8]) if bad else "")
                run_job(jid, work)
                return self.send_json(200, {"job": jid})
            if act == "preset" and self.command == "GET":
                d = pre_dir(); fs = sorted(f for f in os.listdir(d) if f.lower().endswith(APK_EXT))
                return self.send_json(200, {"files": [{"name": f, "size": os.path.getsize(os.path.join(d, f))} for f in fs]})
            if act == "preset-del" and self.command == "POST":
                n = safe_name(self.jbody().get("name") or ""); fp = os.path.join(pre_dir(), n)
                if n.lower().endswith(APK_EXT) and os.path.isfile(fp): os.remove(fp); return self.send_json(200, {"ok": True})
                return self.send_json(404, {"error": "Không có file này."})
            return self.send_json(404, {"error": "Không có chức năng này."})

        if len(parts) != 2: return self.send_json(404, {"error": "Không có chức năng này."})
        under = bool(maint().get("maint")) and not me["admin"]
        if under and parts[1] != "info": return self.send_json(503, {"error": maint_text(), "maint": True})
        pid, act = parts
        p = phone_of(me, pid)
        if not p: return self.send_json(404, {"error": "Không tìm thấy máy của bạn."})
        port, cont = p.get("port"), p.get("container")
        locked = is_locked(p) and not me["admin"]
        if locked and act != "info": return self.send_json(423, {"error": lock_text(p), "locked": True})
        if act == "info":
            st = out(adb(port, ["get-state"], 15)) if port and p["status"] == "active" else ""
            booting = False
            if st == "device":                    # Android đang khởi động lại (VD vừa hết RAM) → báo khách
                try: booting = out(shell(port, "getprop sys.boot_completed", 8)).strip() != "1"
                except Exception: booting = False
            return self.send_json(200, {"name": p["name"], "ram": p["ram"], "cpu": p["cpu"], "res": p.get("res"),
                                        "status": p["status"], "expires_at": p.get("expires_at"), "online": st == "device", "booting": booting,
                                        "viewer": (VIEWERS.get(int(port)) or {}).get("viewer") if port else None,
                                        "fps": p.get("fps") or 30, "fps_range": fps_limits(), "busy": pid in _busy,
                                        "maint": under, "maint_msg": maint_text() if under else None,
                                        "locked": locked, "lock_msg": lock_text(p) if locked else None})
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

        if act == "gsf":          # mã thiết bị Google (GSF ID) → khách đăng ký ở google.com/android/uncertified để đăng nhập Google được
            r = shell(port, "content query --uri content://com.google.android.gsf.gservices --where \"name='android_id'\"", 20)
            m = re.search(r"value=(\d{6,25})", out(r))
            if not m: return self.send_json(409, {"error": "Chưa lấy được mã — mở Cửa hàng Play 1 lần, đợi 1 phút rồi thử lại."})
            return self.send_json(200, {"gsf": m.group(1)})

        if act == "gfix" and self.command == "POST":      # sau khi đăng ký mã: xoá dữ liệu Google Play để nhận chứng nhận mới
            r = shell(port, "pm clear com.android.vending; pm clear com.google.android.gms", 60)
            return self.send_json(200, {"ok": True, "out": out(r)[-200:]})

        if act == "store" and self.command == "GET":          # KHO ỨNG DỤNG: app có sẵn trên server (admin bỏ vào ~/apk-cai-san) + trạng thái đã cài
            d = pre_dir()
            try: fs = sorted(f for f in os.listdir(d) if f.lower().endswith(APK_EXT))
            except OSError: fs = []
            inst = set(l.split(":", 1)[1].strip() for l in out(shell(port, "pm list packages", 30)).splitlines() if l.startswith("package:"))
            try: sdk = int(out(shell(port, "getprop ro.build.version.sdk", 15)).strip() or 0)
            except ValueError: sdk = 0
            fs = [f for f in fs if not sdk or min_sdk(os.path.join(d, f)) <= sdk]   # ẩn app không chạy được trên bản Android của máy này
            return self.send_json(200, {"files": [{"name": f, "size": os.path.getsize(os.path.join(d, f))} for f in fs], "installed": sorted(inst)})
        if act == "store" and self.command == "POST":          # cài 1 app trong kho vào máy này
            n = safe_name(str(self.jbody().get("name") or "")); fp = os.path.join(pre_dir(), n)
            if not n.lower().endswith(APK_EXT) or not os.path.isfile(fp): return self.send_json(404, {"error": "Không có app này trong kho."})
            jid = new_job(me["uid"], "Cài " + n)
            def work(j):
                JOBS[j]["msg"] = "Đang cài " + n + "…"
                files, dst = stage_pkg(fp, n)
                try:
                    args = (["install", "-r", "-g", files[0]] if len(files) == 1 else ["install-multiple", "-r", "-g"] + files)
                    o = out(adb(port, args, timeout=900))
                finally:
                    subprocess.run(["docker", "exec", WSNAME, "rm", "-rf", dst], capture_output=True, timeout=60)
                if "OLDER_SDK" in o: raise RuntimeError("App này cần bản Android mới hơn máy của bạn — hãy chọn máy Android 10 trở lên, hoặc cài bản cũ hơn từ CH Play.")
                if "Success" not in o: raise RuntimeError("Cài lỗi: " + o[-200:])
                return "✅ Đã cài " + n
            run_job(jid, work)
            return self.send_json(200, {"job": jid})
        if act == "play" and self.command == "POST":           # mở trang app trên Google Play trong máy (Chrome, Firefox…)
            pkg = str(self.jbody().get("pkg") or "")
            if not re.match(r"^[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+$", pkg): return self.send_json(400, {"error": "Tên app không hợp lệ."})
            r = shell(port, "am start -a android.intent.action.VIEW -d " + shlex.quote("market://details?id=" + pkg), 20)
            return self.send_json(200, {"ok": True})

        if act == "gps" and self.command == "POST":           # ĐỔI VỊ TRÍ GPS (vị trí giả) — Android 12+ (lệnh "cmd location providers" của Android)
            b = self.jbody()
            if b.get("off"):
                shell(port, "for P in gps network fused; do cmd location providers remove-test-provider $P >/dev/null 2>&1; done; true", 30)
                return self.send_json(200, {"ok": True, "msg": "Đã tắt vị trí giả."})
            try: lat, lng = float(b.get("lat")), float(b.get("lng"))
            except (TypeError, ValueError): return self.send_json(400, {"error": "Toạ độ không hợp lệ."})
            if not (-90 <= lat <= 90 and -180 <= lng <= 180): return self.send_json(400, {"error": "Toạ độ không hợp lệ."})
            try: sdk = int(out(shell(port, "getprop ro.build.version.sdk", 15)).strip() or 0)
            except ValueError: sdk = 0
            if sdk < 31: return self.send_json(400, {"error": "Đổi vị trí GPS cần máy Android 12 trở lên."})
            loc = f"{lat:.6f},{lng:.6f}"
            o = out(shell(port, "appops set com.android.shell android:mock_location allow; cmd location set-location-enabled true >/dev/null 2>&1; "
                                "for P in gps network fused; do cmd location providers add-test-provider $P >/dev/null 2>&1; "
                                "cmd location providers set-test-provider-enabled $P true >/dev/null 2>&1; "
                                f"cmd location providers set-test-provider-location $P --location {loc} --accuracy 3 2>&1; done; echo DONE", 40))
            if "DONE" not in o or "Exception" in o: return self.send_json(500, {"error": "Chưa đặt được vị trí: " + o[-160:]})
            return self.send_json(200, {"ok": True, "msg": f"Đã đặt vị trí {loc}. Mở lại app cần vị trí để nhận vị trí mới."})

        if act == "clone":                                     # CLONE TAB: chạy thêm 1 bản app thứ 2 (VD Roblox chính thức) trong "hồ sơ công việc" của Android
            CU = "pm list users | sed -n 's/.*UserInfo{\\([0-9]*\\):CloneTab:.*/\\1/p' | head -1"
            if self.command == "GET":
                u = out(shell(port, CU, 20)).strip()
                cl = sorted(l.split(":", 1)[1].strip() for l in out(shell(port, f"pm list packages -3 --user {u}", 30)).splitlines() if l.startswith("package:")) if u else []
                return self.send_json(200, {"profile": u or None, "cloned": cl})
            b = self.jbody(); pkg = str(b.get("pkg") or "")
            if not PKG.match(pkg): return self.send_json(400, {"error": "Tên ứng dụng không hợp lệ."})
            if b.get("remove"):
                u = out(shell(port, CU, 20)).strip()
                if u: shell(port, f"pm uninstall --user {u} {shlex.quote(pkg)}", 60)
                return self.send_json(200, {"ok": True, "msg": "Đã bỏ bản clone."})
            jid = new_job(me["uid"], "Clone " + pkg)
            def work(j):
                JOBS[j]["msg"] = "Đang tạo bản clone (lần đầu ~20 giây)…"
                q = shlex.quote(pkg)
                o = out(shell(port, f"pm path {q} >/dev/null 2>&1 || echo NOPKG; U=$({CU}); "
                                    f"[ -n \"$U\" ] || U=$(pm create-user --profileOf 0 --managed CloneTab 2>&1 | sed -n 's/.*created user id \\([0-9]*\\).*/\\1/p'); "
                                    f"[ -n \"$U\" ] || {{ echo NOUSER; exit 0; }}; am start-user $U >/dev/null 2>&1; "
                                    f"pm install-existing --user $U {q} >/dev/null 2>&1; settings put global package_verifier_enable 0; "
                                    f"pm list packages --user $U | grep -qx package:{q} && echo CLONED$U", 120))
                if "NOPKG" in o: raise RuntimeError("Máy chưa cài app này — cài bản chính trước rồi mới clone.")
                if "NOUSER" in o: raise RuntimeError("Android trong máy không cho tạo hồ sơ clone.")
                if "CLONED" not in o: raise RuntimeError("Chưa clone được: " + o[-150:])
                return "✅ Đã clone! Mở danh sách ứng dụng → tab Công việc (Work) → app có biểu tượng cặp tài liệu nhỏ là bản thứ 2."
            run_job(jid, work)
            return self.send_json(200, {"job": jid})

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
            if fps > 60 and float(p.get("cpu") or 0) < 4:      # máy nhỏ chạy FPS cao → quá tải, giật, xám màn hình
                return self.send_json(400, {"error": "Máy dưới 4 nhân chỉ chọn tối đa 60 FPS (treo game nên để 30 FPS cho mượt)."})
            if not m or not all(320 <= int(v) <= 4096 for v in m.groups()): return self.send_json(400, {"error": "Độ phân giải phải dạng rộng x cao, VD 1080x1920."})
            if pid in _busy: return self.send_json(409, {"error": "Máy đang được đổi cấu hình, đợi chút."})
            jid = new_job(me["uid"], f"{res} · {fps} FPS")
            same_fps = fps == int(p.get("fps") or 30)
            def work(j):
                _busy.add(pid)
                try:
                    if same_fps:                      # chỉ đổi kích thước → đổi ngay, app đang mở KHÔNG bị tắt
                        JOBS[j]["msg"] = "Đang đổi kích thước màn hình…"
                        r = subprocess.run(["python3", os.path.join(HOME, "phonectl.py"), "liveres", cont, "--res", res],
                                           capture_output=True, text=True, timeout=120)
                        if r.returncode == 0:
                            sb_patch(f"/rest/v1/cloud_phones?id=eq.{pid}", {"res": res})
                            return f"Đã đổi sang {res} — app đang chạy vẫn giữ nguyên."
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

        if act == "root" and self.command == "POST":        # khách bấm "Root máy": kiểm tra + bật root (Magisk) + cấp sẵn quyền root cho app
            if not cont: return self.send_json(409, {"error": "Không xác định được máy."})
            jid = new_job(me["uid"], "Root máy")
            def work(j):
                JOBS[j]["msg"] = "Đang kiểm tra / bật root (máy có thể khởi động lại ~1 phút)…"
                r = subprocess.run(["python3", os.path.join(HOME, "phonectl.py"), "rootfix", cont, "--force"], capture_output=True, text=True, timeout=600)
                line = next((l for l in (r.stdout or "").splitlines() if l.startswith("ROOT_")), "")
                msg = line.split(":", 1)[1].strip() if ":" in line else "Không kiểm tra được, thử lại sau."
                if line.startswith("ROOT_OK"): return "✅ Máy đã ROOT (Magisk). Mở tool là dùng được — nếu hiện bảng Magisk thì bấm Cho phép (Grant)."
                if line.startswith("ROOT_WAIT"): return "⏳ Máy đang bật root, chờ 1 phút rồi bấm lại."
                raise RuntimeError("Chưa root được: " + msg)
            run_job(jid, work)
            return self.send_json(200, {"job": jid})

        if act == "reboot" and self.command == "POST":
            if not cont: return self.send_json(409, {"error": "Không xác định được máy."})
            jid = new_job(me["uid"], "Khởi động lại")
            def work(j):
                r = subprocess.run(["python3", os.path.join(HOME, "phonectl.py"), "restart", cont], capture_output=True, timeout=120)
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
            if origin and origin not in SELF_ORIGINS: return self.deny(403, "Nguồn không được phép.")
            if not auth: return self.deny(401, "Hãy mở máy từ trang blueroblox.shop.")
            if not auth.get("a"):
                if maint().get("maint"): return self.deny(503, "Đang bảo trì.")
                if q.get("action") != "proxy-adb" or q.get("remote") != "tcp:8886": return self.deny(403, "Không được phép.")
                m = re.match(r"^127\.0\.0\.1:(\d{4,5})$", q.get("udid", ""))
                if not m or auth.get("u") not in port_owners(int(m.group(1))): return self.deny(403, "Đây không phải máy của bạn.")
            m2 = re.match(r"^127\.0\.0\.1:(\d{4,5})$", q.get("udid", ""))
            viewer = re.sub(r"[^\w-]", "", q.get("cpv", ""))[:40]
            key = (int(m2.group(1)) if m2 and q.get("action") == "proxy-adb" else None)
            return self.pipe_ws(key if key and viewer and not auth.get("a") else None, viewer)
        if self.command not in ("GET", "HEAD") and not (auth and auth.get("a")):
            return self.deny(403, "Không được phép.")
        return self.pipe_http()

    def pipe_http(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else None
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"cookie", "host", "content-length", "accept-encoding", "if-none-match", "if-modified-since"}}
        hdr["Host"] = "%s:%d" % UPSTREAM
        c = http.client.HTTPConnection(*UPSTREAM, timeout=60)
        c.request(self.command, self.path, body=data, headers=hdr)
        r = c.getresponse()
        body = r.read()
        c.close()
        self.send_response(r.status, r.reason)
        html = "text/html" in (r.getheader("Content-Type") or "")
        for k, v in r.getheaders():
            if k.lower() not in HOP | {"content-length"} and not (html and k.lower() in ("cache-control", "etag", "last-modified")): self.send_header(k, v)
        if html:
            self.send_header("Content-Security-Policy", "frame-ancestors " + " ".join(sorted(ORIGINS | SELF_ORIGINS)))
            self.send_header("Cache-Control", "no-store")
            if r.status == 200:
                m = re.search(rb"<head[^>]*>", body, re.I)
                body = (body[:m.end()] + INJECT_TAG + body[m.end():]) if m else (INJECT_TAG + body)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def pipe_ws(self, port=None, viewer=""):
        up = socket.create_connection(UPSTREAM, timeout=10)
        me = {"viewer": viewer, "close": None}
        if port:                                  # mỗi máy chỉ 1 nơi xem (khách): nơi mở sau thay nơi cũ → không giành luồng hình
            with VLOCK:
                old = VIEWERS.get(port)
                VIEWERS[port] = me
            if old and old["viewer"] != viewer and old["close"]:
                try: old["close"]()
                except Exception: pass
        for s_ in (up, self.connection):          # gửi ngay từng gói nhỏ (chạm, phím) — không chờ gom
            try: s_.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError: pass
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for k, v in self.headers.items():
            if k.lower() == "cookie": continue
            lines.append(f"{k}: {'%s:%d' % UPSTREAM if k.lower() == 'host' else v}")
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        up.settimeout(None)
        cli = self.connection
        cli.settimeout(None)
        self.close_connection = True

        def kick():
            for s_ in (up, cli):
                try: s_.shutdown(socket.SHUT_RDWR)
                except Exception: pass
        me["close"] = kick

        def client_to_up():
            try:
                while True:
                    d = self.rfile.read1(262144)
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
                d = up.recv(262144)
                if not d: break
                cli.sendall(d)
        except Exception:
            pass
        finally:
            try: up.close()
            except Exception: pass
            try: cli.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            if port:
                with VLOCK:
                    if VIEWERS.get(port) is me: VIEWERS.pop(port, None)
        t.join(timeout=3)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    for f in os.listdir(UP_DIR):          # dọn file tải dở từ lần chạy trước
        try: os.remove(os.path.join(UP_DIR, f))
        except OSError: pass
    log(f"cpapi chạy ở {LISTEN[0]}:{LISTEN[1]} → bảo vệ ws-scrcpy {UPSTREAM[1]}, trang chủ {SELF_ORIGIN}" + (f", đường thẳng {FAST_ORIGIN}" if FAST_ORIGIN else ""))
    Server(LISTEN, H).serve_forever()
