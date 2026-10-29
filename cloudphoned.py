#!/usr/bin/env python3
# cloudphoned.py — Chương trình nền: đọc đơn từ Supabase (cloud_phones) → gọi phonectl tạo/xoá/gia hạn máy.
# Cấu hình đọc từ ~/cloudphone.conf (KEY=VALUE), KHÔNG đưa khoá bí mật lên GitHub.
#   SUPABASE_URL=https://xxxx.supabase.co
#   SUPABASE_SERVICE_KEY=eyJ...            (service_role key — bí mật)
#   PUBLIC_BASE=https://xxxx.trycloudflare.com   (link ws-scrcpy ra internet)
# Chạy:  python3 ~/cloudphoned.py           (vòng lặp mỗi 15s)
import json, os, sys, time, subprocess, urllib.request, urllib.parse, datetime, socket, shutil, threading
GRACE_MIN = 30          # hết hạn quá 30 phút không gia hạn → xoá máy + dữ liệu

HOME = os.path.expanduser("~")
CONF = os.path.join(HOME, "cloudphone.conf")
PHONES_JSON = os.path.join(HOME, "redroid", "phones.json")
PHONECTL = os.path.join(HOME, "phonectl.py")
POLL = 15

def load_conf():
    c = {}
    try:
        for line in open(CONF):
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
BASE = CFG["PUBLIC_BASE"].rstrip("/")
H = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
NODE_ID = CFG.get("NODE_ID") or socket.gethostname()           # mỗi server 1 tên riêng
NOTIFY_URL = CFG.get("NOTIFY_URL", "https://blueroblox.shop/api/notify-admin")
LIMIT_PCT = 85
DATA_ROOT = os.path.join(HOME, "redroid")

