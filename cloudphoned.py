#!/usr/bin/env python3
# cloudphoned.py — Chương trình nền: đọc đơn từ Supabase (cloud_phones) → gọi phonectl tạo/xoá/gia hạn máy.
# Cấu hình đọc từ ~/cloudphone.conf (KEY=VALUE), KHÔNG đưa khoá bí mật lên GitHub.
#   SUPABASE_URL=https://xxxx.supabase.co
#   SUPABASE_SERVICE_KEY=eyJ...            (service_role key — bí mật)
#   PUBLIC_BASE=https://xxxx.trycloudflare.com   (link ws-scrcpy ra internet)
# Chạy:  python3 ~/cloudphoned.py           (vòng lặp mỗi 15s)
import json, os, sys, time, subprocess, urllib.request, urllib.parse, datetime

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
    return sb("GET", "cloud_phones?pending_renew_days=gt.0&status=eq.active&select=*") or []

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
    return f"{BASE}/#!action=stream&udid=127.0.0.1:{port}&player=webcodecs"

def cname(row):
    return "cp_" + row["id"].replace("-", "")[:10]

def do_create(row):
    name = cname(row)
    print(f"[tạo] đơn {row['id'][:8]} → {name} ({row['ram']}GB/{row['cpu']} {row['days']}n)")
    r = phonectl(["create", name, "--ram", str(row["ram"]), "--cpu", str(row["cpu"]),
                  "--days", str(row["days"]), "--res", row.get("res") or "720x1280"])
    if r.returncode == 2:   # HẾT HÀNG (quá 85%)
        patch(row["id"], {"status": "error", "error": "HẾT HÀNG — máy chủ đã đầy (85%)."}); return
    if r.returncode != 0:
        patch(row["id"], {"status": "error", "error": (r.stdout + r.stderr)[:400]}); return
    ps = phone_state(name)
    if not ps:
        patch(row["id"], {"status": "error", "error": "Tạo xong nhưng không đọc được trạng thái."}); return
    patch(row["id"], {"status": "active", "container": name, "port": ps["port"],
                      "node": "home", "stream_url": stream_url(ps["port"]),
                      "expires_at": ps["expires"], "error": None})
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
    if ps: fields["expires_at"] = ps["expires"]
    patch(row["id"], fields)

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

if __name__ == "__main__":
    print(f"cloudphoned chạy — đọc {SB} mỗi {POLL}s. Ctrl+C để dừng.")
    while True:
        try: tick()
        except Exception as e: print("lỗi vòng lặp:", e)
        time.sleep(POLL)
