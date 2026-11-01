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
# Phiên bản Android khách chọn → image + bộ dịch ARM (image 12–14 dựng bằng android-test.sh build)
IMAGES = {"9": ("redroid/redroid:9.0.0_litegapps_houdini", "houdini"),        # dựng bằng: bash ~/android-versions.sh build
          "10": ("redroid/redroid:10.0.0_litegapps_houdini", "houdini"),
          "11": (IMAGE, "ndk"),
          "12": ("redroid/redroid:12.0.0_mindthegapps_ndk", "ndk"),
          "13": ("redroid/redroid:13.0.0_mindthegapps_houdini", "houdini"),
          "14": ("redroid/redroid:14.0.0_mindthegapps_houdini", "houdini")}
# Android 12 dùng bộ dịch ARM Houdini (Intel) thay libndk khi admin bật A12_ARM=houdini trong ~/cloudphone.conf
def _a12():
    try:
        for line in open(os.path.expanduser("~/cloudphone.conf"), encoding="utf-8"):
            if line.strip() == "A12_ARM=houdini": IMAGES["12"] = ("redroid/redroid:12.0.0_mindthegapps_houdini", "houdini")
    except OSError: pass
_a12()
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

# ===================== MÁY GPU (Android Emulator vẽ hình bằng card NVIDIA) =====================
# Máy mới Android 11 tự chạy bằng GPU khi card còn chỗ (GPU_PHONES trong ~/cloudphone.conf, 0 = tắt).
# Hết chỗ trên card → tự tạo kiểu thường (redroid, vẽ bằng CPU). Khách không phải chọn gì.
def _conf():
    c = {}
    try:
        for line in open(os.path.expanduser("~/cloudphone.conf"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); c[k.strip()] = v.strip()
    except OSError: pass
    return c
CONF = _conf()
GPU_MAX = int(CONF.get("GPU_PHONES", "0") or 0)          # số máy GPU tối đa trên card (GTX 1080 8GB: ~8)
GPU_MIN_VRAM = 700                                        # MB bộ nhớ card phải còn trống để thêm 1 máy
SDK = os.path.expanduser("~/android-sdk")
AVD_HOME = os.path.expanduser("~/.android/avd")
GPU_IMG = CONF.get("GPU_IMG", "system-images;android-30;google_apis_playstore;x86_64")
GPU_PORTS = range(5611, 5682, 2)                          # cổng adb máy GPU (console = cổng − 1); 5601 là máy thử
GPU_ENV = {**os.environ, "ANDROID_SDK_ROOT": SDK, "ANDROID_HOME": SDK, "ANDROID_AVD_HOME": AVD_HOME,
           "ANDROID_ADB_SERVER_PORT": "5038", "QT_QPA_PLATFORM": "offscreen", "DISPLAY": ":9"}

def sh(cmd, check=False, keep_sudo=False):
    if not keep_sudo: cmd = [c for c in cmd if c != "sudo"]   # chạy docker trực tiếp (user đã trong nhóm docker)
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

def rec(name, st=None):
    return next((x for x in (st or load())["phones"] if x["name"] == name), None)

def is_gpu(p): return bool(p) and p.get("kind") == "gpu"

def is_running(name, p=None):
    p = p or rec(name)
    if is_gpu(p):
        return sh(["systemctl", "is-active", "-q", f"cloudgpu@{name}"]).returncode == 0
    r = sh(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return (r.stdout or "").strip() == "true"

def ashell(name, script, port=None):
    """Chạy lệnh trong Android: máy thường qua docker exec (root), máy GPU qua adb shell."""
    p = rec(name)
    if is_gpu(p) or (p is None and port):
        serial = f"127.0.0.1:{port or p['port']}"
        if (sh(["docker", "exec", WSNAME, "adb", "-s", serial, "get-state"]).stdout or "").strip() != "device":
            adb_connect(port or p["port"])
        return sh(["docker", "exec", WSNAME, "adb", "-s", serial, "shell", script])
    return sh(["docker", "exec", name, "/system/bin/sh", "-c", script])

def gpu_used(st):
    return [x for x in st["phones"] if is_gpu(x)]

def gpu_vram_free():
    r = sh(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"])
    try: return int((r.stdout or "0").split()[0])
    except Exception: return 0

def gpu_ready():
    return (GPU_MAX > 0 and os.path.exists(os.path.join(SDK, "emulator", "emulator"))
            and os.path.exists("/tmp/.X11-unix/X9") and os.path.exists("/etc/systemd/system/cloudgpu@.service"))

def gpu_slot(st):
    """Còn chỗ trên card cho 1 máy GPU nữa không."""
    return gpu_ready() and len(gpu_used(st)) < GPU_MAX and gpu_vram_free() >= GPU_MIN_VRAM

def gpu_port(st):
    busy = {int(x["port"]) for x in st["phones"]}
    import socket
    for port in GPU_PORTS:
        if port in busy: continue
        ok = True
        for q in (port - 1, port):
            s_ = socket.socket()
            try: s_.bind(("127.0.0.1", q))
            except OSError: ok = False
            finally: s_.close()
        if ok: return port
    return None

def avd_dir(name): return os.path.join(AVD_HOME, name + ".avd")

def gpu_config(name, ram, cpu, res, dpi=0):
    cfg = os.path.join(avd_dir(name), "config.ini")
    w, h = res.split("x")
    want = {"hw.ramSize": str(int(float(ram) * 1024)), "hw.cpu.ncore": str(max(1, int(float(cpu)))),
            "hw.lcd.width": w, "hw.lcd.height": h, "hw.lcd.density": str(int(dpi) or auto_dpi(res)),
            "hw.gpu.enabled": "yes", "hw.gpu.mode": "host", "disk.dataPartition.size": "16G", "hw.keyboard": "yes",
            "fastboot.forceColdBoot": "yes", "hw.audioInput": "no", "hw.audioOutput": "no", "showDeviceFrame": "no",
            "hw.camera.back": "none", "hw.camera.front": "none", "vm.heapSize": "512"}
    lines, seen = [], set()
    for line in open(cfg, encoding="utf-8"):
        k = line.split("=", 1)[0].strip()
        if k in want: lines.append(f"{k}={want[k]}\n"); seen.add(k)
        else: lines.append(line)
    lines += [f"{k}={v}\n" for k, v in want.items() if k not in seen]
    open(cfg, "w", encoding="utf-8").writelines(lines)

def gpu_create(name, ram, cpu, res, dpi=0):
    am = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
    r = subprocess.run([am, "create", "avd", "-n", name, "-k", GPU_IMG, "-d", "pixel_4", "--force"],
                       input="no\n", capture_output=True, text=True, env=GPU_ENV)
    if r.returncode != 0 or not os.path.isdir(avd_dir(name)): return r
    gpu_config(name, ram, cpu, res, dpi)
    return gpu_svc("start", name)

def gpu_svc(action, name):
    """Máy GPU chạy như dịch vụ hệ thống cloudgpu@TÊN (khởi động lại server/dịch vụ khác không làm tắt máy)."""
    return sh(["sudo", "-n", "/usr/bin/systemctl", action, f"cloudgpu@{name}"], keep_sudo=True)

def gpu_wipe(name):
    import shutil
    shutil.rmtree(avd_dir(name), ignore_errors=True)
    try: os.remove(os.path.join(AVD_HOME, name + ".ini"))
    except OSError: pass

def cmd_gpurun(a):
    """(dịch vụ cloudgpu@TÊN gọi) — chạy emulator của máy GPU, giữ tiến trình ở tiền cảnh."""
    p = rec(a.name)
    if not is_gpu(p): print("Không phải máy GPU."); sys.exit(1)
    port = int(p["port"])
    emu = os.path.join(SDK, "emulator", "emulator")
    args = [emu, "-avd", a.name, "-port", str(port - 1), "-no-window", "-gpu", "host", "-feature", "-Vulkan",
            "-no-audio", "-no-boot-anim", "-no-snapshot", "-camera-back", "none", "-camera-front", "none",
            "-netdelay", "none", "-netspeed", "full"]
    os.execve(emu, args, GPU_ENV)

TUNE = (# tắt hẳn hiệu ứng chuyển cảnh → CPU không phải vẽ thêm
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
        # Trình duyệt bị MÀN ĐEN (máy ảo không có chip đồ hoạ): Chrome / WebView vẽ bằng CPU, Firefox dùng WebRender phần mềm
        "for f in /data/local/chrome-command-line /data/local/tmp/chrome-command-line /data/local/tmp/webview-command-line; do "
        "grep -q disable-gpu $f 2>/dev/null || echo '_ --disable-gpu --disable-gpu-compositing --disable-features=Vulkan' > $f 2>/dev/null; "
        "chmod 644 $f 2>/dev/null; done; "
        "for d in /data/data/org.mozilla.firefox/files/mozilla/*.default*; do [ -d \"$d\" ] || continue; "
        "grep -q webrender.software \"$d/user.js\" 2>/dev/null || { printf 'user_pref(\"gfx.webrender.software\", true);\\nuser_pref(\"layers.acceleration.disabled\", true);\\nuser_pref(\"gfx.canvas.accelerated\", false);\\n' >> \"$d/user.js\"; "
        "chown $(stat -c %u \"$d\"):$(stat -c %g \"$d\") \"$d/user.js\"; }; done 2>/dev/null; "
        # Cổng VPN: Android mở /dev/tun (docker chỉ có /dev/net/tun) → thiếu thì VPN báo "Failed to connect"
        "[ -c /dev/tun ] || { rm -f /dev/tun; mknod /dev/tun c 10 200; }; chmod 666 /dev/tun")
def tune(name):
    """Cho máy mượt hơn: tắt hiệu ứng, bật cửa sổ nổi, không ngủ đông/đóng băng app nền, cài APK không phải chờ quét, màn hình không tự tắt."""
    ashell(name, TUNE)
def _pre_dir():
    """Thư mục app cài sẵn: cạnh phonectl.py, hoặc ~/apk-cai-san, hoặc /home/*/apk-cai-san (dịch vụ chạy bằng user nào cũng tìm thấy)."""
    import glob
    for d in [os.path.join(os.path.dirname(os.path.abspath(__file__)), "apk-cai-san"), os.path.expanduser("~/apk-cai-san")] + sorted(glob.glob("/home/*/apk-cai-san")):
        if os.path.isdir(d): return d
    return os.path.expanduser("~/apk-cai-san")
PRE_DIR = _pre_dir()   # bỏ file .apk/.apkm vào đây → mọi máy mới tự cài sẵn (trình duyệt, Gmail…)
def preinstall(name, port, force=False):
    """Cài các APK trong ~/apk-cai-san vào máy (mỗi APK chỉ cài 1 lần / máy, lưu dấu trong /data của máy)."""
    try: apks = sorted(f for f in os.listdir(PRE_DIR) if f.lower().endswith((".apk", ".apks", ".apkm", ".xapk")))
    except OSError: print(f"   ! Không thấy thư mục app cài sẵn {PRE_DIR}"); return
    todo = []
    for f in apks:
        mark = "/data/local/tmp/.cp_pre_" + re.sub(r"[^\w.-]", "_", f)
        if not force and (ashell(name, f"test -f {mark} && echo y", port).stdout or "").strip() == "y":
            continue
        todo.append((f, mark))
    def one(fm):
        f, mark = fm
        r = install_file(port, os.path.join(PRE_DIR, f))
        if "Success" in (r.stdout or ""):
            ashell(name, f"touch {mark}", port); return f"   + Đã cài sẵn {f} cho {name}"
        return f"   ! Cài {f} cho {name} lỗi: {((r.stdout or '') + (r.stderr or ''))[-200:]}"
    if todo:                                  # cài song song (tối đa 3 app cùng lúc) → máy mới có đủ app nhanh hơn
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(3, len(todo))) as ex:
            for line in ex.map(one, todo): print(line)

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
# Cho phép TRÌNH DUYỆT / Gmail / app quản lý file cài APK (khách khỏi phải bật "Cài ứng dụng không rõ nguồn").
# ⚠️ Chỉ đổi khi chưa bật: Android TẮT app ngay khi quyền này bị thay đổi → trước đây đổi cho mọi app mỗi 5 phút
#    làm game/app của khách bị văng. Không bao giờ đụng vào app khác (game…).
BROWSERS = ("com.android.chrome org.mozilla.firefox com.brave.browser com.google.android.gm com.google.android.documentsui "
            "com.android.documentsui com.google.android.apps.nbu.files com.kiwibrowser.browser com.opera.browser "
            "com.microsoft.emmx mark.via.gp com.sec.android.app.sbrowser com.mi.android.globalFileexplorer com.UCMobile.intl")
PERM_SH = ("for p in " + BROWSERS + "; do pm path $p >/dev/null 2>&1 || continue; "
           "case \"$(appops get $p REQUEST_INSTALL_PACKAGES 2>/dev/null)\" in *allow*) ;; "
           "*) appops set $p REQUEST_INSTALL_PACKAGES allow >/dev/null 2>&1;; esac; done; true")
def grant_perms(name):
    ashell(name, PERM_SH)
    tune(name)          # giữ màn hình máy ảo luôn sáng (máy tạo trước bản này cũng được áp dụng)

def cmd_perms(a):
    """Mở quyền cài APK cho mọi máy đang chạy (chương trình nền tự gọi mỗi 5 phút → app khách cài sau cũng được mở)."""
    running = set((sh(["docker", "ps", "--format", "{{.Names}}"]).stdout or "").split())
    for p in load()["phones"]:
        if p["name"] in running or (is_gpu(p) and is_running(p["name"], p)): grant_perms(p["name"])

def cmd_preinstall(a):
    """Cài APK trong ~/apk-cai-san cho máy đang chạy: phonectl.py preinstall TÊN_MÁY  hoặc  --all"""
    st = load(); running = set((sh(["docker", "ps", "--format", "{{.Names}}"]).stdout or "").split())
    for p in st["phones"]:
        if (a.all and (p["name"] in running or (is_gpu(p) and is_running(p["name"], p)))) or p["name"] == a.name:
            if sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{p['port']}", "get-state"]).stdout.strip() != "device":
                adb_connect(p["port"])
            preinstall(p["name"], p["port"], a.force); grant_perms(p["name"])

def wait_boot(name, port, apps=True):
    """Chờ Android khởi động xong rồi mới kết nối (kết nối sớm → màn đen).
    apps=False: không chờ cài app sẵn (chương trình nền cài ngay sau, khách dùng máy được sớm hơn)."""
    t0 = time.time()
    gpu = is_gpu(rec(name))
    while time.time() - t0 < BOOT_WAIT + (120 if gpu else 0):
        r = ashell(name, "getprop sys.boot_completed", port) if gpu else sh(["docker", "exec", name, "/system/bin/getprop", "sys.boot_completed"])
        if (r.stdout or "").strip() == "1": break
        time.sleep(2)
    tune(name)
    if not is_gpu(rec(name)):
        try: su_install(name)                      # máy vừa bật lại → bật lại root daemon
        except Exception: pass
    time.sleep(1.5)
    for _ in range(8):
        adb_connect(port)
        r = sh(["docker", "exec", WSNAME, "adb", "-s", f"127.0.0.1:{port}", "get-state"])
        if (r.stdout or "").strip() == "device":
            if apps: preinstall(name, port)
            grant_perms(name); return True
        time.sleep(2)
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
    if not re.match(r"^\d+x\d+$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    av = android_of(a.android)
    # Android 11 + card còn chỗ → máy GPU. Máy GPU CHƯA root được → khi đã có bản root, máy mới ưu tiên bản ROOT
    # (muốn ưu tiên GPU hơn root: thêm GPU_OVER_ROOT=1 vào ~/cloudphone.conf)
    rooted = pick_image(IMAGES[av][0]) != IMAGES[av][0]
    gpu = av == "11" and not a.nogpu and (not rooted or gpu_rooted() or CONF.get("GPU_OVER_ROOT") == "1") and gpu_slot(st)
    port = gpu_port(st) if gpu else free_port(st)
    if gpu and not port: gpu, port = False, free_port(st)
    if not port: print("LỖI: hết cổng (tối đa 30 máy/node)."); sys.exit(3)
    if not SAFE.match(a.name): print("LỖI: tên máy chỉ gồm chữ, số, _ . -"); sys.exit(1)
    fps = clamp_fps(a.fps)
    sh(["docker", "rm", "-f", a.name])           # phòng còn container cũ trùng tên
    wipe_data(a.name); gpu_wipe(a.name)           # đảm bảo máy MỚI TINH, không dính dữ liệu cũ
    if not gpu and sh(["docker", "image", "inspect", IMAGES[av][0]]).returncode != 0:
        print(f"LỖI: máy chủ chưa có Android {av} (chạy: bash ~/android-test.sh build)"); sys.exit(4)
    exp = now() + datetime.timedelta(days=a.days)
    row = {"name": a.name, "port": port, "ram": a.ram, "cpu": a.cpu, "res": a.res, "fps": fps,
           "dpi": a.dpi, "android": av, "created": fmt(now()), "expires": exp.isoformat()}
    if gpu: row["kind"] = "gpu"
    st["phones"].append(row); save(st)             # lưu trước để dịch vụ máy GPU đọc được cấu hình
    r = gpu_create(a.name, a.ram, a.cpu, a.res, a.dpi) if gpu else docker_run(a.name, a.ram, a.cpu, port, a.res, fps, a.dpi, av)
    if r.returncode != 0:
        st = load(); st["phones"] = [x for x in st["phones"] if x["name"] != a.name]; save(st)
        if gpu: gpu_wipe(a.name)
        print("LỖI tạo máy:", (r.stderr or r.stdout).strip()[-400:]); sys.exit(4)
    # chờ boot xong rồi mới đăng ký vào ws-scrcpy (tránh màn đen) — bỏ qua nếu --nowait (chương trình nền tự chờ song song)
    ok = True if a.nowait else wait_boot(a.name, port)
    print(f"✅ Đã tạo máy '{a.name}'{' (GPU ⚡)' if gpu else ''}  | cổng {port} | RAM {a.ram}GB CPU {a.cpu} | hết hạn {fmt(exp)}")
    print("   Android đã khởi động xong, sẵn sàng điều khiển." if ok else "   CẢNH BÁO: máy chưa kết nối được trình điều khiển, sẽ tự thử lại.")

def clamp_fps(v):
    try: v = int(v)
    except Exception: v = 30
    return min(max(v, 10), 120)

def auto_dpi(res):
    w, h = (int(x) for x in res.split("x"))
    return max(160, min(640, round(min(w, h) / 2.25)))     # 720→320, 1080→480, 540→240

def mem_args(ram):
    """RAM cho máy: khách được đúng số GB đã thuê + 1GB dư cho hệ thống/luồng hình, kèm vùng đệm swap.
    Android trong container không biết giới hạn RAM → nếu chạm trần, lõi Android bị tắt → văng hết app, màn đen.
    Chừa dư + cho phép tràn sang swap thì máy chậm lại một chút thay vì văng."""
    r = float(ram)
    # swap cho mỗi máy tới 3 lần RAM (zram nhanh dùng trước, ổ cứng dùng sau) → đầy RAM thì chậm lại chứ không văng
    return [f"--memory={r + 1:g}g", f"--memory-swap={r * 4 + 2:g}g", f"--memory-reservation={r:g}g"]

HOUDINI = [x.replace("libndk_translation.so", "libhoudini.so") for x in ARM if not x.startswith("ro.ndk_translation")] \
          + ["ro.enable.native.bridge.exec64=1"]

def android_of(v):
    v = str(v or "11").split(".")[0]
    return v if v in IMAGES else "11"

def cpu_args(cpu):
    """CPU "tăng tốc" như UgPhone: máy ảo vẽ màn hình + nén hình bằng CPU (không có chip đồ hoạ riêng),
    nên giới hạn cứng đúng số nhân thuê làm máy chạm trần 100% → giật/trễ.
    → Mỗi máy được ƯU TIÊN đúng số nhân đã thuê (cpu-shares), và khi server rảnh được mượn thêm tới 3 lần (tối đa 16 nhân).
      Server đông máy thì chia theo tỉ lệ đã thuê, không máy nào giành của máy khác."""
    c = float(cpu)
    return [f"--cpus={min(c * 3, max(c, 16)):g}", f"--cpu-shares={int(c * 1024)}"]

_IMG_OK = {}
def image_ok(img):
    if img not in _IMG_OK: _IMG_OK[img] = sh(["docker", "image", "inspect", img]).returncode == 0
    return _IMG_OK[img]

def root_img(base):
    """Bản đã ROOT (Magisk) của image — dựng bằng: bash ~/cloudphone-root.sh build. Có thì máy mới tự dùng bản root."""
    return base + "_root"

def pick_image(base):
    # Bản Magisk làm HỎNG bộ dịch ARM (Roblox/app ARM văng) → chỉ dùng khi admin cố ý đặt ROOT=magisk.
    # Mặc định root bằng "cloudphone-su" (daemon, không sửa hệ thống) → xem su_install().
    return root_img(base) if CONF.get("ROOT", "1") == "magisk" and image_ok(root_img(base)) else base

def docker_run(name, ram, cpu, port, res, fps, dpi=0, android="11"):
    w, h = res.split("x")
    img, tr = IMAGES[android_of(android)]
    img = pick_image(img)                                   # có bản root → máy tự có root (Magisk)
    data = os.path.join(DATA_ROOT, name); os.makedirs(data, exist_ok=True)
    return sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", name,
               *mem_args(ram), *cpu_args(cpu),
               "-v", f"{data}:/data", "-p", f"127.0.0.1:{port}:5555", img,   # chỉ mở trong server, không lộ ra internet
               f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
               f"androidboot.redroid_dpi={int(dpi) or auto_dpi(res)}", f"androidboot.redroid_fps={fps}"] + (HOUDINI if tr == "houdini" else ARM))

def cmd_boost(a):
    """Áp dụng ngay chế độ CPU tăng tốc cho mọi máy đang có (không cần khởi động lại máy)."""
    st = load(); n = 0
    for p in st["phones"]:
        if is_gpu(p): continue
        r = sh(["docker", "update", *cpu_args(p.get("cpu", 2)), p["name"]])
        if r.returncode == 0: n += 1; print(f"✓ {p['name']}: thuê {p.get('cpu', 2):g} nhân → được dùng tới {cpu_args(p.get('cpu', 2))[0].split('=')[1]} nhân khi server rảnh")
        else: print(f"! {p['name']}: {(r.stderr or r.stdout).strip()[:120]}")
    print(f"✅ Đã tăng tốc {n} máy")

# ===================== ROOT (Magisk) =====================
GPU_ROOT_MARK = os.path.expanduser("~/.cloudphone-gpu-rooted")      # cloudphone-root.sh gpu ghi khi đã root ảnh Android của máy GPU
def gpu_rooted(): return os.path.exists(GPU_ROOT_MARK)
MAGISK_PKGS = ("com.topjohnwu.magisk", "io.github.huskydg.magisk", "io.github.vvb2060.magisk")
def magisk_apk():
    for f in (os.path.expanduser("~/.cache/cloudphone-root/magisk/system/etc/init/magisk/magisk.apk"), os.path.expanduser("~/rootAVD/Magisk.zip")):
        if os.path.isfile(f): return f
    return None
# cho phép root sâu: ADB + app đều dùng được root, và CẤP SẴN quyền root cho mọi app khách cài (tool khỏi phải bấm "Cho phép")
GRANT_SH = ("M=$(command -v magisk || ls /sbin/magisk /debug_ramdisk/magisk 2>/dev/null | head -1); [ -n \"$M\" ] || exit 0; "
            "$M --sqlite \"REPLACE INTO settings (key,value) VALUES('root_access',3)\" >/dev/null 2>&1; "
            "for u in 2000 $(cmd package list packages -3 -U 2>/dev/null | sed -n 's/.*uid:\\([0-9]*\\).*/\\1/p' | sort -u); do "
            "$M --sqlite \"REPLACE INTO policies (uid,policy,until,logging,notification) VALUES($u,2,0,1,1)\" >/dev/null 2>&1; done; echo GRANTED")
# ----- ROOT kiểu daemon (cloudphone-su): su + cpsud chép vào máy, KHÔNG đổi image, KHÔNG khởi động lại, KHÔNG đụng bộ dịch ARM -----
SU_DIR = os.path.expanduser("~/cloudphone-su")
def su_ready(): return os.path.isfile(os.path.join(SU_DIR, "su")) and os.path.isfile(os.path.join(SU_DIR, "cpsud"))
def su_works(name):
    r = sh(["docker", "exec", "--user", "2000", name, "/system/xbin/su", "-c", "id -u"])
    return (r.stdout or "").strip().endswith("0") and r.returncode == 0
def su_install(name):
    """Cài / bật lại root daemon trong máy thường. Trả về True nếu app trong máy dùng được su."""
    if not su_ready() or CONF.get("ROOT", "1") == "0": return False
    import hashlib
    want = hashlib.md5(open(os.path.join(SU_DIR, "su"), "rb").read()).hexdigest() + hashlib.md5(open(os.path.join(SU_DIR, "cpsud"), "rb").read()).hexdigest()
    have = (sh(["docker", "exec", name, "/system/bin/sh", "-c", "md5sum /system/xbin/su /system/xbin/cpsud 2>/dev/null | cut -d' ' -f1 | tr -d '\\n'"]).stdout or "").strip()
    if have != want:
        sh(["docker", "exec", name, "/system/bin/sh", "-c", "mkdir -p /system/xbin; pkill -x cpsud; rm -f /system/xbin/su /system/xbin/cpsud"])
        for f in ("su", "cpsud"): sh(["docker", "cp", os.path.join(SU_DIR, f), f"{name}:/system/xbin/{f}"])
        sh(["docker", "exec", name, "/system/bin/sh", "-c", "chown 0:0 /system/xbin/su /system/xbin/cpsud; chmod 755 /system/xbin/su /system/xbin/cpsud; "
            "[ -e /system/bin/su ] || ln -s /system/xbin/su /system/bin/su 2>/dev/null; true"])
    if not su_works(name):
        sh(["docker", "exec", name, "/system/bin/sh", "-c", "pkill -x cpsud; /system/xbin/cpsud"])
        time.sleep(0.5)
    return su_works(name)
def su_remove(name):
    sh(["docker", "exec", name, "/system/bin/sh", "-c", "pkill -x cpsud; rm -f /system/xbin/su /system/xbin/cpsud /dev/socket/cpsu; [ -L /system/bin/su ] && rm -f /system/bin/su; true"])

def root_state(p):
    """'root' = có Magisk chạy · 'image' = đã dùng bản root nhưng Android chưa lên · 'no' = chưa root."""
    if is_gpu(p):
        o = (ashell(p["name"], "command -v su; command -v magisk").stdout or "")   # máy GPU (bản Play) gốc KHÔNG có su → có su = đã có Magisk
        return "root" if "/su" in o or "magisk" in o else "no"
    img = (sh(["docker", "inspect", "-f", "{{.Config.Image}}", p["name"]]).stdout or "").strip()
    if not img.endswith("_root"): return "root" if su_works(p["name"]) else "no"
    o = (sh(["docker", "exec", p["name"], "/system/bin/sh", "-c", "ls /sbin/magisk /debug_ramdisk/magisk 2>/dev/null"]).stdout or "")
    return "root" if "magisk" in o else "image"

def root_one(p, force=False):
    """Kiểm tra + tự root 1 máy. In 1 dòng kết quả: ROOT_OK / ROOT_WAIT / ROOT_NO."""
    name = p["name"]
    if not is_running(name, p): print(f"ROOT_SKIP {name}: máy đang tắt"); return
    stt = root_state(p)
    if stt == "no" and is_gpu(p) and not force:   # máy GPU cần khởi động lại để có root → tối đa 1 lần / 6 giờ
        st = load(); r0 = rec(name, st)
        if r0 and time.time() - float(r0.get("root_try", 0)) < 6 * 3600: print(f"ROOT_WAIT {name}: vừa thử root, chờ lần sau"); return
        if r0: r0["root_try"] = time.time(); save(st)
    if is_gpu(p):
        if stt != "root":
            if gpu_rooted():
                gpu_svc("restart", name); wait_boot(name, p["port"], apps=False); stt = root_state(p)
            if stt != "root": print(f"ROOT_NO {name}: máy GPU chưa root được (admin chạy: bash ~/cloudphone-root.sh gpu)"); return
        apk = magisk_apk()
        has = (ashell(name, "pm list packages 2>/dev/null").stdout or "")
        if apk and not any(k in has for k in MAGISK_PKGS):
            import shutil; tmp = "/tmp/magisk-app.apk"; shutil.copyfile(apk, tmp); install_file(p["port"], tmp)
        print(f"ROOT_OK {name}: đã root (Magisk) — tool xin quyền thì bấm Cho phép"); return
    img = (sh(["docker", "inspect", "-f", "{{.Config.Image}}", name]).stdout or "").strip()
    if img.endswith("_root") and CONF.get("ROOT", "1") != "magisk":   # máy đang dùng bản Magisk (làm văng app ARM) → trả về bản thường
        recreate_base(p); print(f"   {name}: đã bỏ bản Magisk (gây văng app), chuyển sang root an toàn")
    if not su_ready(): print(f"ROOT_NO {name}: server chưa có bộ root (admin chạy: bash ~/cloudphone-root.sh su)"); return
    ok = su_install(name)
    if not ok:
        time.sleep(3); ok = su_install(name)
    print(f"ROOT_OK {name}: đã root — tool gọi su là chạy bằng quyền root" if ok else f"ROOT_WAIT {name}: chưa bật được root, thử lại sau 1 phút")

def cmd_rootfix(a):
    """rootfix TÊN [--force]  |  rootfix --all : máy nào chưa root → tự root (máy thường: tạo lại cùng dữ liệu; máy GPU: khởi động lại)."""
    if CONF.get("ROOT", "1") == "0":            # admin đã tắt root (VD root làm app ARM văng) → không root máy nào, kể cả khi khách bấm
        print(f"ROOT_NO {a.name or 'all'}: chức năng root đang tạm tắt để bảo trì"); return
    for p in load()["phones"]:
        if (a.all or p["name"] == a.name):
            try: root_one(p, force=a.force)
            except Exception as e: print(f"ROOT_NO {p['name']}: {e}")

def recreate_base(p):
    """Tạo lại container bằng image gốc (không Magisk), giữ nguyên dữ liệu."""
    was_on = is_running(p["name"], p)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"]); rm_container(p["name"])
    w, h = p.get("res", "720x1280").split("x"); img, tr = IMAGES[android_of(p.get("android", "11"))]
    data = os.path.join(DATA_ROOT, p["name"])
    r = sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", p["name"], *mem_args(p["ram"]), *cpu_args(p["cpu"]),
            "-v", f"{data}:/data", "-p", f"127.0.0.1:{p['port']}:5555", img, f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
            f"androidboot.redroid_dpi={int(p.get('dpi', 0)) or auto_dpi(p.get('res', '720x1280'))}", f"androidboot.redroid_fps={p.get('fps', 30)}"] + (HOUDINI if tr == "houdini" else ARM))
    if r.returncode != 0: raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    if not was_on: sh(["docker", "stop", p["name"]])
    else: wait_boot(p["name"], p["port"], apps=False)

