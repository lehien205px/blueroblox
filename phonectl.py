#!/usr/bin/env python3
# phonectl.py — Quản lý máy Android (redroid) trên server: tạo/xoá/gia hạn + chặn 85% tài nguyên.
# Nền tảng của bộ điều phối (orchestrator). Chạy: python3 phonectl.py <lệnh> ...
# Lệnh:
#   create <ten> --ram 2 --cpu 2 --days 7 --res 720x1280   : tạo máy (tự chọn cổng, chặn nếu quá 85%)
#   list                                                    : liệt kê máy + hạn dùng
#   delete <ten>                                            : xoá máy
#   renew <ten> --days 7                                    : cộng thêm ngày
#   stop <ten>                                              : tạm dừng máy (khi vừa hết hạn)
#   reconfig <ten> --fps 60 --res 1080x1920                 : đổi FPS / độ phân giải (giữ nguyên dữ liệu)
#   reap                                                    : hết hạn → tạm dừng; quá 30 phút → xoá máy + dữ liệu
#   connect-all                                             : kết nối lại mọi máy vào trình điều khiển web
#   capacity                                                : xem tài nguyên đã dùng / ngưỡng 85%
import json, os, sys, subprocess, time, argparse, datetime, re

STATE = os.path.expanduser("~/redroid/phones.json")
DATA_ROOT = os.path.expanduser("~/redroid")
IMAGE = "redroid/redroid:11.0.0_gapps_ndk"
WSNAME = "wsscrcpy"                 # container ws-scrcpy (để đăng ký máy cho điều khiển web)
PORT_BASE = 5555
PORT_MAX = 5585                    # adb emulator chỉ quét 5555..5585
LIMIT_PCT = 85                    # ngưỡng chặn (theo % tài nguyên máy chủ)
GRACE_MIN = 30                    # hết hạn bao nhiêu phút thì xoá hẳn dữ liệu
BOOT_WAIT = 180                   # chờ Android khởi động tối đa (giây)
ARM = ("ro.product.cpu.abilist=x86_64,arm64-v8a,x86,armeabi-v7a,armeabi "
       "ro.product.cpu.abilist64=x86_64,arm64-v8a "
       "ro.product.cpu.abilist32=x86,armeabi-v7a,armeabi "
       "ro.dalvik.vm.isa.arm=x86 ro.dalvik.vm.isa.arm64=x86_64 "
       "ro.enable.native.bridge.exec=1 ro.vendor.enable.native.bridge.exec=1 "
       "ro.vendor.enable.native.bridge.exec64=1 "
       "ro.dalvik.vm.native.bridge=libndk_translation.so ro.ndk_translation.version=0.2.3").split()

def sh(cmd, check=False):
    cmd = [c for c in cmd if c != "sudo"]   # chạy docker trực tiếp (user đã trong nhóm docker)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r

def load():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {"phones": []}

def save(st):
    os.makedirs(DATA_ROOT, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)

def host_total():
    # RAM (GB) và số nhân CPU của máy chủ
    mem_kb = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal"): mem_kb = int(line.split()[1]); break
    return mem_kb / 1024 / 1024, os.cpu_count() or 1

def used(st):
    ram = sum(float(p.get("ram", 0)) for p in st["phones"])
    cpu = sum(float(p.get("cpu", 0)) for p in st["phones"])
    return ram, cpu

def cap():
    tr, tc = host_total()
    return tr * LIMIT_PCT / 100.0, tc * LIMIT_PCT / 100.0, tr, tc

def docker_busy_ports():
    # Cổng host đang bị bất kỳ container nào chiếm (kể cả phone1 tạo tay)
    busy = set()
    r = sh(["sudo", "docker", "ps", "--format", "{{.Ports}}"])
    for m in re.findall(r"(?:\d+\.\d+\.\d+\.\d+|\[::\]):(\d+)->", r.stdout or ""):
        busy.add(int(m))
    return busy

def free_port(st):
    busy = {int(p["port"]) for p in st["phones"]} | docker_busy_ports()
    for port in range(PORT_BASE, PORT_MAX + 1):
        if port not in busy: return port
    return None

