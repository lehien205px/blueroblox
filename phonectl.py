#!/usr/bin/env python3
# phonectl.py — Quản lý máy Android (redroid) trên server: tạo/xoá/gia hạn + chặn 95% tài nguyên.
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
#   capacity                                                : xem tài nguyên đã dùng / ngưỡng 95%
import json, os, sys, subprocess, time, argparse, datetime, re

STATE = os.path.expanduser("~/redroid/phones.json")
DATA_ROOT = os.path.expanduser("~/redroid")
IMAGE = "redroid/redroid:11.0.0_gapps_ndk"
# Phiên bản Android khách chọn → image + bộ dịch ARM (image 12–14 dựng bằng android-test.sh build)
IMAGES = {"9": ("redroid/redroid:9.0.0_gapps_houdini", "houdini"),        # dựng bằng: bash ~/android-versions.sh build
          "10": ("redroid/redroid:10.0.0_gapps_houdini", "houdini"),
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
try: LIMIT_PCT = max(50, min(100, int(CONF.get("LIMIT_PCT", "95"))))   # ngưỡng chặn (% tài nguyên máy chủ), mặc định 95
except ValueError: LIMIT_PCT = 95
# CPU DÙNG CHUNG: máy ảo không giữ chết nhân (chia theo cpu-shares) → cho cấp vượt số nhân thật CPU_OVER lần,
# chỉ ngừng nhận máy mới khi CPU THẬT trung bình 5 phút ≥ CPU_BUSY_PCT %. RAM vẫn giới hạn theo cấu hình (RAM không chia chung được).
try: CPU_OVER = max(1.0, min(10.0, float(CONF.get("CPU_OVER", "4"))))
except ValueError: CPU_OVER = 4.0
try: CPU_BUSY = max(30, min(100, int(CONF.get("CPU_BUSY_PCT", "85"))))
except ValueError: CPU_BUSY = 85
# RAM DÙNG CHUNG: giới hạn RAM của mỗi máy chỉ là "trần", RAM máy không dùng tới thì máy khác dùng.
# → cho cấp vượt RAM_OVER lần, chỉ ngừng nhận máy mới khi RAM THẬT đang dùng ≥ RAM_BUSY_PCT %.
try: RAM_OVER = max(1.0, min(4.0, float(CONF.get("RAM_OVER", "2"))))
except ValueError: RAM_OVER = 2.0
try: RAM_BUSY = max(30, min(98, int(CONF.get("RAM_BUSY_PCT", "85"))))
except ValueError: RAM_BUSY = 85
# Ổ CỨNG DÙNG CHUNG: máy chỉ chiếm đúng phần dữ liệu đã ghi → không giới hạn theo máy,
# chỉ ngừng nhận máy mới khi ổ chứa dữ liệu máy đã dùng ≥ DISK_BUSY_PCT % (ổ đầy 100% thì MỌI máy treo).
try: DISK_BUSY = max(50, min(98, int(CONF.get("DISK_BUSY_PCT", "90"))))
except ValueError: DISK_BUSY = 90
def disk_real():
    try:
        import shutil as _sh; d = _sh.disk_usage(DATA_ROOT); return 100.0 * d.used / d.total, d.free / 1073741824
    except Exception: return None, None
def ram_real():
    """% RAM thật đang dùng (theo MemAvailable)."""
    try:
        m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return 100.0 * (1 - m["MemAvailable"] / m["MemTotal"])
    except Exception: return None
CPU_NOW = os.path.expanduser("~/redroid/cpu_now.json")      # cloudphoned ghi % CPU thật trung bình 5 phút
def cpu_real():
    try:
        d = json.load(open(CPU_NOW))
        return float(d["avg5"]) if time.time() - float(d.get("ts", 0)) < 600 else None
    except Exception: return None
GPU_MAX = int(CONF.get("GPU_PHONES", "0") or 0)          # 0 = tắt máy GPU; số lớn (999) = không giới hạn số máy, chỉ dừng khi card hết bộ nhớ
GPU_MIN_VRAM = int(CONF.get("GPU_MIN_VRAM", "400") or 400)  # MB bộ nhớ card phải còn trống để thêm 1 máy (card thật sự đầy mới dừng)
SDK = os.path.expanduser("~/android-sdk")
AVD_HOME = os.path.expanduser("~/.android/avd")
GPU_IMG = CONF.get("GPU_IMG", "system-images;android-30;google_apis_playstore;x86_64")
# Máy GPU theo bản Android (máy ảo Google chạy bằng GTX 1080, đã gắn root sâu bằng: bash ~/gpu-phone-test.sh rootboot)
GPU_IMGS = {"10": "system-images;android-29;google_apis_playstore;x86",
            "11": GPU_IMG,
            "12": "system-images;android-31;google_apis_playstore;x86_64"}
GPU_ONLY = {"9", "10"}          # redroid 9/10 không chạy được game/Chrome → các bản này CHỈ tạo bằng GPU
def gpu_img_ok(av):
    img = GPU_IMGS.get(av)
    return bool(img) and os.path.isdir(os.path.join(SDK, *img.split(";")))
GPU_PORTS = range(5611, 5698, 2)                          # cổng adb máy GPU (console = cổng − 1); 5601 là máy thử
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
    return tr * LIMIT_PCT / 100.0 * RAM_OVER, tc * CPU_OVER, tr, tc

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

GPU_RAM_RESERVE = float(CONF.get("GPU_RAM_RESERVE", "8") or 8)   # GB RAM thật luôn chừa lại cho hệ thống
GPU_FALLBACK = CONF.get("GPU_FALLBACK", "0") == "1"            # 1 = card đầy thì tạo máy thường (redroid); 0 = báo HẾT HÀNG
def ram_avail_gb():
    try:
        m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return m["MemAvailable"] / 1048576
    except Exception: return None

def gpu_full_reason(st, ram=0):
    """Lý do KHÔNG thêm được máy GPU (chuỗi rỗng = còn chỗ)."""
    if not gpu_ready(): return "máy GPU chưa sẵn sàng"
    if len(gpu_used(st)) >= GPU_MAX: return f"đủ {GPU_MAX} máy GPU"
    v = gpu_vram_free()
    if v < GPU_MIN_VRAM: return f"card đồ hoạ đầy (còn {v} MB)"
    ra = ram_avail_gb()   # máy GPU là máy ảo → chiếm RAM thật, không dùng chung như redroid
    if ra is not None and ra - (float(ram) + 1) < GPU_RAM_RESERVE:
        return f"RAM thật còn {ra:.0f}GB, máy {ram}GB cần thêm {float(ram) + 1:g}GB và chừa {GPU_RAM_RESERVE:g}GB"
    return ""

def gpu_slot(st, ram=0):
    """Còn chỗ trên card (và RAM thật) cho 1 máy GPU nữa không."""
    return not gpu_full_reason(st, ram)

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

def gpu_create(name, ram, cpu, res, dpi=0, av="11"):
    am = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
    r = subprocess.run([am, "create", "avd", "-n", name, "-k", GPU_IMGS.get(av, GPU_IMG), "-d", "pixel_4", "--force"],
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

def for_sdk(fname, path, sdk):
    """App trong kho có chạy được trên máy SDK này không. File tên có '-sdkNN-' (lấy riêng cho Android cũ) chỉ dành cho đúng máy SDK đó;
    file thường bị ẩn trên máy cũ nếu cần Android mới hơn, và nhường chỗ cho bản '-sdkNN-' cùng tên nếu có."""
    if not sdk: return True
    m = re.search(r"-sdk(\d+)-", fname)
    if m: return int(m.group(1)) == sdk
    return min_sdk(path) <= sdk

def preinstall(name, port, force=False):
    """Cài các APK trong ~/apk-cai-san vào máy (mỗi APK chỉ cài 1 lần / máy, lưu dấu trong /data của máy)."""
    try: apks = sorted(f for f in os.listdir(PRE_DIR) if f.lower().endswith((".apk", ".apks", ".apkm", ".xapk")))
    except OSError: print(f"   ! Không thấy thư mục app cài sẵn {PRE_DIR}"); return
    todo = []
    try: sdk = int((ashell(name, "getprop ro.build.version.sdk", port).stdout or "").strip() or 0)
    except ValueError: sdk = 0
    for f in apks:
        mark = "/data/local/tmp/.cp_pre_" + re.sub(r"[^\w.-]", "_", f)
        if not for_sdk(f, os.path.join(PRE_DIR, f), sdk):
            continue                              # app cần Android mới hơn máy này (VD Chrome mới không chạy Android 9) → bỏ qua
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

PLAY_APK = os.path.expanduser("~/apk-gpu/Phonesky.apk")
def gpu_play_fix(port):
    """CH Play gốc của bản Android 11 quá cũ → mở là văng ("Google Play Store keeps stopping").
    Có ~/apk-gpu/Phonesky.apk (CH Play mới hơn, lấy từ máy GPU Android 12) → cập nhật ngay khi máy vừa bật."""
    if not os.path.isfile(PLAY_APK): return
    r = _hadb(port, ["shell", "dumpsys package com.android.vending | grep -m1 codePath"], timeout=60)
    if "/data/app" in (r.stdout or ""): return           # đã được cập nhật rồi
    r = _hadb(port, ["install", "-r", PLAY_APK], timeout=300)
    if "Success" in (r.stdout or ""):                     # chỉ xoá dữ liệu CH Play khi vừa cập nhật thành công
        _hadb(port, ["shell", "pm clear com.android.vending"], timeout=60)

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
    if gpu:
        try: gpu_play_fix(port)
        except Exception: pass
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
    cr = cpu_real()
    rr = ram_real()
    print(f"Giới hạn: RAM {lr:.0f} GB (dùng chung ×{RAM_OVER:g}), CPU {lc:.0f} nhân (dùng chung ×{CPU_OVER:g})")
    print(f"RAM thật: {'?' if rr is None else f'{rr:.0f}%'} đang dùng — ngừng nhận máy mới khi ≥ {RAM_BUSY}%")
    dp, dfree = disk_real()
    print(f"Ổ cứng: {'?' if dp is None else f'{dp:.0f}% (còn {dfree:.0f} GB)'} — ngừng nhận máy mới khi ≥ {DISK_BUSY}%")
    print(f"CPU thật: {'?' if cr is None else f'{cr:.0f}%'} (trung bình 5 phút) — ngừng nhận máy mới khi ≥ {CPU_BUSY}%")
    print(f"Đã cấp:  RAM {ur:.1f} GB, CPU {uc:.1f} nhân  ({len(st['phones'])} máy)")
    print(f"Còn:     RAM {lr-ur:.1f} GB, CPU {lc-uc:.1f} nhân")

def cmd_create(a):
    st = load()
    if any(p["name"] == a.name for p in st["phones"]):
        print("LỖI: đã có máy tên này."); sys.exit(1)
    ur, uc = used(st); lr, lc, tr, tc = cap()
    if ur + a.ram > lr or uc + a.cpu > lc:
        print(f"HẾT HÀNG: thêm máy này (RAM {a.ram}GB, CPU {a.cpu}) sẽ vượt giới hạn (RAM {LIMIT_PCT}%, CPU ×{CPU_OVER:g}).")
        print(f"  Còn: RAM {lr-ur:.1f}GB, CPU {lc-uc:.1f} nhân."); sys.exit(2)
    cr = cpu_real()
    if cr is not None and cr >= CPU_BUSY:
        print(f"HẾT HÀNG: CPU thật đang chạy {cr:.0f}% (ngưỡng {CPU_BUSY}%)."); sys.exit(2)
    dp, dfree = disk_real()
    if dp is not None and (dp >= DISK_BUSY or dfree < 15):
        print(f"HẾT HÀNG: ổ cứng đã dùng {dp:.0f}% (còn {dfree:.0f}GB, ngưỡng {DISK_BUSY}%)."); sys.exit(2)
    rr = ram_real()
    if rr is not None and rr >= RAM_BUSY:
        print(f"HẾT HÀNG: RAM thật đang dùng {rr:.0f}% (ngưỡng {RAM_BUSY}%)."); sys.exit(2)
    if not re.match(r"^\d+x\d+$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    av = android_of(a.android)
    # Android 11 + card còn chỗ → máy GPU. Máy GPU CHƯA root được → khi đã có bản root, máy mới ưu tiên bản ROOT
    # (muốn ưu tiên GPU hơn root: thêm GPU_OVER_ROOT=1 vào ~/cloudphone.conf)
    rooted = pick_image(IMAGES[av][0]) != IMAGES[av][0]
    # Máy GPU nay đã có root sâu → bản nào có máy GPU (10/11/12) thì ưu tiên GPU khi card còn chỗ
    want_gpu = gpu_img_ok(av) and not a.nogpu and (gpu_rooted() or not rooted or CONF.get("GPU_OVER_ROOT") == "1")
    why = gpu_full_reason(st, a.ram) if want_gpu else "chưa có máy GPU bản này"
    gpu = want_gpu and not why
    # Card đầy → KHÔNG tạo nữa (báo HẾT HÀNG, web tự hoàn tiền). Chỉ khi GPU tắt hẳn mới dùng máy thường.
    if not gpu and (av in GPU_ONLY or (want_gpu and gpu_ready() and not GPU_FALLBACK)):
        print(f"HẾT HÀNG: Android {av} chỉ tạo máy GPU — {why}."); sys.exit(2)
    port = gpu_port(st) if gpu else free_port(st)
    if gpu and not port:
        if av in GPU_ONLY or not GPU_FALLBACK: print("HẾT HÀNG: hết cổng máy GPU."); sys.exit(2)
        gpu, port = False, free_port(st)
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
    r = gpu_create(a.name, a.ram, a.cpu, a.res, a.dpi, av) if gpu else docker_run(a.name, a.ram, a.cpu, port, a.res, fps, a.dpi, av)
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
    if CONF.get("GFX", "0") == "1" and image_ok(base + "_gfx"): return base + "_gfx"   # đồ hoạ đời mới (cloudphone-gfx.sh)
    return root_img(base) if CONF.get("ROOT", "1") == "magisk" and image_ok(root_img(base)) else base

def boot_extra(android=None):
    """Máy chủ có /dev/ashmem (module ashmem_linux) → Android dùng ashmem thật thay memfd.
    Chrome / Chromium / WebView BẮT BUỘC ashmem (memfd → Chrome văng ngay lúc mở: SharedMemoryRegionGetProtectionFlags failed).
    Android 9/10/11: ÉP vẽ hình bằng CPU (gpu_mode=guest). Mặc định "auto" thấy /dev/dri của card NVIDIA trên server → thử dùng card
    → card NVIDIA không chạy được → mọi app có đồ hoạ (Chrome, game) màn trắng 2 giây rồi văng ("GPU process isn't usable")."""
    ex = ["androidboot.use_memfd=false"] if os.path.exists("/dev/ashmem") else []
    gm = CONF.get("GPU_MODE", "guest")
    try: old = int(str(android or "11").split(".")[0]) <= 11
    except ValueError: old = True
    if gm in ("guest", "host") and (old or CONF.get("GPU_MODE_ALL") == "1"): ex.append(f"androidboot.redroid_gpu_mode={gm}")
    if old and CONF.get("GFX", "0") == "1" and image_ok(IMAGES[android_of(android or "11")][0] + "_gfx"): ex.append("ro.hardware.egl=angle")      # bản *_gfx: đồ hoạ ANGLE của Android 12 (cloudphone-gfx.sh)
    return ex

def docker_run(name, ram, cpu, port, res, fps, dpi=0, android="11"):
    w, h = res.split("x")
    img, tr = IMAGES[android_of(android)]
    img = pick_image(img)                                   # có bản root → máy tự có root (Magisk)
    data = os.path.join(DATA_ROOT, name); os.makedirs(data, exist_ok=True)
    return sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", name,
               *mem_args(ram), *cpu_args(cpu),
               "-v", f"{data}:/data", "-p", f"127.0.0.1:{port}:5555", img,   # chỉ mở trong server, không lộ ra internet
               f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
               f"androidboot.redroid_dpi={int(dpi) or auto_dpi(res)}", f"androidboot.redroid_fps={fps}"] + boot_extra(android) + (HOUDINI if tr == "houdini" else ARM))

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
MC_APK = os.path.join(SU_DIR, "multiclone-overlay.apk"); MC_DEST = "/system/overlay/BlueRobloxMultiClone.apk"
def mc_install(name):
    """NHIỀU CLONE (thử nghiệm, bật bằng cloudphone-multiclone.sh on): chép gói chỉnh hệ thống vào máy Android 11/12.
    Có tác dụng từ lần khởi động lại tiếp theo của máy."""
    if CONF.get("MULTICLONE", "0") != "1" or not os.path.isfile(MC_APK): return
    r = sh(["docker", "exec", name, "/system/bin/sh", "-c", f"[ -f {MC_DEST} ] && echo HAVE; getprop ro.build.version.sdk"])
    o = (r.stdout or "").split()
    if "HAVE" in o or not o or not o[-1].isdigit() or int(o[-1]) < 30: return
    if sh(["docker", "cp", MC_APK, f"{name}:{MC_DEST}"]).returncode == 0:
        sh(["docker", "exec", name, "/system/bin/chmod", "644", MC_DEST])

def su_install(name):
    """Cài / bật lại root daemon trong máy thường. Trả về True nếu app trong máy dùng được su."""
    try: mc_install(name)
    except Exception: pass
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
        sh(["docker", "exec", name, "/system/bin/sh", "-c", "pkill -x cpsud; true"])
        # chạy nền bằng "docker exec -d" (tự tách nền kiểu fork bị docker tắt theo khi lệnh exec kết thúc)
        sh(["docker", "exec", "-d", name, "/system/xbin/cpsud", "/dev/socket/cpsu", "fg"])
        time.sleep(1)
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
    if CONF.get("GFX", "0") == "1" and image_ok(img + "_gfx"): img = img + "_gfx"
    data = os.path.join(DATA_ROOT, p["name"])
    r = sh(["docker", "run", "-itd", "--privileged", "--restart", "unless-stopped", "--name", p["name"], *mem_args(p["ram"]), *cpu_args(p["cpu"]),
            "-v", f"{data}:/data", "-p", f"127.0.0.1:{p['port']}:5555", img, f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
            f"androidboot.redroid_dpi={int(p.get('dpi', 0)) or auto_dpi(p.get('res', '720x1280'))}", f"androidboot.redroid_fps={p.get('fps', 30)}"] + boot_extra(p.get("android", "11")) + (HOUDINI if tr == "houdini" else ARM))
    if r.returncode != 0: raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    if not was_on: sh(["docker", "stop", p["name"]])
    else: wait_boot(p["name"], p["port"], apps=False)

def cmd_recreate(a):
    """Tạo lại máy (cùng dữ liệu) để nhận cấu hình khởi động mới (VD bật ashmem cho Chrome). Mỗi máy khởi động lại ~1 phút."""
    n = 0
    for p in load()["phones"]:
        if is_gpu(p) or (a.name and p["name"] != a.name): continue
        try: recreate_base(p); n += 1; print(f"✓ {p['name']}: đã tạo lại" + (" (có ashmem — Chrome chạy được)" if boot_extra() else ""))
        except Exception as e: print(f"! {p['name']}: LỖI {e}")
    print(f"✅ Xong — {n} máy")

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
    ra = ram_avail_gb()
    print(f"RAM thật còn trống: {'?' if ra is None else f'{ra:.0f} GB'} (chừa {GPU_RAM_RESERVE:g} GB) · card đầy thì: {'tạo máy thường' if GPU_FALLBACK else 'HẾT HÀNG, không tạo nữa'}")
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

def cmd_resize(a):
    """Nâng RAM / CPU cho máy đang chạy — KHÔNG khởi động lại, giữ nguyên app/dữ liệu (docker update)."""
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    ram = a.ram if a.ram > 0 else float(p["ram"]); cpu = a.cpu if a.cpu > 0 else float(p["cpu"])
    if is_gpu(p):
        gpu_svc("stop", a.name); gpu_config(a.name, ram, cpu, p.get("res", "720x1280"), p.get("dpi", 0)); r = gpu_svc("start", a.name)
    else:
        r = sh(["docker", "update"] + mem_args(ram) + cpu_args(cpu) + [a.name])
    if r.returncode != 0:
        print("LỖI:", (r.stderr or r.stdout or "").strip()[:300]); sys.exit(4)
    p["ram"], p["cpu"] = ram, cpu; save(st)
    print(f"⬆️ '{a.name}' → {ram:g}GB RAM · {cpu:g} nhân")

# ===================== CHUYỂN MÁY THƯỜNG (redroid) → MÁY GPU =====================
# Sao lưu app + dữ liệu app (+ thư mục Android/data, obb) từ máy cũ → tạo máy GPU mới cùng cấu hình & hạn → cài lại + chép dữ liệu.
# KHÔNG xoá máy cũ (cloudphone-migrate.py xoá sau khi đã đổi trên web). Lỗi giữa chừng → xoá máy GPU mới, máy cũ giữ nguyên.
PKG_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$")
MIGRATE_MAP = {"9": "11", "10": "11"}          # 9/10 lên 11: máy GPU 11 chạy được game ARM (bản 10 của Google thì không)
def _hadb(port, args, timeout=900, **kw):
    return subprocess.run([os.path.join(SDK, "platform-tools", "adb"), "-s", f"emulator-{int(port) - 1}"] + args,
                          capture_output=True, text=True, timeout=timeout, env=GPU_ENV, **kw)

def cmd_migrate(a):
    import glob, shutil, math
    st = load(); p = rec(a.name, st)
    if not p: print("Không có máy tên này."); sys.exit(1)
    if is_gpu(p): print("Máy này đã là máy GPU."); sys.exit(0)
    av = a.android or MIGRATE_MAP.get(str(p.get("android", "11")), str(p.get("android", "11")))
    if not gpu_img_ok(av): print(f"SKIP: chưa có máy GPU Android {av} trên server."); sys.exit(2)
    why = gpu_full_reason(st, p.get("ram", 4))
    if why: print(f"SKIP: {why} — máy cũ giữ nguyên."); sys.exit(2)
    W = os.path.join(DATA_ROOT, "_migrate", a.name); shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
    # 1) sao lưu từ máy cũ (máy cũ vẫn chạy)
    pkgs = [l.split(":", 1)[1].strip() for l in (ashell(a.name, "pm list packages -3").stdout or "").splitlines() if l.startswith("package:")]
    skip = {"com.topjohnwu.magisk"}
    # Dịch vụ Google / CH Play: máy GPU đã có sẵn bản gốc → KHÔNG chép đè (chép đè làm "Google Play Store keeps stopping")
    gsys = ("com.android.vending", "com.google.android.gms", "com.google.android.gsf", "com.google.android.gsf.login",
            "com.google.android.syncadapters", "com.google.android.ext.services", "com.google.android.webview",
            "com.android.chrome", "com.google.android.apps.", "org.microg.", "com.google.android.youtube")
    pkgs = [x for x in pkgs if x not in skip and not x.startswith(gsys) and PKG_RE.match(x)]
    print(f"[1/4] Sao lưu {len(pkgs)} app từ {a.name}…")
    for pk in pkgs:
        d = os.path.join(W, pk); os.makedirs(d)
        paths = [l.split(":", 1)[1].strip() for l in (ashell(a.name, f"pm path {pk}").stdout or "").splitlines() if l.startswith("package:")]
        for i, ap in enumerate(paths): sh(["docker", "cp", f"{a.name}:{ap}", os.path.join(d, f"{i:02d}.apk")])
        ashell(a.name, f"am force-stop {pk}")
        for tag, cmd in (("data", f"cd /data/data && tar -cf - --exclude={pk}/lib --exclude={pk}/cache --exclude={pk}/code_cache {pk}"),
                         ("sd", f"cd /data/media/0 && ls -d Android/data/{pk} Android/obb/{pk} 2>/dev/null | xargs -r tar -cf -")):
            with open(os.path.join(d, tag + ".tar"), "wb") as fo:
                subprocess.run(["docker", "exec", a.name, "/system/bin/sh", "-c", cmd], stdout=fo, stderr=subprocess.DEVNULL)
    # 2) tạo máy GPU mới cùng cấu hình
    left = max(1, math.ceil((parse_dt(p["expires"]) - now()).total_seconds() / 86400))
    print(f"[2/4] Tạo máy GPU {a.new} (Android {av}, {p['ram']}GB, {p['cpu']} nhân)…")
    r = subprocess.run(["python3", os.path.abspath(__file__), "create", a.new, "--ram", str(p["ram"]), "--cpu", str(p["cpu"]), "--days", str(left),
                        "--res", p.get("res") or "720x1280", "--fps", str(p.get("fps", 30)), "--android", av], capture_output=True, text=True)
    st = load(); q = rec(a.new, st)
    if r.returncode != 0 or not q or not is_gpu(q):
        if q: subprocess.run(["python3", os.path.abspath(__file__), "delete", a.new], capture_output=True)
        print("LỖI tạo máy GPU:", (r.stdout + r.stderr)[-300:]); sys.exit(4)
    q["expires"] = p["expires"]; q["dpi"] = p.get("dpi", 0); save(st)        # giữ ĐÚNG hạn cũ
    port = q["port"]
    # 3) cài lại app + chép dữ liệu (máy GPU có root sâu)
    print(f"[3/4] Cài lại app + chép dữ liệu vào {a.new}…"); fails = []
    for pk in pkgs:
        d = os.path.join(W, pk); apks = sorted(glob.glob(os.path.join(d, "*.apk")))
        if not apks: continue
        ri = _hadb(port, (["install-multiple"] if len(apks) > 1 else ["install"]) + ["-r", "-g"] + apks)
        if ri.returncode != 0 or "Success" not in (ri.stdout or ""): fails.append(pk); continue
        for tag, dest in (("data", "/data/data"), ("sd", "/data/media/0")):
            t = os.path.join(d, tag + ".tar")
            if not os.path.exists(t) or os.path.getsize(t) < 1024: continue
            _hadb(port, ["push", t, "/data/local/tmp/m.tar"])
            if tag == "data":
                cmd = (f"am force-stop {pk}; U=$(stat -c %u /data/data/{pk}); cd /data/data && tar -xf /data/local/tmp/m.tar && "
                       f"chown -R $U:$U /data/data/{pk} && restorecon -RF /data/data/{pk}")
            else:
                cmd = (f"U=$(stat -c %u /data/data/{pk}); cd /data/media/0 && tar -xf /data/local/tmp/m.tar; "
                       f"[ -d Android/data/{pk} ] && chown -R $U:$U Android/data/{pk}; [ -d Android/obb/{pk} ] && chmod -R 775 Android/obb/{pk}; true")
            _hadb(port, ["shell", f"su -c '{cmd}; rm -f /data/local/tmp/m.tar'"])
    shutil.rmtree(W, ignore_errors=True)
    print(f"[4/4] Xong: {len(pkgs) - len(fails)}/{len(pkgs)} app" + (f" (không cài lại được: {', '.join(fails[:8])})" if fails else ""))
    print(f"MIGRATED {a.new} {port} {av}")

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
    z = sub.add_parser("resize"); z.add_argument("name"); z.add_argument("--ram", type=float, default=0); z.add_argument("--cpu", type=float, default=0); z.set_defaults(f=cmd_resize)
    mg = sub.add_parser("migrate"); mg.add_argument("name"); mg.add_argument("new"); mg.add_argument("--android", default=""); mg.set_defaults(f=cmd_migrate)
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
    for nm, fn in (("unroot", cmd_unroot), ("crashes", cmd_crashes), ("recreate", cmd_recreate)):
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