def cmd_unroot(a):
    """Trả máy về bản KHÔNG root (giữ nguyên dữ liệu) — dùng khi root làm app văng."""
    st = load(); n = 0
    for p in st["phones"]:
        if is_gpu(p) or (a.name and p["name"] != a.name): continue
        base = IMAGES[android_of(p.get("android", "11"))][0]
        cur = (sh(["docker", "inspect", "-f", "{{.Config.Image}}", p["name"]]).stdout or "").strip()
        if is_running(p["name"], p): su_remove(p["name"])
        if cur == base: print(f"✓ {p['name']}: đã gỡ root"); continue
        try: recreate_base(p); n += 1; print(f"✓ {p['name']}: đã trả về bản KHÔNG root"); continue
        except Exception as e: print(f"! {p['name']}: LỖI {e}"); continue
        was_on = is_running(p["name"], p)
        sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"]); rm_container(p["name"])
        w, h = p.get("res", "720x1280").split("x"); img, tr = IMAGES[android_of(p.get("android", "11"))]
        data = os.path.join(DATA_ROOT, p["name"])
        r = sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", p["name"], *mem_args(p["ram"]), *cpu_args(p["cpu"]),
                "-v", f"{data}:/data", "-p", f"127.0.0.1:{p['port']}:5555", img, f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
                f"androidboot.redroid_dpi={int(p.get('dpi', 0)) or auto_dpi(p.get('res', '720x1280'))}", f"androidboot.redroid_fps={p.get('fps', 30)}"] + (HOUDINI if tr == "houdini" else ARM))
        if r.returncode != 0: print(f"! {p['name']}: LỖI {(r.stderr or r.stdout).strip()[:200]}"); continue
        if not was_on: sh(["docker", "stop", p["name"]])
        else: wait_boot(p["name"], p["port"], apps=False)
        n += 1; print(f"✓ {p['name']}: đã trả về bản KHÔNG root")
    print(f"✅ Xong — {n} máy")

