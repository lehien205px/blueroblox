#!/usr/bin/env python3
# cloudphoned.py — Chương trình nền: đọc đơn từ Supabase (cloud_phones) → gọi phonectl tạo/xoá/gia hạn máy.
# Cấu hình đọc từ ~/cloudphone.conf (KEY=VALUE), KHÔNG đưa khoá bí mật lên GitHub.
#   SUPABASE_URL=https://xxxx.supabase.co
#   SUPABASE_SERVICE_KEY=eyJ...            (service_role key — bí mật)
#   PUBLIC_BASE=https://xxxx.trycloudflare.com   (link ws-scrcpy ra internet)
# Chạy:  python3 ~/cloudphoned.py           (vòng lặp mỗi 15s)
import json, os, sys, time, subprocess, urllib.request, urllib.parse, datetime
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

def do_create(row):
    name = cname(row)
    print(f"[tạo] đơn {row['id'][:8]} → {name} ({row['ram']}GB/{row['cpu']} {row['days']}n)")
    r = phonectl(["create", name, "--ram", str(row["ram"]), "--cpu", str(row["cpu"]),
                  "--days", str(row["days"]), "--res", row.get("res") or "720x1280",
                  "--fps", str(row.get("fps") or 30)])
    if r.returncode == 2:   # HẾT HÀNG (quá 85%)
        patch(row["id"], {"status": "error", "error": "HẾT HÀNG — máy chủ đã đầy (85%)."}); return
    if r.returncode != 0:
        patch(row["id"], {"status": "error", "error": (r.stdout + r.stderr)[:400]}); return
    ps = phone_state(name)
    if not ps:
        patch(row["id"], {"status": "error", "error": "Tạo xong nhưng không đọc được trạng thái."}); return
    patch(row["id"], {"status": "active", "container": name, "port": ps["port"],
                      "node": "home", "stream_url": stream_url(ps["port"]),
                      "expires_at": parse_dt(ps["expires"]).isoformat(), "error": None})
    print(f"   ✓ active, cổng {ps['port']}")

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
    if t > exp + datetime.timedelta(minutes=GRACE_MIN):
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
    print(f"cloudphoned chạy — đọc {SB} mỗi {POLL}s. Ctrl+C để dừng.")
    n = 0
    while True:
        try: tick()
        except Exception as e: print("lỗi vòng lặp:", e)
        try: phonectl(["connect-all"])      # mỗi 15 giây: máy nào rớt kết nối thì nối lại (tránh màn xám)
        except Exception as e: print("lỗi connect-all:", e)
        n += 1
        time.sleep(POLL)