def sb(method, path, body=None, extra=None):
    url = SB + "/rest/v1/" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**H, **(extra or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

def get_pending(status):
    return sb("GET", f"cloud_phones?status=eq.{status}&select=*&order=created_at.asc") or []

def get_renew():
    return sb("GET", "cloud_phones?pending_renew_days=gt.0&status=in.(active,expired)&select=*") or []

def get_live():
    return sb("GET", "cloud_phones?status=in.(active,expired)&select=*") or []

def patch(pid, fields):
    fields["updated_at"] = datetime.datetime.now().isoformat()
    sb("PATCH", f"cloud_phones?id=eq.{pid}", fields, {"Prefer": "return=minimal"})

def phonectl(args):
    return subprocess.run(["python3", PHONECTL] + args, capture_output=True, text=True)

def phone_state(name):
    try:
        st = json.load(open(PHONES_JSON))
        return next((p for p in st.get("phones", []) if p["name"] == name), None)
    except Exception:
        return None

def stream_url(port):
    # Phải có tham số ws= thì trang điều khiển mới hiện hình (thiếu → màn đen)
    q = urllib.parse.quote
    udid = f"127.0.0.1:{port}"
    ws = BASE.replace("https://", "wss://").replace("http://", "ws://") + "/?action=proxy-adb&remote=" + q("tcp:8886", safe="") + "&udid=" + q(udid, safe="")
    return f"{BASE}/#!action=stream&udid={q(udid, safe='')}&player=webcodecs&ws={q(ws, safe='')}"

def now(): return datetime.datetime.now().astimezone()
def parse_dt(s):
    d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()

def cname(row):
    return "cp_" + row["id"].replace("-", "")[:10]

BOOTING = set()
def do_create(row):
    """Tạo container ngay (vài giây), rồi chờ Android khởi động ở luồng riêng → nhiều máy khởi động song song."""
    name = cname(row)
    ps = phone_state(name)
    if not ps:
        print(f"[tạo] đơn {row['id'][:8]} → {name} ({row['ram']}GB/{row['cpu']} {row['days']}n {row.get('fps') or 30}fps)")
        r = phonectl(["create", name, "--ram", str(row["ram"]), "--cpu", str(row["cpu"]),
                      "--days", str(row["days"]), "--res", row.get("res") or "720x1280",
                      "--fps", str(row.get("fps") or 30), "--nowait"])
        if r.returncode == 2:   # HẾT HÀNG (quá 85%)
            refund(row, "Máy chủ đã đầy, chưa tạo được máy"); notify(f"⚠️ <b>Đơn ĐT đám mây bị hoàn tiền</b> vì {NODE_ID} đã đầy 85%."); return
        if r.returncode != 0:
            log_err = (r.stdout + r.stderr)[-300:]
            print("   ✗", log_err)
            refund(row, "Tạo máy lỗi, vui lòng thử lại"); notify(f"⚠️ <b>Tạo máy lỗi</b> trên {NODE_ID} (đã hoàn tiền khách):\n<code>{log_err[-200:]}</code>"); return
        ps = phone_state(name)
        if not ps:
            refund(row, "Tạo máy lỗi, vui lòng thử lại"); return
        patch(row["id"], {"container": name, "port": ps["port"], "node": NODE_ID})
    if name in BOOTING: return
    BOOTING.add(name)
    threading.Thread(target=finish_boot, args=(row["id"], name), daemon=True).start()

def finish_boot(pid, name):
    try:
        r = phonectl(["waitboot", name])      # tối đa ~4 phút; rớt thì connect-all sẽ nối lại sau (gồm cài app sẵn + mở quyền APK)
        for ln in (r.stdout or "").splitlines():
            if ln.strip().startswith(("+", "!")): print("  ", name, ln.strip())
        ps = phone_state(name)
        if not ps: return
        patch(pid, {"status": "active", "container": name, "port": ps["port"], "node": NODE_ID,
                    "stream_url": stream_url(ps["port"]), "expires_at": parse_dt(ps["expires"]).isoformat(), "error": None})
        print(f"   ✓ {name} sẵn sàng, cổng {ps['port']}")
    except Exception as e:
        print("lỗi chờ khởi động:", e)
    finally:
        BOOTING.discard(name)

def refund(row, msg):
    """Tạo máy lỗi → hoàn lại tiền khách đã trả (hàm SQL cloudphone_refund)."""
    try:
        sb("POST", "rpc/cloudphone_refund", {"p_id": row["id"], "p_err": msg})
    except Exception as e:
        print("   lỗi hoàn tiền:", e)
        patch(row["id"], {"status": "error", "error": msg + " — liên hệ admin để hoàn tiền."})

def notify(text):
    try:
        req = urllib.request.Request(NOTIFY_URL, data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "cloudphoned"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("   không gửi được Telegram:", e)

# ---------------- Bảo trì ----------------
_maint = {"t": 0, "on": False, "off_at": None}
def maint_state():
    """Đang bảo trì (ĐT đám mây hoặc toàn web)? + lúc tắt bảo trì gần nhất."""
    if time.time() - _maint["t"] < 30: return _maint
    try:
        pr = (sb("GET", "cloudphone_pricing?id=eq.1&select=maintenance,maint_off_at") or [{}])[0]
        site = ((sb("GET", "settings?id=eq.1&select=data") or [{}])[0].get("data") or {})
        offs = [parse_dt(x) for x in (pr.get("maint_off_at"), site.get("maint_off_at")) if x]
        _maint.update(t=time.time(), on=bool(pr.get("maintenance")) or bool(site.get("maint")), off_at=max(offs) if offs else None)
    except Exception as e:
        print("lỗi đọc bảo trì:", e)
    return _maint

# ---------------- Báo cáo tài nguyên máy chủ ----------------
_cpu_prev = None
def cpu_pct():
    global _cpu_prev
    with open("/proc/stat") as f: v = [int(x) for x in f.readline().split()[1:]]
    idle, tot = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
    p = None
    if _cpu_prev and tot > _cpu_prev[0]:
        p = round(100 * (1 - (idle - _cpu_prev[1]) / (tot - _cpu_prev[0])), 1)
    _cpu_prev = (tot, idle)
    return p

def meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1); m[k] = int(v.split()[0])
    return m.get("MemTotal", 0) / 1048576, m.get("MemAvailable", 0) / 1048576

def gpus():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        out = []
        for line in r.stdout.strip().splitlines():
            n, u, mu, mt, tp = [x.strip() for x in line.split(",")]
            num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None
            out.append({"name": n, "util": num(u), "mem_used": num(mu), "mem_total": num(mt), "temp": num(tp)})
        return out
    except Exception:
        return []

def disk(path):
    try:
        d = shutil.disk_usage(path); return {"used": round(d.used / 1073741824, 1), "total": round(d.total / 1073741824, 1)}
    except Exception:
        return None

def cpu_temp():
    best = None
    for p in ("/sys/class/hwmon",):
        try:
            for h in os.listdir(p):
                for f in os.listdir(os.path.join(p, h)):
                    if f.startswith("temp") and f.endswith("_input"):
                        v = int(open(os.path.join(p, h, f)).read()) / 1000
                        if 0 < v < 130: best = max(best or 0, v)
        except Exception:
            pass
    return best

_alert = {"full": None}
def heartbeat():
    try:
        st = json.load(open(PHONES_JSON)).get("phones", [])
    except Exception:
        st = []
    ram_t, ram_avail = meminfo(); cpu_t = os.cpu_count() or 1
    a_ram = sum(float(p.get("ram", 0)) for p in st); a_cpu = sum(float(p.get("cpu", 0)) for p in st)
    c_ram, c_cpu = ram_t * LIMIT_PCT / 100, cpu_t * LIMIT_PCT / 100
    try:
        running = len([l for l in subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=15).stdout.split() if l.startswith("cp_")])
    except Exception:
        running = None
    try: up_h = round(float(open("/proc/uptime").read().split()[0]) / 3600, 1)
    except Exception: up_h = None
    metrics = {"cpu_pct": cpu_pct(), "load": [round(x, 2) for x in os.getloadavg()], "cpu_temp": cpu_temp(),
               "ram_used": round(ram_t - ram_avail, 1), "gpus": gpus(), "disk_data": disk(DATA_ROOT), "disk_root": disk("/"),
               "running": running, "uptime_h": up_h}
    body = {"id": NODE_ID, "cpu_total": cpu_t, "ram_total": round(ram_t, 1), "cap_cpu": round(c_cpu, 1), "cap_ram": round(c_ram, 1),
            "alloc_cpu": a_cpu, "alloc_ram": a_ram, "phones": len(st), "limit_pct": LIMIT_PCT, "metrics": metrics,
            "updated_at": now().isoformat()}
    sb("POST", "cloud_nodes", body, {"Prefer": "resolution=merge-duplicates,return=minimal"})
    # Báo Telegram 1 lần khi chạm ngưỡng 85% (và khi có chỗ trở lại)
    full = a_ram >= c_ram - 1 or a_cpu >= c_cpu - 1
    if _alert["full"] is not None and full != _alert["full"]:
        notify(f"🚨 <b>{NODE_ID} đã đạt {LIMIT_PCT}%</b> — RAM cấp {a_ram:.0f}/{ram_t:.0f}GB, CPU cấp {a_cpu:.0f}/{cpu_t} luồng. Khách sẽ thấy HẾT HÀNG."
               if full else f"✅ <b>{NODE_ID}</b> đã có chỗ trống trở lại (RAM cấp {a_ram:.0f}/{ram_t:.0f}GB).")
    _alert["full"] = full