def now(): return datetime.datetime.now().astimezone()
def parse_dt(s):
    d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()   # giờ cũ không có múi giờ → coi là giờ máy chủ

SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
def wipe_data(name):
    """Xoá SẠCH thư mục dữ liệu của máy (file do Android tạo thuộc root nên xoá qua docker)."""
    if not SAFE.match(name): return
    path = os.path.join(DATA_ROOT, name)
    if not os.path.exists(path): return
    sh(["docker", "run", "--rm", "-v", f"{DATA_ROOT}:/d", "alpine", "rm", "-rf", f"/d/{name}"])
    if os.path.exists(path):
        import shutil; shutil.rmtree(path, ignore_errors=True)

def adb_connect(port):
    sh(["docker", "exec", WSNAME, "adb", "connect", f"127.0.0.1:{port}"])

def is_running(name):
    r = sh(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return (r.stdout or "").strip() == "true"

TUNE = ("settings put global window_animation_scale 0.5; settings put global transition_animation_scale 0.5; "
        "settings put global animator_duration_scale 0.5; settings put global package_verifier_enable 0; "
        "settings put global verifier_verify_adb_installs 0; settings put global stay_on_while_plugged_in 7; "
        "settings put system screen_off_timeout 2147483647")
def tune(name):
    """Cho máy mượt hơn: giảm hiệu ứng chuyển cảnh, cài APK không phải chờ quét, màn hình không tự tắt."""
    sh(["docker", "exec", name, "/system/bin/sh", "-c", TUNE])
PRE_DIR = os.path.expanduser("~/apk-cai-san")   # bỏ file .apk vào đây → mọi máy mới tự cài sẵn (VD: trình duyệt Chrome/Firefox)
def preinstall(name, port, force=False):
    """Cài các APK trong ~/apk-cai-san vào máy (mỗi APK chỉ cài 1 lần / máy, lưu dấu trong /data của máy)."""
    try: apks = sorted(f for f in os.listdir(PRE_DIR) if f.lower().endswith((".apk", ".apks", ".apkm", ".xapk")))
    except OSError: return
    for f in apks:
        mark = "/data/local/tmp/.cp_pre_" + re.sub(r"[^\w.-]", "_", f)
        if not force and sh(["docker", "exec", name, "/system/bin/sh", "-c", f"test -f {mark} && echo y"]).stdout.strip() == "y":
            continue
        r = install_file(port, os.path.join(PRE_DIR, f))
        if "Success" in (r.stdout or ""):
            sh(["docker", "exec", name, "/system/bin/sh", "-c", f"touch {mark}"]); print(f"   + Đã cài sẵn {f} cho {name}")
        else: print(f"   ! Cài {f} cho {name} lỗi: {((r.stdout or '') + (r.stderr or ''))[-200:]}")

def install_file(port, path):
    """Cài 1 file: .apk thường, hoặc gói nhiều phần .apks/.apkm/.xapk (VD Chrome, Gmail tải từ APKMirror)."""
    import zipfile, tempfile, shutil
    base = re.sub(r"[^\w.-]", "_", os.path.basename(path))
    if path.lower().endswith(".apk"):
        tmp = "/tmp/pre_" + base
        sh(["docker", "cp", path, f"{WSNAME}:{tmp}"])
        r = sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{port}", "install", "-r", "-g", tmp])
        sh(["docker", "exec", WSNAME, "rm", "-f", tmp]); return r
    d = tempfile.mkdtemp(prefix="cpb_"); rdir = "/tmp/preb_" + base
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".apk") and "/" not in n.strip("/")]
            for n in names: z.extract(n, d)
        if not names: return subprocess.CompletedProcess([], 1, "", "gói không có file .apk bên trong")
        sh(["docker", "exec", WSNAME, "rm", "-rf", rdir]); sh(["docker", "cp", d, f"{WSNAME}:{rdir}"])
        r = sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{port}", "install-multiple", "-r", "-g"] + [f"{rdir}/{n}" for n in names])
        sh(["docker", "exec", WSNAME, "rm", "-rf", rdir]); return r
    except zipfile.BadZipFile:
        return subprocess.CompletedProcess([], 1, "", "file gói hỏng hoặc bị mã hoá")
    finally: shutil.rmtree(d, ignore_errors=True)

