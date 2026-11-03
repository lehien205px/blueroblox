# -*- coding: utf-8 -*-
# =====================================================================
#  phonectl.py — BẢN WINDOWS (MEmu Play). Cùng câu lệnh với bản Ubuntu để cloudphoned.py / cpapi.py dùng y nguyên.
#   python phonectl.py create TÊN --ram 4 --cpu 4 --days 7 [--res 720x1280] [--nowait]
#   python phonectl.py list | capacity | delete TÊN | stop TÊN | start TÊN | restart TÊN | renew TÊN --days N
#   python phonectl.py resize TÊN --ram 6 --cpu 6 | waitboot TÊN | connect-all | reap | rootfix --all
#  Máy số N của MEmu ⇄ adb 127.0.0.1:(21503 + 10·N)
# =====================================================================
import argparse, ctypes, datetime, json, os, re, subprocess, sys, time

HOME = os.environ.get("BLUE_HOME") or os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HOME, "redroid", "phones.json")
CONF_F = os.path.join(HOME, "cloudphone.conf")
ADB = os.path.join(HOME, "platform-tools", "adb.exe")
GRACE_MIN = 30
BOOT_WAIT = 300
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")

def _conf():
    c = {}
    try:
        for line in open(CONF_F, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); c[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError: pass
    return c
CONF = _conf()
RESERVE = float(CONF.get("WIN_RAM_RESERVE", "4") or 4)          # GB RAM Windows luôn chừa lại
MEMU_DIRS = [CONF.get("MEMU_DIR", ""), os.environ.get("MEMU_DIR", ""), r"C:\Program Files\Microvirt\MEmu", r"D:\Program Files\Microvirt\MEmu",
             r"C:\Microvirt\MEmu", r"D:\Microvirt\MEmu", r"C:\Program Files (x86)\Microvirt\MEmu"]
MEMU = next((d for d in MEMU_DIRS if d and os.path.isfile(os.path.join(d, "memuc.exe"))), "")
NOWIN = 0x08000000

def run(args, timeout=180, text=True):
    try:
        return subprocess.run([str(x) for x in args], capture_output=True, text=text, timeout=timeout, creationflags=NOWIN,
                              encoding="utf-8" if text else None, errors="replace" if text else None)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timeout")
def memuc(*a, timeout=300):
    """memuc chỉ xử lý 1 lệnh một lúc (chạy chồng → "read failed: 233") → xếp hàng bằng khoá + thử lại."""
    r = None
    for i in range(4):
        with _Lock("memuc", wait=max(120, timeout)):
            r = run([os.path.join(MEMU, "memuc.exe")] + list(a), timeout)
        o = (r.stdout or "") + (r.stderr or "")
        if "read failed" not in o and "connect failed" not in o.lower(): return r
        time.sleep(2 + 2 * i)
    return r
def adb(*a, timeout=60): return run([ADB] + list(a), timeout)

# ---------- trạng thái ----------
def load():
    try:
        with open(STATE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {"phones": []}
def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)
def rec(name, st=None): return next((x for x in (st or load())["phones"] if x["name"] == name), None)
def now(): return datetime.datetime.now().astimezone()
def parse_dt(s):
    d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")); return d if d.tzinfo else d.astimezone()
def fmt(dt): return dt.strftime("%d/%m/%Y %H:%M")
def port_of(idx): return 21503 + 10 * int(idx)

class _Lock:            # khoá file: các lệnh memuc không chạy chồng nhau
    def __init__(self, name, wait=600): self.p = os.path.join(HOME, "redroid", name + ".lock"); self.wait = wait
    def __enter__(self):
        os.makedirs(os.path.dirname(self.p), exist_ok=True)
        for _ in range(int(self.wait * 4)):
            try: self.fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY); return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.p) > 960: os.remove(self.p)
                except Exception: pass
                time.sleep(0.25)
        raise RuntimeError("đợi khoá quá lâu")
    def __exit__(self, *a):
        try: os.close(self.fd); os.remove(self.p)
        except Exception: pass