def running(name):
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
    return r.stdout.strip() == "true"

def do_locks():
    """Máy admin khoá → tắt; hết hạn khoá → tự mở khoá; máy đang chạy mà bị tắt ngoài ý muốn → bật lại."""
    rows = sb("GET", "cloud_phones?status=eq.active&select=id,container,locked,locked_until") or []
    t = now()
    for row in rows:
        name = row.get("container") or cname(row)
        if not phone_state(name): continue
        lk = bool(row.get("locked"))
        if lk and row.get("locked_until") and parse_dt(row["locked_until"]) <= t:
            patch(row["id"], {"locked": False, "locked_until": None, "lock_reason": None}); lk = False
            print(f"[mở khoá] {name} (hết thời hạn khoá)")
        on = running(name)
        if lk and on:
            print(f"[khoá] tắt {name}"); phonectl(["stop", name])
        elif not lk and not on:
            print(f"[bật lại] {name}")
            subprocess.run(["docker", "start", name], capture_output=True, text=True)

def do_cancel(row):
    name = row.get("container") or cname(row)
    print(f"[huỷ] {name}")
    phonectl(["delete", name])
    patch(row["id"], {"status": "cancelled"})

def do_renew(row):
    name = row.get("container") or cname(row)
    d = int(row["pending_renew_days"])
    print(f"[gia hạn] {name} +{d}n")
    r = phonectl(["renew", name, "--days", str(d)])
    ps = phone_state(name)
    fields = {"pending_renew_days": 0}
    if ps:
        fields["expires_at"] = parse_dt(ps["expires"]).isoformat()
        fields["status"] = "active"; fields["error"] = None
        fields["stream_url"] = stream_url(ps["port"])
    patch(row["id"], fields)