def cmd_crashes(a):
    """Đọc lỗi văng app (bộ nhớ 'crash' của Android) trong từng máy đang chạy."""
    for p in load()["phones"]:
        if (a.name and p["name"] != a.name) or not is_running(p["name"], p): continue
        r = ashell(p["name"], "logcat -d -b crash 2>/dev/null | grep -E 'FATAL|Process:|Exception|Error|signal|Abort|backtrace|#0[0-4] ' | tail -25")
        print(f"===== {p['name']} (Android {p.get('android', '11')}{', GPU' if is_gpu(p) else ''})"); print((r.stdout or "").strip() or "   (không có lỗi văng nào)")

def cmd_rootall(a):
    """Chuyển máy đang có sang bản ROOT: tạo lại container với CÙNG dữ liệu (app, tài khoản giữ nguyên), máy khởi động lại ~1 phút."""
    st = load(); n = 0
    for p in st["phones"]:
        if is_gpu(p) or (a.name and p["name"] != a.name): continue
        base = IMAGES[android_of(p.get("android", "11"))][0]
        cur = (sh(["docker", "inspect", "-f", "{{.Config.Image}}", p["name"]]).stdout or "").strip()
        if not image_ok(root_img(base)): print(f"! {p['name']}: chưa có bản root của Android {p.get('android', '11')} (chạy: bash ~/cloudphone-root.sh build)"); continue
        if cur == root_img(base): print(f"✓ {p['name']}: đã root sẵn"); continue
        was_on = is_running(p["name"], p)
        sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
        rm_container(p["name"])
        r = docker_run(p["name"], p["ram"], p["cpu"], p["port"], p.get("res", "720x1280"), p.get("fps", 30), p.get("dpi", 0), p.get("android", "11"))
        if r.returncode != 0: print(f"! {p['name']}: LỖI {(r.stderr or r.stdout).strip()[:200]}"); continue
        if not was_on: sh(["docker", "stop", p["name"]])          # máy đang tạm dừng (hết hạn) → giữ tắt
        else: wait_boot(p["name"], p["port"], apps=False)
        n += 1; print(f"✓ {p['name']}: đã chuyển sang bản ROOT" + ("" if was_on else " (đang tạm dừng)"))
    print(f"✅ Xong — {n} máy vừa được root")