# Cho phép trình duyệt / Gmail / app quản lý file cài APK (khách khỏi phải bật "Cài ứng dụng không rõ nguồn")
PERM_SH = ("for p in com.android.chrome org.mozilla.firefox com.google.android.gm com.google.android.documentsui "
           "com.android.documentsui com.google.android.apps.nbu.files $(pm list packages -3 2>/dev/null | cut -d: -f2); do "
           "pm path $p >/dev/null 2>&1 && appops set $p REQUEST_INSTALL_PACKAGES allow >/dev/null 2>&1; done; "
           "settings put secure install_non_market_apps 1 >/dev/null 2>&1; true")
def grant_perms(name):
    sh(["docker", "exec", name, "/system/bin/sh", "-c", PERM_SH])

def cmd_perms(a):
    """Mở quyền cài APK cho mọi máy đang chạy (chương trình nền tự gọi mỗi 5 phút → app khách cài sau cũng được mở)."""
    running = set((sh(["docker", "ps", "--format", "{{.Names}}"]).stdout or "").split())
    for p in load()["phones"]:
        if p["name"] in running: grant_perms(p["name"])

def cmd_preinstall(a):
    """Cài APK trong ~/apk-cai-san cho máy đang chạy: phonectl.py preinstall TÊN_MÁY  hoặc  --all"""
    st = load()
    for p in st["phones"]:
        if a.all or p["name"] == a.name:
            if sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{p['port']}", "get-state"]).stdout.strip() != "device":
                adb_connect(p["port"])
            preinstall(p["name"], p["port"], a.force); grant_perms(p["name"])

def wait_boot(name, port):
    """Chờ Android khởi động xong rồi mới kết nối (kết nối sớm → màn đen)."""
    t0 = time.time()
    while time.time() - t0 < BOOT_WAIT:
        r = sh(["docker", "exec", name, "/system/bin/getprop", "sys.boot_completed"])
        if (r.stdout or "").strip() == "1": break
        time.sleep(5)
    tune(name)
    time.sleep(3)
    for _ in range(5):
        adb_connect(port)
        r = sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{port}", "get-state"])
        if (r.stdout or "").strip() == "device":
            preinstall(name, port); grant_perms(name); return True
        time.sleep(4)
    return False
def fmt(dt): return dt.strftime("%d/%m/%Y %H:%M")

def cmd_capacity(a):
    st = load(); ur, uc = used(st); lr, lc, tr, tc = cap()
    print(f"Máy chủ: RAM {tr:.1f} GB, CPU {tc} nhân")
    print(f"Ngưỡng {LIMIT_PCT}%: RAM {lr:.1f} GB, CPU {lc:.1f} nhân")
    print(f"Đã cấp:  RAM {ur:.1f} GB, CPU {uc:.1f} nhân  ({len(st['phones'])} máy)")
    print(f"Còn:     RAM {lr-ur:.1f} GB, CPU {lc-uc:.1f} nhân")