def vms():
    """{index: {name, running}} theo memuc listvms (index,tên,cửa sổ,đang chạy,pid,dung lượng)."""
    out = {}
    for line in (memuc("listvms", timeout=60).stdout or "").splitlines():
        p = line.split(",")
        if len(p) >= 4 and p[0].strip().isdigit():
            out[int(p[0])] = {"name": p[1].strip(), "running": p[3].strip() == "1"}
    return out
def is_running(name, p=None, _v=None):
    p = p or rec(name)
    if not p: return False
    v = (_v if _v is not None else vms()).get(int(p["idx"]))
    return bool(v and v["running"])

def mem():
    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                    ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
    m = M(); m.l = ctypes.sizeof(M); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.total / 2**30, m.avail / 2**30

def serial(p): return f"127.0.0.1:{p['port']}"
def adb_connect(port): return adb("connect", f"127.0.0.1:{port}", timeout=20)
def ashell(name, script, port=None, timeout=60):
    p = rec(name); s = f"127.0.0.1:{port or p['port']}"
    if (adb("-s", s, "get-state", timeout=15).stdout or "").strip() != "device": adb_connect(port or p["port"])
    return adb("-s", s, "shell", script, timeout=timeout)

BROWSERS = ("com.android.chrome org.mozilla.firefox com.brave.browser com.google.android.gm com.google.android.documentsui "
            "com.android.documentsui com.google.android.apps.nbu.files com.kiwibrowser.browser com.opera.browser "
            "com.microsoft.emmx mark.via.gp com.sec.android.app.sbrowser com.mi.android.globalFileexplorer com.UCMobile.intl")
PERM_SH = ("for p in " + BROWSERS + "; do pm path $p >/dev/null 2>&1 || continue; "
           "case \"$(appops get $p REQUEST_INSTALL_PACKAGES 2>/dev/null)\" in *allow*) ;; "
           "*) appops set $p REQUEST_INSTALL_PACKAGES allow >/dev/null 2>&1;; esac; done; true")

TUNE = (# CLONE TAB cần Android cho phép nhiều người dùng (máy ảo mặc định chỉ 1) — mất khi khởi động lại nên đặt lại mỗi lần
        "[ \"$(getprop fw.max_users)\" = 8 ] || setprop fw.max_users 8; "
        # Trình cài đặt ban đầu của Google bị lỗi trên máy ảo (Android 9/10 kẹt ở MÀN ĐEN) → tắt nó + đánh dấu "đã cài đặt xong"
        "if pm path com.google.android.setupwizard >/dev/null 2>&1 && [ \"$(settings get secure user_setup_complete)\" != 1 ]; then "
        "settings put global device_provisioned 1; settings put secure user_setup_complete 1; "
        "pm disable com.google.android.setupwizard >/dev/null 2>&1; am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1; fi; "
        # tắt hẳn hiệu ứng chuyển cảnh → CPU không phải vẽ thêm
        "settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
        "settings put global animator_duration_scale 0; settings put global package_verifier_enable 0; "
        # cửa sổ nổi: mở nhiều app/tab cùng lúc (có hiệu lực sau lần khởi động lại máy kế tiếp)
        "settings put global enable_freeform_support 1; settings put global force_resizable_activities 1; "
        "settings put global enable_sizecompat_freeform 1; "
        # treo game qua đêm: không ngủ đông (doze), không xếp app vào nhóm hạn chế, không đóng băng app chạy nền
        "dumpsys deviceidle disable >/dev/null 2>&1; settings put global app_standby_enabled 0; "
        "settings put global adaptive_battery_management_enabled 0; settings put global always_finish_activities 0; "
        "device_config put activity_manager_native_boot use_freezer false >/dev/null 2>&1; "
        "settings put global cached_apps_freezer disabled; "
        # Cửa hàng Play KHÔNG tự cập nhật app chạy nền (đỡ lag, đỡ tốn mạng); khách mở Play vẫn tải/cập nhật tay được.
        # Chỉ đặt khi chưa đặt — đổi quyền của app làm Android tắt app đó, đặt lặp lại sẽ văng Play khi đang dùng.
        "case \"$(cmd appops get com.android.vending RUN_ANY_IN_BACKGROUND 2>/dev/null)\" in *ignore*) ;; *) "
        "cmd appops set com.android.vending RUN_ANY_IN_BACKGROUND ignore; am set-standby-bucket com.android.vending restricted;; esac; "
        "settings put global package_verifier_user_consent -1; settings put global upload_apk_enable 0; "
        "settings put global verifier_verify_adb_installs 0; settings put global stay_on_while_plugged_in 7; "
        "settings put system screen_off_timeout 2147483647; svc power stayon true; "
        # Trình duyệt bị MÀN ĐEN (máy ảo không có chip đồ hoạ): Firefox dùng WebRender phần mềm
        # (Chrome trên Android KHÔNG chạy được khi tắt GPU → đã bỏ cờ --disable-gpu cũ, nó làm Chrome văng ngay lúc mở)
        "rm -f /data/local/chrome-command-line /data/local/tmp/chrome-command-line /data/local/tmp/webview-command-line 2>/dev/null; "
        "for d in /data/data/org.mozilla.firefox/files/mozilla/*.default*; do [ -d \"$d\" ] || continue; "
        "grep -q webrender.software \"$d/user.js\" 2>/dev/null || { printf 'user_pref(\"gfx.webrender.software\", true);\\nuser_pref(\"layers.acceleration.disabled\", true);\\nuser_pref(\"gfx.canvas.accelerated\", false);\\n' >> \"$d/user.js\"; "
        "chown $(stat -c %u \"$d\"):$(stat -c %g \"$d\") \"$d/user.js\"; }; done 2>/dev/null; "
        # Cổng VPN: Android mở /dev/tun (docker chỉ có /dev/net/tun) → thiếu thì VPN báo "Failed to connect"
        "[ -c /dev/tun ] || { rm -f /dev/tun; mknod /dev/tun c 10 200; }; chmod 666 /dev/tun")

