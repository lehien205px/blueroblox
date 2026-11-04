# -*- coding: utf-8 -*-
# =====================================================================
#  phathinh.py — PHÁT HÌNH KIỂU PARSEC cho 1 máy LDPlayer (bản thử bước 1)
#   Quay CỬA SỔ máy (Windows Graphics Capture — quay được cả khi cửa sổ chạy ngầm/bị che)
#   → cắt đúng phần màn hình Android → nén bằng card NVIDIA (NVENC, không được thì CPU x264)
#   → phát WebRTC qua MediaMTX:  http://<IP máy chủ>:8889/<TÊN_MÁY>
#  Chạy:  python C:\blue\phathinh.py cp_265918469c [--fps 60] [--mbps 4] [--cpu]
#  Cần:   pip install windows-capture numpy   (tool tự cài nếu thiếu)
# =====================================================================
import argparse, ctypes, ctypes.wintypes as wt, os, subprocess, sys, threading, time

B = os.path.dirname(os.path.abspath(__file__)); RTC = os.path.join(B, "rtc")
ap = argparse.ArgumentParser(); ap.add_argument("name"); ap.add_argument("--fps", type=int, default=60)
ap.add_argument("--mbps", type=float, default=4); ap.add_argument("--cpu", action="store_true")
A = ap.parse_args()
try:
    import numpy as np
    from windows_capture import WindowsCapture, Frame, InternalCaptureControl
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "windows-capture", "numpy"], check=False)
    import numpy as np
    from windows_capture import WindowsCapture, Frame, InternalCaptureControl

u = ctypes.windll.user32
try: ctypes.windll.shcore.SetProcessDpiAwareness(2)        # toạ độ điểm ảnh thật
except Exception: u.SetProcessDPIAware()

# ---------- tìm cửa sổ máy + phần màn hình Android bên trong (cửa sổ con lớn nhất) ----------
def find_top(name):
    hits = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, b, n + 1)
            if b.value == name: hits.append(h)
        return True
    u.EnumWindows(cb, 0); return hits[0] if hits else None
def rect(h):
    r = wt.RECT(); u.GetWindowRect(h, ctypes.byref(r)); return r.left, r.top, r.right, r.bottom
def frame_bounds(h):                                        # vùng Windows Graphics Capture thật sự quay (không gồm bóng đổ)
    r = wt.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0: return r.left, r.top, r.right, r.bottom
    return rect(h)
def render_child(h):
    kids = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(c, _):
        if u.IsWindowVisible(c):
            l, t, r_, b_ = rect(c); kids.append(((r_ - l) * (b_ - t), (l, t, r_, b_)))
        return True
    u.EnumChildWindows(h, cb, 0)
    return max(kids)[1] if kids else None

top = find_top(A.name)
if not top: sys.exit(f"Không thấy cửa sổ '{A.name}' — máy phải đang bật (cửa sổ chạy ngầm cũng được, KHÔNG thu nhỏ).")
if u.IsIconic(top): u.ShowWindow(top, 4); time.sleep(1)
fb = frame_bounds(top); ch = render_child(top) or fb
CX, CY = max(0, ch[0] - fb[0]), max(0, ch[1] - fb[1])
CW, CH = (ch[2] - ch[0]) & ~1, (ch[3] - ch[1]) & ~1
print(f"Cửa sổ '{A.name}': vùng Android {CW}x{CH} (lệch {CX},{CY})", flush=True)

# ---------- máy chủ WebRTC (MediaMTX) ----------
def ensure_mediamtx():
    if subprocess.run(["tasklist", "/FI", "IMAGENAME eq mediamtx.exe"], capture_output=True, text=True).stdout.count("mediamtx.exe"): return
    exe = next((os.path.join(d, f) for d, _, fs in os.walk(RTC) for f in fs if f == "mediamtx.exe"), None)
    if not exe: sys.exit("Chưa có MediaMTX — chạy thu-webrtc.ps1 1 lần để tải.")
    subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=0x08000000); time.sleep(2)
ensure_mediamtx()
FF = os.path.join(RTC, "ffmpeg.exe")
if not os.path.isfile(FF): sys.exit("Chưa có ffmpeg — chạy lệnh tải FFmpeg 6.1 trước.")

# ---------- nén + phát ----------
def ffmpeg_cmd(cpu):
    inp = ["-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{CW}x{CH}", "-r", str(A.fps), "-i", "-"]
    br = f"{A.mbps:g}M"
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline", "-pix_fmt", "yuv420p"] if cpu else
           ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-zerolatency", "1", "-rc", "cbr", "-profile:v", "baseline", "-pix_fmt", "yuv420p"])
    return [FF, "-hide_banner", "-loglevel", "warning"] + inp + enc + ["-b:v", br, "-maxrate", br, "-bufsize", f"{A.mbps / 4:g}M", "-bf", "0",
            "-g", str(A.fps * 2), "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:8554/{A.name}"]

latest = {"buf": None, "n": 0}; lock = threading.Lock()
cap = WindowsCapture(cursor_capture=False, draw_border=False, window_name=A.name)
@cap.event
def on_frame_arrived(frame: Frame, ctl: InternalCaptureControl):
    a = frame.frame_buffer                                   # (cao, rộng, 4) BGRA
    sub = a[CY:CY + CH, CX:CX + CW]
    if sub.shape[0] == CH and sub.shape[1] == CW:
        with lock: latest["buf"] = np.ascontiguousarray(sub).tobytes(); latest["n"] += 1
@cap.event
def on_closed(): print("Cửa sổ đã đóng — dừng.", flush=True); os._exit(0)
if hasattr(cap, "start_free_threaded"): cap.start_free_threaded()       # quay ở luồng riêng của thư viện
else: threading.Thread(target=cap.start, daemon=True).start()

def run(cpu):
    p = subprocess.Popen(ffmpeg_cmd(cpu), stdin=subprocess.PIPE, creationflags=0x08000000)
    step = 1.0 / A.fps; nxt = time.perf_counter(); last = None; t0 = time.time(); sent = 0
    try:
        while p.poll() is None:
            with lock: buf = latest["buf"]
            if buf is not None:
                p.stdin.write(buf); sent += 1                # luôn gửi đều đặn khung MỚI NHẤT (không xếp hàng khung cũ)
            nxt += step; d = nxt - time.perf_counter()
            if d > 0: time.sleep(d)
            else: nxt = time.perf_counter()
            if time.time() - t0 > 5:
                print(f"  {('CPU x264' if cpu else 'card NVENC')}: gửi {sent / (time.time() - t0):.0f} khung/giây · cửa sổ vẽ {latest['n'] / (time.time() - t0):.0f} khung/giây", flush=True)
                t0 = time.time(); sent = 0; latest["n"] = 0
    except (BrokenPipeError, OSError): pass
    return p.wait()

ip = next((l.split(":")[-1].strip() for l in subprocess.run(["ipconfig"], capture_output=True, text=True, errors="replace").stdout.splitlines()
           if "IPv4" in l and ("192.168." in l or "10." in l)), "IP_MAY_CHU")
print(f"\n===== XEM: http://{ip}:8889/{A.name}  (điện thoại cùng wifi nhà) — Ctrl+C để dừng =====\n", flush=True)
while latest["buf"] is None: time.sleep(0.05)
t = time.time(); code = run(A.cpu)
if not A.cpu and time.time() - t < 8:
    print("Card NVIDIA không nén được (driver?) → chuyển sang CPU x264", flush=True); run(True)