def cmd_reconfig(a):
    """Đổi FPS / độ phân giải: tạo lại container với CÙNG thư mục dữ liệu → giữ nguyên app, tài khoản."""
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    res = a.res or p.get("res") or "720x1280"
    if not re.match(r"^\d{3,4}x\d{3,4}$", res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    fps = clamp_fps(a.fps if a.fps else p.get("fps", 30))
    dpi = p.get("dpi", 0) if res == p.get("res") else 0
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    if is_gpu(p):
        gpu_svc("stop", a.name); gpu_config(a.name, p["ram"], p["cpu"], res, dpi); r = gpu_svc("start", a.name)
    else:
        rm_container(a.name)                       # chỉ xoá container, KHÔNG xoá dữ liệu
        r = docker_run(a.name, p["ram"], p["cpu"], p["port"], res, fps, dpi, p.get("android", "11"))
    if r.returncode != 0:
        print("LỖI:", (r.stderr or r.stdout).strip()); sys.exit(4)
    p["res"], p["fps"], p["dpi"] = res, fps, dpi; save(st)
    ok = wait_boot(a.name, p["port"])
    s = f"127.0.0.1:{p['port']}"                 # bỏ kích thước đổi nhanh trước đó → dùng đúng cấu hình mới
    sh(["docker", "exec", WSNAME, "adb", "-s", s, "shell", "wm", "size", "reset"])
    sh(["docker", "exec", WSNAME, "adb", "-s", s, "shell", "wm", "density", "reset"])
    print(f"✅ '{a.name}' → {res} · {fps} FPS" + ("" if ok else " (đang kết nối lại)"))

def cmd_liveres(a):
    """Đổi độ phân giải NGAY khi máy đang chạy (wm size/density) — app đang mở KHÔNG bị tắt."""
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    if not re.match(r"^\d{3,4}x\d{3,4}$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    dpi = auto_dpi(a.res)
    s = f"127.0.0.1:{p['port']}"
    r1 = sh(["docker", "exec", WSNAME, "adb", "-s", s, "shell", "wm", "size", a.res])
    r2 = sh(["docker", "exec", WSNAME, "adb", "-s", s, "shell", "wm", "density", str(dpi)])
    if r1.returncode or r2.returncode:
        print("LỖI:", ((r1.stderr or "") + (r2.stderr or "")).strip()); sys.exit(4)
    p["res"], p["dpi"] = a.res, dpi; save(st)
    print(f"✅ '{a.name}' → {a.res} (dpi {dpi}) — không khởi động lại")

def cmd_memfix(a):
    """Nới RAM cho các máy đang chạy (không cần khởi động lại) + báo máy nào từng bị tắt vì hết RAM."""
    st = load()
    running = set((sh(["docker", "ps", "--format", "{{.Names}}"]).stdout or "").split())
    for p in st["phones"]:
        if p["name"] not in running or is_gpu(p): continue
        r = sh(["docker", "update"] + mem_args(p["ram"]) + [p["name"]])
        info = sh(["docker", "inspect", "-f", "{{.State.OOMKilled}} {{.RestartCount}} {{.State.StartedAt}}", p["name"]]).stdout.strip()
        print(f"   {'✓' if r.returncode == 0 else '!'} {p['name']} RAM {p['ram']}GB (+1GB dư)  | OOM/restart/bật lúc: {info}"
              + ("" if r.returncode == 0 else "  LỖI: " + (r.stderr or "").strip()[:120]))

def cmd_waitboot(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    ok = wait_boot(a.name, p["port"], apps=not a.noapps)
    print("Android đã khởi động xong." if ok else "Chưa kết nối được, sẽ tự thử lại."); sys.exit(0 if ok else 5)

def rm_container(name):
    sh(["sudo", "docker", "rm", "-f", name])

def cmd_delete(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    if is_gpu(p): gpu_svc("stop", a.name); gpu_wipe(a.name)
    else: rm_container(a.name); wipe_data(a.name)
    st["phones"] = [x for x in st["phones"] if x["name"] != a.name]; save(st)
    print(f"🗑️  Đã xoá máy '{a.name}' và toàn bộ dữ liệu.")

def cmd_stop(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    gpu_svc("stop", a.name) if is_gpu(p) else sh(["docker", "stop", a.name])
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
        if serial in online or not is_running(p["name"], p): continue
        sh(["docker", "exec", WSNAME, "adb", "disconnect", serial])
        adb_connect(p["port"]); n += 1
        print("🔌 nối lại", p["name"], serial)
    if n == 0: print("Tất cả máy đã kết nối.")

def start_phone(p):
    return gpu_svc("start", p["name"]) if is_gpu(p) else sh(["docker", "start", p["name"]])

def cmd_start(a):
    p = rec(a.name)
    if not p: print("Không có máy tên này."); sys.exit(1)
    r = start_phone(p); print("✅ Đã bật" if r.returncode == 0 else "LỖI: " + (r.stderr or r.stdout).strip()); sys.exit(r.returncode and 4)

def cmd_restart(a):
    """Khởi động lại máy (cả máy thường lẫn máy GPU) — trang điều khiển gọi khi khách bấm 'Khởi động lại'."""
    p = rec(a.name)
    if not p: print("Không có máy tên này."); sys.exit(1)
    sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    r = gpu_svc("restart", a.name) if is_gpu(p) else sh(["docker", "restart", a.name])
    print("✅ Đã khởi động lại" if r.returncode == 0 else "LỖI: " + (r.stderr or r.stdout).strip()); sys.exit(r.returncode and 4)

def cmd_gpuinfo(a):
    st = load(); g = gpu_used(st)
    print(f"Máy GPU: {len(g)}/{GPU_MAX} · card còn trống {gpu_vram_free()} MB · sẵn sàng: {'có' if gpu_ready() else 'CHƯA (chạy: sudo bash ~/cloudphone-gpu.sh install)'}")
    for p in g: print(f"   {p['name']}  cổng {p['port']}  {'đang chạy' if is_running(p['name'], p) else 'đang tắt'}")

def cmd_renew(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    base = parse_dt(p["expires"])
    if base < now(): base = now()
    base += datetime.timedelta(days=a.days)
    p["expires"] = base.isoformat(); save(st)
    if not is_running(a.name, p):                 # máy đang tạm dừng vì hết hạn → bật lại
        start_phone(p); wait_boot(a.name, p["port"])
    print(f"🔄 '{a.name}' gia hạn +{a.days} ngày → hết hạn {fmt(base)}")

def cmd_list(a):
    st = load()
    if not st["phones"]: print("(chưa có máy nào)"); return
    print(f"{'TÊN':<16}{'CỔNG':<7}{'RAM':<6}{'CPU':<5}{'HẾT HẠN':<18}{'CÒN'}")
    for p in st["phones"]:
        exp = parse_dt(p["expires"])
        left = exp - now(); d = left.days; hrs = int(left.total_seconds()//3600)
        status = f"{d}n" if d >= 0 else "HẾT HẠN"
        print(f"{p['name'] + (' ⚡' if is_gpu(p) else ''):<16}{p['port']:<7}{str(p['ram'])+'G':<6}{str(p['cpu']):<5}{fmt(exp):<18}{status}")

def cmd_reap(a):
    st = load(); n = 0
    for p in list(st["phones"]):
        exp = parse_dt(p["expires"])
        if exp + datetime.timedelta(minutes=GRACE_MIN) < now():
            sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
            if is_gpu(p): gpu_svc("stop", p["name"]); gpu_wipe(p["name"])
            else: rm_container(p["name"]); wipe_data(p["name"])
            st["phones"] = [x for x in st["phones"] if x["name"] != p["name"]]; n += 1
            print(f"🗑️  Hết hạn quá {GRACE_MIN} phút → xoá '{p['name']}' + dữ liệu")
        elif exp < now() and is_running(p["name"], p):
            sh(["docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
            gpu_svc("stop", p["name"]) if is_gpu(p) else sh(["docker", "stop", p["name"]])
            print(f"⏸  Hết hạn → tạm dừng '{p['name']}' (còn {GRACE_MIN} phút để gia hạn)")
    save(st)
    print(f"Xong. Đã xoá {n} máy.")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create"); c.add_argument("name"); c.add_argument("--ram", type=float, default=2); c.add_argument("--cpu", type=float, default=2); c.add_argument("--days", type=int, default=7); c.add_argument("--res", default="720x1280"); c.add_argument("--dpi", type=int, default=0); c.add_argument("--fps", type=int, default=30); c.add_argument("--nowait", action="store_true"); c.add_argument("--android", default="11"); c.add_argument("--nogpu", action="store_true"); c.set_defaults(f=cmd_create)
    sub.add_parser("list").set_defaults(f=cmd_list)
    d = sub.add_parser("delete"); d.add_argument("name"); d.set_defaults(f=cmd_delete)
    r = sub.add_parser("renew"); r.add_argument("name"); r.add_argument("--days", type=int, default=7); r.set_defaults(f=cmd_renew)
    sub.add_parser("reap").set_defaults(f=cmd_reap)
    s = sub.add_parser("stop"); s.add_argument("name"); s.set_defaults(f=cmd_stop)
    sub.add_parser("connect-all").set_defaults(f=cmd_connect_all)
    w = sub.add_parser("waitboot"); w.add_argument("name"); w.add_argument("--noapps", action="store_true"); w.set_defaults(f=cmd_waitboot)
    g = sub.add_parser("reconfig"); g.add_argument("name"); g.add_argument("--fps", type=int, default=0); g.add_argument("--res", default=""); g.set_defaults(f=cmd_reconfig)
    sub.add_parser("capacity").set_defaults(f=cmd_capacity)
    sub.add_parser("perms").set_defaults(f=cmd_perms)
    sub.add_parser("memfix").set_defaults(f=cmd_memfix)
    sub.add_parser("boost").set_defaults(f=cmd_boost)
    for nm, fn in (("start", cmd_start), ("restart", cmd_restart), ("gpurun", cmd_gpurun)):
        x = sub.add_parser(nm); x.add_argument("name"); x.set_defaults(f=fn)
    sub.add_parser("gpuinfo").set_defaults(f=cmd_gpuinfo)
    for nm, fn in (("unroot", cmd_unroot), ("crashes", cmd_crashes)):
        x = sub.add_parser(nm); x.add_argument("name", nargs="?", default=""); x.set_defaults(f=fn)
    ra = sub.add_parser("rootall"); ra.add_argument("name", nargs="?", default=""); ra.set_defaults(f=cmd_rootall)
    rf = sub.add_parser("rootfix"); rf.add_argument("name", nargs="?", default=""); rf.add_argument("--all", action="store_true"); rf.add_argument("--force", action="store_true"); rf.set_defaults(f=cmd_rootfix)
    lr = sub.add_parser("liveres"); lr.add_argument("name"); lr.add_argument("--res", required=True); lr.set_defaults(f=cmd_liveres)
    pi = sub.add_parser("preinstall"); pi.add_argument("name", nargs="?", default=""); pi.add_argument("--all", action="store_true"); pi.add_argument("--force", action="store_true"); pi.set_defaults(f=cmd_preinstall)
    a = ap.parse_args()
    if not getattr(a, "cmd", None): ap.print_help(); sys.exit(0)
    a.f(a)

if __name__ == "__main__":
    main()