def do_expiry(row):
    """Hết hạn → tạm dừng (giữ dữ liệu 30 phút). Quá 30 phút → xoá máy + toàn bộ dữ liệu."""
    name = row.get("container") or cname(row)
    ps = phone_state(name)
    exp = parse_dt(ps["expires"]) if ps else (parse_dt(row["expires_at"]) if row.get("expires_at") else None)
    if not exp: return
    t = now()
    m = maint_state()
    if m["on"]: return                       # đang bảo trì: không tạm dừng / không xoá máy khách
    start = max(exp, m["off_at"]) if m["off_at"] else exp   # hết hạn lúc bảo trì → 30 phút tính từ lúc tắt bảo trì
    if t > start + datetime.timedelta(minutes=GRACE_MIN):
        print(f"[xoá hết hạn] {name}")
        if ps: phonectl(["delete", name])
        patch(row["id"], {"status": "cancelled", "error": f"Hết hạn quá {GRACE_MIN} phút không gia hạn — máy và dữ liệu đã bị xoá."})
    elif t > exp and row["status"] == "active":
        print(f"[tạm dừng] {name} hết hạn, chờ gia hạn {GRACE_MIN} phút")
        if ps: phonectl(["stop", name])
        patch(row["id"], {"status": "expired"})

def tick():
    for row in get_pending("pending"):
        try: do_create(row)
        except Exception as e: print("lỗi tạo:", e)
    for row in get_pending("cancelling"):
        try: do_cancel(row)
        except Exception as e: print("lỗi huỷ:", e)
    for row in get_renew():
        try: do_renew(row)
        except Exception as e: print("lỗi gia hạn:", e)
    for row in get_live():
        try: do_expiry(row)
        except Exception as e: print("lỗi hết hạn:", e)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(line_buffering=True)   # in log ngay để xem bằng journalctl
    except Exception: pass
    print(f"cloudphoned chạy — đọc {SB} mỗi {POLL}s. Ctrl+C để dừng.")
    try:                                    # nới RAM cho máy đang chạy (chống văng app khi hết RAM)
        r = phonectl(["memfix"]); print(r.stdout.strip())
    except Exception as e: print("lỗi memfix:", e)
    n = 0
    while True:
        try: tick()
        except Exception as e: print("lỗi vòng lặp:", e)
        try: phonectl(["connect-all"])      # mỗi 15 giây: máy nào rớt kết nối thì nối lại (tránh màn xám)
        except Exception as e: print("lỗi connect-all:", e)
        if n % 2 == 0:                      # ~30 giây: báo cáo CPU/RAM/card/ổ cứng lên trang admin
            try: heartbeat()
            except Exception as e: print("lỗi báo cáo tài nguyên:", e)
            try: do_locks()
            except Exception as e: print("lỗi khoá máy:", e)
        if n % 20 == 5:                     # ~5 phút: cài app còn thiếu (máy nào lỡ) + mở quyền cài APK cho app khách mới cài
            try:
                r = phonectl(["preinstall", "--all"])
                for ln in (r.stdout or "").splitlines():
                    if ln.strip(): print("  ", ln.strip())
                phonectl(["perms"])
            except Exception as e: print("lỗi cài app sẵn / mở quyền APK:", e)
        n += 1
        time.sleep(POLL)