def tune(name, port=None):
    try: ashell(name, TUNE, port)
    except Exception: pass
def grant_perms(name):
    try: ashell(name, PERM_SH)
    except Exception: pass
    tune(name)

def auto_dpi(res):
    w, h = (int(x) for x in res.split("x")); return max(160, min(640, round(min(w, h) / 2.25)))
def clamp_fps(v):
    try: v = int(v)
    except Exception: v = 30
    return min(max(v, 10), 120)

def configure(idx, ram, cpu, res, dpi):
    w, h = res.lower().split("x")
    for k, v in (("memory", int(float(ram) * 1024)), ("cpus", max(1, int(round(float(cpu))))), ("is_customed_resolution", 1),
                 ("resolution_width", w), ("resolution_height", h), ("vbox_dpi", int(dpi or auto_dpi(res))), ("enable_su", 1)):
        memuc("setconfigex", "-i", idx, k, v, timeout=60)

def booted(port):
    r = adb("-s", f"127.0.0.1:{port}", "shell", "getprop sys.boot_completed", timeout=15)
    return (r.stdout or "").strip() == "1"

def wait_boot(name, port, apps=True):
    t0 = time.time(); ok = False
    while time.time() - t0 < BOOT_WAIT:
        adb_connect(port)
        if booted(port): ok = True; break
        time.sleep(3)
    if ok:
        tune(name, port); grant_perms(name)
        if apps: preinstall(name, port)
    return ok

# ---------- cài app sẵn: mọi file .apk trong <BLUE>\apk-cai-san ----------
def preinstall(name, port, force=False):
    d = os.path.join(HOME, "apk-cai-san")
    if not os.path.isdir(d): return
    have = adb("-s", f"127.0.0.1:{port}", "shell", "pm list packages -3", timeout=60).stdout or ""
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".apk"): continue
        mark = os.path.join(HOME, "redroid", f"pre_{name}_{f}.ok")
        if os.path.exists(mark) and not force: continue
        r = adb("-s", f"127.0.0.1:{port}", "install", "-r", "-g", os.path.join(d, f), timeout=900)
        print(("+ " if "Success" in (r.stdout or "") else "! ") + f"{name}: {f}")
        if "Success" in (r.stdout or ""): open(mark, "w").close()

