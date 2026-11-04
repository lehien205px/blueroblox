# -*- coding: utf-8 -*-
# =====================================================================================================
#  doi-ten-mien.py — chuyển MÁY CHỦ này sang tên miền mới (chạy 1 lần trên mỗi máy chủ)
#
#    Máy 1:  python C:\blue\doi-ten-mien.py cloudblue.vn
#    Máy 2:  python C:\blue\doi-ten-mien.py cloudblue.vn phone2
#
#  Làm:  1) sửa C:\blue\cloudphone.conf (giữ nguyên các dòng khác + khoá; chép dự phòng .bak trước khi sửa)
#           PUBLIC_BASE   = https://phone.<tên miền>           cổng bảo vệ cpapi qua đường hầm Cloudflare
#           STREAM_WS     = wss://stream.<tên miền>/v2/stream   đường phát hình dự phòng qua Cloudflare
#           CPAPI_ORIGINS = https://<tên miền>,https://www.<tên miền>  (giữ thêm các tên miền đã có)
#           NOTIFY_URL    = https://<tên miền>/api/notify-admin
#        2) đổi link điều khiển cũ (stream_url) của các máy khách trên máy chủ này sang tên miền mới
#  Xong: trong bảng bấm ■ Tắt tất cả → ▶ Bật tất cả.   Không in khoá bí mật ra màn hình.
# =====================================================================================================
import datetime, json, os, re, shutil, sys, urllib.parse, urllib.request

try: sys.stdout.reconfigure(errors="replace")
except Exception: pass

B = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(B, "cloudphone.conf")
HOST_RE = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


def read_conf():
    raw = open(CONF, encoding="utf-8-sig", newline="").read()   # utf-8-sig: bỏ BOM nếu PowerShell lỡ thêm vào; newline="": giữ kiểu xuống dòng
    cur = {}
    for l in raw.splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1); cur[k.strip()] = v.strip()
    return raw, cur


def write_conf(raw, want):
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines, seen = raw.splitlines(), set()
    for i, l in enumerate(lines):
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in want: lines[i] = f"{k}={want[k]}"; seen.add(k)
    lines += [f"{k}={v}" for k, v in want.items() if k not in seen]
    data, tmp = nl.join(lines) + nl, CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f: f.write(data)
    try: os.replace(tmp, CONF)
    except PermissionError:                     # file đang bị chương trình khác giữ → ghi thẳng
        with open(CONF, "w", encoding="utf-8", newline="") as f: f.write(data)
        os.remove(tmp)


def fix_stream_urls(cur, old_host, new_host):
    """Máy khách đã tạo trước đây giữ link cũ (phone.<tên miền cũ>/…) → đổi sang tên miền mới."""
    sb, key = cur.get("SUPABASE_URL", "").rstrip("/"), cur.get("SUPABASE_SERVICE_KEY", "")
    if not (sb and len(key) > 30): print("  (bỏ qua bước đổi link máy khách: chưa có khoá Supabase trong conf)"); return
    H = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    q = sb + "/rest/v1/cloud_phones?select=id,stream_url&stream_url=like." + urllib.parse.quote(f"*{old_host}*", safe="*")
    rows = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=H), timeout=20).read() or b"[]")
    n = 0
    for r in rows:
        su = r.get("stream_url") or ""
        nu = su.replace(old_host, new_host)
        if nu == su: continue
        req = urllib.request.Request(sb + "/rest/v1/cloud_phones?id=eq." + urllib.parse.quote(str(r["id"])),
                                     data=json.dumps({"stream_url": nu}).encode(), method="PATCH",
                                     headers={**H, "Prefer": "return=minimal"})
        urllib.request.urlopen(req, timeout=20).read(); n += 1
    print(f"✓ Đổi link điều khiển của {n} máy khách: {old_host} → {new_host}")


def main():
    a = [x.strip().lower().rstrip(".") for x in sys.argv[1:] if x.strip()]
    if a and a[0].startswith("www."): a[0] = a[0][4:]
    if not a or not HOST_RE.match(a[0]) or (len(a) > 1 and not re.fullmatch(r"[a-z0-9-]{1,40}", a[1])):
        print("Cách dùng:  python doi-ten-mien.py cloudblue.vn            (máy 1)")
        print("            python doi-ten-mien.py cloudblue.vn phone2     (máy 2)"); return 2
    dom, sub = a[0], (a[1] if len(a) > 1 else "phone")
    ssub = ("stream" + sub[5:]) if sub.startswith("phone") else ("stream-" + sub)
    try: raw, cur = read_conf()
    except FileNotFoundError: print("✗ Không thấy", CONF, "— chạy file này trong thư mục C:\\blue của máy chủ."); return 1

    origins = [f"https://{dom}", f"https://www.{dom}"] + [o.strip().rstrip("/") for o in cur.get("CPAPI_ORIGINS", "").split(",") if o.strip()]
    want = {"PUBLIC_BASE": f"https://{sub}.{dom}",
            "STREAM_WS": f"wss://{ssub}.{dom}/v2/stream",
            "CPAPI_ORIGINS": ",".join(dict.fromkeys(origins)),
            "NOTIFY_URL": f"https://{dom}/api/notify-admin"}
    old_host = urllib.parse.urlparse(cur.get("PUBLIC_BASE", "")).netloc.lower()

    bak = CONF + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"); i = 1
    while os.path.exists(bak): bak = bak.split("~")[0] + f"~{i}"; i += 1
    shutil.copy2(CONF, bak)
    write_conf(raw, want)
    print(f"✓ Đã sửa {CONF}  (bản cũ lưu ở {os.path.basename(bak)})")
    for k, v in want.items():
        o = cur.get(k, "")
        print(f"   {k:<14} {'(giữ nguyên)' if o == v else (o or '(chưa có)') + '  →'}  {v}")

    if old_host and old_host != f"{sub}.{dom}":
        try: fix_stream_urls(cur, old_host, f"{sub}.{dom}")
        except Exception as e: print("✗ Chưa đổi được link máy khách (chạy lại file này sau):", e)

    print()
    print(f"Tiếp theo: Cloudflare Zero Trust → đường hầm của máy này → thêm {sub}.{dom} → http://localhost:8010"
          f" và {ssub}.{dom} → http://localhost:8020 (nếu chưa thêm).")
    print("Rồi trong bảng BlueRoblox bấm  ■ Tắt tất cả  →  ▶ Bật tất cả.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
