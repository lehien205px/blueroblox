# -*- coding: utf-8 -*-
# =====================================================================
#  remake.py — LÀM MỚI toàn bộ máy khách đang thuê trên máy Windows (MEmu), sau khi đã tắt server Ubuntu.
#   • Giữ: chủ máy, tên, RAM/CPU, độ phân giải, HẠN SỬ DỤNG.   • Máy mới tinh (khách cài lại game, đăng nhập lại).
#   python run.py remake.py list        ← xem những máy sẽ làm mới
#   python run.py remake.py all         ← làm mới tất cả (từng máy một)
#   python run.py remake.py one TÊN     ← làm mới 1 máy
# =====================================================================
import datetime, json, os, subprocess, sys, time, urllib.parse, urllib.request

BLUE = os.environ.get("BLUE_HOME") or os.path.dirname(os.path.abspath(__file__))
PHONES = os.path.join(BLUE, "redroid", "phones.json")
def conf():
    d = {}
    for line in open(os.path.join(BLUE, "cloudphone.conf"), encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); d[k.strip()] = v.strip()
    return d
C = conf(); SB = C["SUPABASE_URL"].rstrip("/"); KEY = C["SUPABASE_SERVICE_KEY"]; BASE = C["PUBLIC_BASE"].rstrip("/")
NODE = C.get("NODE_ID") or os.environ.get("COMPUTERNAME", "win1")

def sb(method, path, body=None):
    req = urllib.request.Request(f"{SB}/rest/v1/{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json", "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode() or "null")

def stream_url(port):
    q = urllib.parse.quote; udid = f"127.0.0.1:{port}"
    ws = BASE.replace("https://", "wss://") + "/?action=proxy-adb&remote=" + q("tcp:8886", safe="") + "&udid=" + q(udid, safe="")
    return f"{BASE}/#!action=stream&udid={q(udid, safe='')}&player=webcodecs&ws={q(ws, safe='')}"

def local():
    try: return {p["name"]: p for p in json.load(open(PHONES, encoding="utf-8")).get("phones", [])}
    except Exception: return {}

PRIMARY = C.get("PRIMARY", "1") == "1"
def rows():
    rs = sb("GET", "cloud_phones?status=eq.active&select=id,name,container,node,ram,cpu,res,fps,expires_at,user_id&order=created_at.asc") or []
    L = local()
    # CHỈ máy của MÁY CHỦ NÀY (nhiều máy chủ: không bao giờ làm lại máy đang chạy ở máy chủ khác).
    # Máy cũ chưa ghi máy chủ → thuộc máy chủ chính (PRIMARY=1).
    mine = [r for r in rs if r.get("node") == NODE or (not r.get("node") and PRIMARY)]
    return [r for r in mine if not (r.get("container") in L)]

def remake(r):
    name = r.get("container") or ("cp_" + r["id"].replace("-", "")[:10])
    print(f"\n######## {r.get('name') or name}  ({r['ram']}GB/{r['cpu']} nhân · hết hạn {str(r.get('expires_at'))[:16]}) ########", flush=True)
    if name in local(): subprocess.run([sys.executable, os.path.join(BLUE, "phonectl.py"), "delete", name], capture_output=True)
    p = subprocess.run([sys.executable, os.path.join(BLUE, "phonectl.py"), "create", name, "--ram", str(r["ram"]), "--cpu", str(r["cpu"]),
                        "--days", "1", "--res", r.get("res") or "720x1280", "--fps", str(r.get("fps") or 30)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("   " + ((p.stdout or "") + (p.stderr or "")).strip().replace("\n", "\n   "))
    q = local().get(name)
    if p.returncode != 0 or not q: print("   ✗ chưa làm mới được máy này"); return False
    st = json.load(open(PHONES, encoding="utf-8"))
    for x in st["phones"]:
        if x["name"] == name and r.get("expires_at"): x["expires"] = r["expires_at"]
    json.dump(st, open(PHONES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sb("PATCH", f"cloud_phones?id=eq.{r['id']}", {"container": name, "port": q["port"], "stream_url": stream_url(q["port"]),
                                                 "node": NODE, "android": "9", "error": None, "updated_at": datetime.datetime.now().isoformat()})
    print(f"   ✓ ĐÃ LÀM MỚI → {name} (MEmu, cổng {q['port']})"); return True

if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else "list"; L = rows()
    if act == "list":
        print(f"Máy khách đang thuê cần làm mới trên Windows: {len(L)}  (tổng RAM {sum(float(r['ram']) for r in L):g} GB)")
        for r in L: print(f"  {(r.get('container') or '')[:16]:16} {str(r.get('name'))[:24]:24} {r['ram']}GB/{r['cpu']} nhân  hết hạn {str(r.get('expires_at'))[:16]}")
    elif act == "one":
        m = [r for r in L if sys.argv[2] in (r.get("container"), r.get("name"))]
        if not m: sys.exit("Không thấy máy này.")
        remake(m[0])
    elif act == "all":
        ok = sum(1 for r in L if remake(r)); print(f"\n================ ĐÃ LÀM MỚI {ok}/{len(L)} MÁY ================")