# ---------- lệnh ----------
def cmd_create(a):
    st = load()
    if rec(a.name, st): print("LỖI: đã có máy tên này."); sys.exit(1)
    if not SAFE.match(a.name): print("LỖI: tên máy chỉ gồm chữ, số, _ . -"); sys.exit(1)
    if not re.match(r"^\d+x\d+$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    if not MEMU: print("LỖI: không thấy MEmu (memuc.exe)."); sys.exit(4)
    tot, av = mem()
    if av - (a.ram + 1) < RESERVE:
        print(f"HẾT HÀNG: RAM Windows còn {av:.1f}GB, máy {a.ram:g}GB cần thêm {a.ram + 1:g}GB và chừa {RESERVE:g}GB."); sys.exit(2)
    print(f"[1/4] Tạo máy Android 9 trên MEmu…", flush=True)
    with _Lock("memu"):
        before = set(vms())
        r = memuc("create", "96", timeout=900)                       # 96 = Android 9 64-bit
        m = re.search(r"index\D*(\d+)", r.stdout or "")
        idx = int(m.group(1)) if m else next(iter(sorted(set(vms()) - before)), None)
        if idx is None: print("LỖI tạo máy:", ((r.stdout or "") + (r.stderr or "")).strip()[-300:]); sys.exit(4)
        print(f"[2/4] Đã tạo MEmu #{idx} → đặt tên, RAM, CPU, root…", flush=True)
        memuc("rename", "-i", idx, a.name, timeout=60)
        dpi = a.dpi or auto_dpi(a.res)
        configure(idx, a.ram, a.cpu, a.res, dpi)
    port = port_of(idx); exp = now() + datetime.timedelta(days=a.days)
    row = {"name": a.name, "idx": idx, "port": port, "ram": a.ram, "cpu": a.cpu, "res": a.res, "fps": clamp_fps(a.fps), "dpi": dpi,
           "android": "9", "kind": "memu", "created": fmt(now()), "expires": exp.isoformat()}
    st = load(); st["phones"].append(row); save(st)
    print(f"[3/4] Bật máy…", flush=True)
    s = memuc("start", "-i", idx, timeout=600)
    if not a.nowait: print(f"[4/4] Chờ Android khởi động (tối đa {BOOT_WAIT // 60} phút)…", flush=True)
    if s.returncode not in (0, None) and "SUCCESS" not in (s.stdout or "").upper():
        print("CẢNH BÁO bật máy:", (s.stdout or s.stderr or "").strip()[-200:])
    ok = True if a.nowait else wait_boot(a.name, port)
    print(f"✅ Đã tạo máy '{a.name}' (MEmu #{idx})  | cổng {port} | RAM {a.ram:g}GB CPU {a.cpu:g} | hết hạn {fmt(exp)}")
    print("   Android đã khởi động xong, sẵn sàng điều khiển." if ok else "   CẢNH BÁO: máy chưa kết nối được trình điều khiển, sẽ tự thử lại.")

def _need(name):
    st = load(); p = rec(name, st)
    if not p: print("Không có máy tên này."); sys.exit(1)
    return st, p

def cmd_delete(a):
    st, p = _need(a.name)
    adb("disconnect", serial(p), timeout=15)
    with _Lock("memu"):
        memuc("stop", "-i", p["idx"], timeout=120); time.sleep(3)
        memuc("remove", "-i", p["idx"], timeout=600)
    st = load(); st["phones"] = [x for x in st["phones"] if x["name"] != a.name]; save(st)
    print(f"🗑️  Đã xoá máy '{a.name}' và toàn bộ dữ liệu.")

def cmd_stop(a):
    st, p = _need(a.name); adb("disconnect", serial(p), timeout=15)
    memuc("stop", "-i", p["idx"], timeout=120); print(f"⏸  Đã tạm dừng '{a.name}'.")

def start_phone(p):
    r = memuc("start", "-i", p["idx"], timeout=600); adb_connect(p["port"]); return r
def cmd_start(a):
    st, p = _need(a.name); start_phone(p); print("✅ Đã bật")
def cmd_restart(a):
    st, p = _need(a.name); adb("disconnect", serial(p), timeout=15)
    memuc("stop", "-i", p["idx"], timeout=120); time.sleep(3); start_phone(p); print("✅ Đã khởi động lại")

def cmd_waitboot(a):
    st, p = _need(a.name)
    ok = wait_boot(a.name, p["port"], apps=not a.noapps)
    print("Android đã khởi động xong." if ok else "Chưa kết nối được, sẽ tự thử lại."); sys.exit(0 if ok else 5)

def cmd_renew(a):
    st, p = _need(a.name)
    base = parse_dt(p["expires"]); base = max(base, now()) + datetime.timedelta(days=a.days)
    p["expires"] = base.isoformat(); save(st)
    if not is_running(a.name, p): start_phone(p); wait_boot(a.name, p["port"], apps=False)
    print(f"🔄 '{a.name}' gia hạn +{a.days} ngày → hết hạn {fmt(base)}")

def cmd_resize(a):
    st, p = _need(a.name)
    ram = a.ram if a.ram > 0 else float(p["ram"]); cpu = a.cpu if a.cpu > 0 else float(p["cpu"])
    tot, av = mem()
    if ram > float(p["ram"]) and av - (ram - float(p["ram"])) < RESERVE: print("LỖI: RAM Windows không đủ để nâng."); sys.exit(4)
    adb("disconnect", serial(p), timeout=15); memuc("stop", "-i", p["idx"], timeout=120); time.sleep(3)
    configure(p["idx"], ram, cpu, p.get("res", "720x1280"), p.get("dpi"))
    start_phone(p); wait_boot(a.name, p["port"], apps=False)
    st = load(); q = rec(a.name, st); q["ram"], q["cpu"] = ram, cpu; save(st)
    print(f"⬆️ '{a.name}' → {ram:g}GB RAM · {cpu:g} nhân")

def cmd_reconfig(a):
    st, p = _need(a.name)
    res = a.res or p.get("res") or "720x1280"; fps = clamp_fps(a.fps or p.get("fps", 30)); dpi = auto_dpi(res)
    adb("disconnect", serial(p), timeout=15); memuc("stop", "-i", p["idx"], timeout=120); time.sleep(3)
    configure(p["idx"], p["ram"], p["cpu"], res, dpi); start_phone(p); ok = wait_boot(a.name, p["port"], apps=False)
    st = load(); q = rec(a.name, st); q["res"], q["fps"], q["dpi"] = res, fps, dpi; save(st)
    print(f"✅ '{a.name}' → {res} · {fps} FPS" + ("" if ok else " (đang kết nối lại)"))

def cmd_liveres(a):
    st, p = _need(a.name)
    if not re.match(r"^\d{3,4}x\d{3,4}$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    dpi = auto_dpi(a.res)
    adb("-s", serial(p), "shell", "wm", "size", a.res); adb("-s", serial(p), "shell", "wm", "density", str(dpi))
    p["res"], p["dpi"] = a.res, dpi; save(st); print(f"✅ '{a.name}' → {a.res} (dpi {dpi}) — không khởi động lại")

def cmd_connect_all(a):
    st = load(); v = vms()
    online = {l.split()[0] for l in (adb("devices", timeout=20).stdout or "").splitlines()[1:] if l.strip().endswith("device")}
    n = 0
    for p in st["phones"]:
        if serial(p) in online or not is_running(p["name"], p, v): continue
        adb_connect(p["port"]); n += 1; print("🔌 nối lại", p["name"], serial(p))
    if n == 0: print("Tất cả máy đã kết nối.")

def cmd_reap(a):
    st = load(); n = 0
    for p in list(st["phones"]):
        exp = parse_dt(p["expires"])
        if exp + datetime.timedelta(minutes=GRACE_MIN) < now():
            a.name = p["name"]; cmd_delete(a); n += 1
        elif exp < now() and is_running(p["name"], p):
            memuc("stop", "-i", p["idx"], timeout=120); print(f"⏸  Hết hạn → tạm dừng '{p['name']}'")
    print(f"Xong. Đã xoá {n} máy.")

def cmd_preinstall(a):
    st = load(); v = vms()
    for p in st["phones"]:
        if (a.all and is_running(p["name"], p, v)) or p["name"] == a.name:
            adb_connect(p["port"]); preinstall(p["name"], p["port"], a.force); grant_perms(p["name"])

def cmd_perms(a):
    v = vms()
    for p in load()["phones"]:
        if is_running(p["name"], p, v): grant_perms(p["name"])

def cmd_rootfix(a):
    v = vms()
    for p in load()["phones"]:
        if not (a.all or p["name"] == a.name): continue
        if not is_running(p["name"], p, v): print(f"ROOT_SKIP {p['name']}: máy đang tắt"); continue
        o = (ashell(p["name"], "command -v su").stdout or "")
        if "su" not in o:
            memuc("setconfigex", "-i", p["idx"], "enable_su", 1, timeout=60)
            print(f"ROOT_WAIT {p['name']}: vừa bật root, khởi động lại máy để có hiệu lực")
        else: print(f"ROOT_OK {p['name']}: đã root — tool gọi su là chạy bằng quyền root")

def cmd_capacity(a):
    st = load(); tot, av = mem()
    print(f"Windows: RAM {tot:.1f} GB (còn trống {av:.1f} GB, chừa {RESERVE:g} GB) · CPU {os.cpu_count()} luồng · MEmu: {MEMU or 'KHÔNG THẤY'}")
    print(f"Đã cấp:  RAM {sum(float(p['ram']) for p in st['phones']):.1f} GB  ({len(st['phones'])} máy)")

def cmd_list(a):
    st = load(); v = vms()
    if not st["phones"]: print("(chưa có máy nào)"); return
    for p in st["phones"]:
        print(f"{p['name']:<16} #{p['idx']:<3} cổng {p['port']:<6} {p['ram']}G/{p['cpu']}  hết hạn {fmt(parse_dt(p['expires']))}  {'chạy' if is_running(p['name'], p, v) else 'tắt'}")

def cmd_noop(a): print("(bản Windows: không cần lệnh này)")

def main():
    ap = argparse.ArgumentParser(); s = ap.add_subparsers(dest="c", required=True)
    c = s.add_parser("create"); c.add_argument("name"); c.add_argument("--ram", type=float, default=2); c.add_argument("--cpu", type=float, default=2)
    c.add_argument("--days", type=int, default=7); c.add_argument("--res", default="720x1280"); c.add_argument("--dpi", type=int, default=0)
    c.add_argument("--fps", type=int, default=30); c.add_argument("--nowait", action="store_true"); c.add_argument("--android", default="9")
    c.add_argument("--nogpu", action="store_true"); c.set_defaults(f=cmd_create)
    for nm, fn in (("delete", cmd_delete), ("stop", cmd_stop), ("start", cmd_start), ("restart", cmd_restart)):
        x = s.add_parser(nm); x.add_argument("name"); x.set_defaults(f=fn)
    r = s.add_parser("renew"); r.add_argument("name"); r.add_argument("--days", type=int, default=7); r.set_defaults(f=cmd_renew)
    z = s.add_parser("resize"); z.add_argument("name"); z.add_argument("--ram", type=float, default=0); z.add_argument("--cpu", type=float, default=0); z.set_defaults(f=cmd_resize)
    w = s.add_parser("waitboot"); w.add_argument("name"); w.add_argument("--noapps", action="store_true"); w.set_defaults(f=cmd_waitboot)
    g = s.add_parser("reconfig"); g.add_argument("name"); g.add_argument("--fps", type=int, default=0); g.add_argument("--res", default=""); g.set_defaults(f=cmd_reconfig)
    lr = s.add_parser("liveres"); lr.add_argument("name"); lr.add_argument("--res", required=True); lr.set_defaults(f=cmd_liveres)
    pi = s.add_parser("preinstall"); pi.add_argument("name", nargs="?", default=""); pi.add_argument("--all", action="store_true"); pi.add_argument("--force", action="store_true"); pi.set_defaults(f=cmd_preinstall)
    rf = s.add_parser("rootfix"); rf.add_argument("name", nargs="?", default=""); rf.add_argument("--all", action="store_true"); rf.add_argument("--force", action="store_true"); rf.set_defaults(f=cmd_rootfix)
    for nm, fn in (("connect-all", cmd_connect_all), ("reap", cmd_reap), ("perms", cmd_perms), ("capacity", cmd_capacity), ("list", cmd_list),
                   ("memfix", cmd_noop), ("boost", cmd_noop), ("gpuinfo", cmd_noop)):
        s.add_parser(nm).set_defaults(f=fn)
    a = ap.parse_args(); a.f(a)

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