def cmd_create(a):
    st = load()
    if any(p["name"] == a.name for p in st["phones"]):
        print("LỖI: đã có máy tên này."); sys.exit(1)
    ur, uc = used(st); lr, lc, tr, tc = cap()
    if ur + a.ram > lr or uc + a.cpu > lc:
        print(f"HẾT HÀNG: thêm máy này (RAM {a.ram}GB, CPU {a.cpu}) sẽ vượt {LIMIT_PCT}%.")
        print(f"  Còn: RAM {lr-ur:.1f}GB, CPU {lc-uc:.1f} nhân."); sys.exit(2)
    port = free_port(st)
    if not port: print("LỖI: hết cổng (tối đa 30 máy/node)."); sys.exit(3)
    if not re.match(r"^\d+x\d+$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    if not SAFE.match(a.name): print("LỖI: tên máy chỉ gồm chữ, số, _ . -"); sys.exit(1)
    fps = clamp_fps(a.fps)
    sh(["docker", "rm", "-f", a.name])           # phòng còn container cũ trùng tên
    wipe_data(a.name)                             # đảm bảo máy MỚI TINH, không dính dữ liệu cũ
    r = docker_run(a.name, a.ram, a.cpu, port, a.res, fps, a.dpi)
    if r.returncode != 0:
        print("LỖI tạo máy:", (r.stderr or r.stdout).strip()); sys.exit(4)
    # chờ boot xong rồi mới đăng ký vào ws-scrcpy (tránh màn đen) — bỏ qua nếu --nowait (chương trình nền tự chờ song song)
    ok = True if a.nowait else wait_boot(a.name, port)
    exp = now() + datetime.timedelta(days=a.days)
    st["phones"].append({"name": a.name, "port": port, "ram": a.ram, "cpu": a.cpu, "res": a.res, "fps": fps,
                          "dpi": a.dpi, "created": fmt(now()), "expires": exp.isoformat()})
    save(st)
    print(f"✅ Đã tạo máy '{a.name}'  | cổng {port} | RAM {a.ram}GB CPU {a.cpu} | hết hạn {fmt(exp)}")
    print("   Android đã khởi động xong, sẵn sàng điều khiển." if ok else "   CẢNH BÁO: máy chưa kết nối được trình điều khiển, sẽ tự thử lại.")

def clamp_fps(v):
    try: v = int(v)
    except Exception: v = 30
    return min(max(v, 10), 120)

def auto_dpi(res):
    w, h = (int(x) for x in res.split("x"))
    return max(160, min(640, round(min(w, h) / 2.25)))     # 720→320, 1080→480, 540→240

def docker_run(name, ram, cpu, port, res, fps, dpi=0):
    w, h = res.split("x")
    data = os.path.join(DATA_ROOT, name); os.makedirs(data, exist_ok=True)
    return sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", name,
               f"--memory={ram}g", f"--cpus={cpu}",
               "-v", f"{data}:/data", "-p", f"127.0.0.1:{port}:5555", IMAGE,   # chỉ mở trong server, không lộ ra internet
               f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
               f"androidboot.redroid_dpi={int(dpi) or auto_dpi(res)}", f"androidboot.redroid_fps={fps}"] + ARM)

def cmd_reconfig(a):
    """Đổi FPS / độ phân giải: tạo lại container với CÙNG thư mục dữ liệu → giữ nguyên app, tài khoản."""
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    res = a.res or p.get("res") or "720x1280"
    if not re.match(r"^\d{3,4}x\d{3,4}$", res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    fps = clamp_fps(a.fps if a.fps else p.get("fps", 30))
    dpi = p.get("dpi", 0) if res == p.get("res") else 0
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    rm_container(a.name)                       # chỉ xoá container, KHÔNG xoá dữ liệu
    r = docker_run(a.name, p["ram"], p["cpu"], p["port"], res, fps, dpi)
    if r.returncode != 0:
        print("LỖI:", (r.stderr or r.stdout).strip()); sys.exit(4)
    p["res"], p["fps"], p["dpi"] = res, fps, dpi; save(st)
    ok = wait_boot(a.name, p["port"])
    print(f"✅ '{a.name}' → {res} · {fps} FPS" + ("" if ok else " (đang kết nối lại)"))

def cmd_waitboot(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    ok = wait_boot(a.name, p["port"])
    print("Android đã khởi động xong." if ok else "Chưa kết nối được, sẽ tự thử lại."); sys.exit(0 if ok else 5)

def rm_container(name):
    sh(["sudo", "docker", "rm", "-f", name])

def cmd_delete(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    rm_container(a.name); wipe_data(a.name)
    st["phones"] = [x for x in st["phones"] if x["name"] != a.name]; save(st)
    print(f"🗑️  Đã xoá máy '{a.name}' và toàn bộ dữ liệu.")

def cmd_stop(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    sh(["docker", "stop", a.name])
    print(f"⏸  Đã tạm dừng '{a.name}' (dữ liệu còn giữ {GRACE_MIN} phút).")

def adb_online():
    r = sh(["docker", "exec", WSNAME, "adb", "devices"])
    ok = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device": ok.add(parts[0])
    return ok

def cmd_connect_all(a):
    """Máy nào đang chạy mà trình điều khiển chưa thấy (rớt kết nối) → nối lại."""
    st = load(); online = adb_online(); n = 0
    for p in st["phones"]:
        serial = f"127.0.0.1:{p['port']}"
        if serial in online or not is_running(p["name"]): continue
        sh(["docker", "exec", WSNAME, "adb", "disconnect", serial])
        adb_connect(p["port"]); n += 1
        print("🔌 nối lại", p["name"], serial)
    if n == 0: print("Tất cả máy đã kết nối.")

def cmd_renew(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    base = parse_dt(p["expires"])
    if base < now(): base = now()
    base += datetime.timedelta(days=a.days)
    p["expires"] = base.isoformat(); save(st)
    if not is_running(a.name):                    # máy đang tạm dừng vì hết hạn → bật lại
        sh(["docker", "start", a.name]); wait_boot(a.name, p["port"])
    print(f"🔄 '{a.name}' gia hạn +{a.days} ngày → hết hạn {fmt(base)}")

def cmd_list(a):
    st = load()
    if not st["phones"]: print("(chưa có máy nào)"); return
    print(f"{'TÊN':<16}{'CỔNG':<7}{'RAM':<6}{'CPU':<5}{'HẾT HẠN':<18}{'CÒN'}")
    for p in st["phones"]:
        exp = parse_dt(p["expires"])
        left = exp - now(); d = left.days; hrs = int(left.total_seconds()//3600)
        status = f"{d}n" if d >= 0 else "HẾT HẠN"
        print(f"{p['name']:<16}{p['port']:<7}{str(p['ram'])+'G':<6}{str(p['cpu']):<5}{fmt(exp):<18}{status}")

def cmd_reap(a):
    st = load(); n = 0
    for p in list(st["phones"]):
        exp = parse_dt(p["expires"])
        if exp + datetime.timedelta(minutes=GRACE_MIN) < now():
            sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
            rm_container(p["name"]); wipe_data(p["name"])
            st["phones"] = [x for x in st["phones"] if x["name"] != p["name"]]; n += 1
            print(f"🗑️  Hết hạn quá {GRACE_MIN} phút → xoá '{p['name']}' + dữ liệu")
        elif exp < now() and is_running(p["name"]):
            sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
            sh(["docker", "stop", p["name"]])
            print(f"⏸  Hết hạn → tạm dừng '{p['name']}' (còn {GRACE_MIN} phút để gia hạn)")
    save(st)
    print(f"Xong. Đã xoá {n} máy.")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create"); c.add_argument("name"); c.add_argument("--ram", type=float, default=2); c.add_argument("--cpu", type=float, default=2); c.add_argument("--days", type=int, default=7); c.add_argument("--res", default="720x1280"); c.add_argument("--dpi", type=int, default=0); c.add_argument("--fps", type=int, default=30); c.add_argument("--nowait", action="store_true"); c.set_defaults(f=cmd_create)
    sub.add_parser("list").set_defaults(f=cmd_list)
    d = sub.add_parser("delete"); d.add_argument("name"); d.set_defaults(f=cmd_delete)
    r = sub.add_parser("renew"); r.add_argument("name"); r.add_argument("--days", type=int, default=7); r.set_defaults(f=cmd_renew)
    sub.add_parser("reap").set_defaults(f=cmd_reap)
    s = sub.add_parser("stop"); s.add_argument("name"); s.set_defaults(f=cmd_stop)
    sub.add_parser("connect-all").set_defaults(f=cmd_connect_all)
    w = sub.add_parser("waitboot"); w.add_argument("name"); w.set_defaults(f=cmd_waitboot)
    g = sub.add_parser("reconfig"); g.add_argument("name"); g.add_argument("--fps", type=int, default=0); g.add_argument("--res", default=""); g.set_defaults(f=cmd_reconfig)
    sub.add_parser("capacity").set_defaults(f=cmd_capacity)
    sub.add_parser("perms").set_defaults(f=cmd_perms)
    pi = sub.add_parser("preinstall"); pi.add_argument("name", nargs="?", default=""); pi.add_argument("--all", action="store_true"); pi.add_argument("--force", action="store_true"); pi.set_defaults(f=cmd_preinstall)
    a = ap.parse_args()
    if not getattr(a, "cmd", None): ap.print_help(); sys.exit(0)
    a.f(a)

if __name__ == "__main__":
    main()
