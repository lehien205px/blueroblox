# -*- coding: utf-8 -*-
# =====================================================================
#  panel.py — BẢNG ĐIỀU KHIỂN BlueRoblox trên Windows (MEmu)
#  Mở: bấm đúp biểu tượng "BlueRoblox" trên Desktop  (hoặc: pythonw C:\blue\panel.py)
#   • Đèn trạng thái: MEmu, adb, khoá Supabase, trình phát hình, cổng bảo vệ, chương trình nhận đơn, Cloudflare, web
#   • Nút: Cài đặt/Cập nhật · Dán khoá Supabase · Nối Cloudflare · Bật/Tắt tất cả · Tạo/Xoá máy thử · Làm mới máy khách
#   • Nhật ký từng phần, dòng lỗi tô đỏ. Chương trình nào bị tắt → tự bật lại.
# =====================================================================
import ctypes, json, os, queue, re, socket, subprocess, sys, threading, time, tkinter as tk, urllib.request
from tkinter import messagebox, simpledialog, ttk

B = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(os.path.dirname(sys.executable), "python.exe") if sys.executable.lower().endswith("pythonw.exe") else sys.executable
LOGS = os.path.join(B, "logs"); os.makedirs(LOGS, exist_ok=True)
CONF = os.path.join(B, "cloudphone.conf")
RAW = "https://raw.githubusercontent.com/lehien205px/blueroblox/main"
NOWIN = 0x08000000
ENV = dict(os.environ, PATH=os.path.join(B, "platform-tools") + os.pathsep + os.environ.get("PATH", ""), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

# ---------- màu Cloud Blue ----------
BG, CARD, LINE, FG, MUTED, BLUE, GOOD, BAD, WARN = "#0b1530", "#12204a", "#22356e", "#e8efff", "#93a4cf", "#3d8bff", "#2ecc71", "#ff5d6c", "#ffb547"
ERR = re.compile(r"(LỖI|lỗi|Error|ERROR|Traceback|Exception|✗|HẾT HÀNG|failed|không kết nối)", re.I)

SERVICES = {   # tên: (thư mục chạy, lệnh)
    "ws-scrcpy": (os.path.join(B, "ws-scrcpy", "dist"), [__import__("shutil").which("node") or r"C:\Program Files\nodejs\node.exe", "index.js"]),
    "cpapi": (B, [PY, "-u", "run.py", "cpapi.py"]),
    "cloudphoned": (B, [PY, "-u", "run.py", "cloudphoned.py"]),
}

def conf():
    d = {}
    try:
        for line in open(CONF, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); d[k.strip()] = v.strip()
    except FileNotFoundError: pass
    return d

def set_conf(key, val):
    lines, done = [], False
    try: lines = open(CONF, encoding="utf-8").read().splitlines()
    except FileNotFoundError: pass
    for i, l in enumerate(lines):
        if l.split("=", 1)[0].strip() == key: lines[i] = f"{key}={val}"; done = True
    if not done: lines.append(f"{key}={val}")
    open(CONF, "w", encoding="utf-8").write("\n".join(lines) + "\n")

def port_open(p):
    s = socket.socket(); s.settimeout(0.5)
    try: s.connect(("127.0.0.1", p)); return True
    except Exception: return False
    finally: s.close()

def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def run_admin(ps_cmd, log):
    """Chạy lệnh PowerShell bằng quyền Administrator (Windows hỏi 1 lần); mọi dòng chữ ghi ra file nhật ký (UTF-8)."""
    ps1 = os.path.join(LOGS, "_admin_job.ps1")
    body = ("$ErrorActionPreference = 'Continue'\n"
            f"$LOG = '{log}'\n"
            "& {\n" + ps_cmd + "\n} *>&1 | ForEach-Object { $s = ($_ | Out-String).TrimEnd(); "
            "[IO.File]::AppendAllText($LOG, $s + [Environment]::NewLine, [Text.Encoding]::UTF8); $s }\n"
            "[IO.File]::AppendAllText($LOG, 'XONG_JOB' + [Environment]::NewLine)\n")
    open(ps1, "w", encoding="utf-8-sig").write(body)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", f'-NoProfile -ExecutionPolicy Bypass -File "{ps1}"', B, 1)

class Svc:
    def __init__(self, name, ui):
        self.name, self.ui, self.p, self.want = name, ui, None, False
        self.log = os.path.join(LOGS, name + ".log")
    def start(self):
        self.want = True
        if self.p and self.p.poll() is None: return
        cwd, cmd = SERVICES[self.name]
        if not os.path.isdir(cwd): self.ui.say(self.name, f"✗ Chưa cài ({cwd}) — bấm Cài đặt trước"); return
        try:
            self.p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      env=ENV, creationflags=NOWIN, bufsize=1, text=True, encoding="utf-8", errors="replace")
        except Exception as e:
            self.ui.say(self.name, f"✗ Không chạy được: {e}"); return
        self.ui.say(self.name, f"▶ Đã bật (pid {self.p.pid})")
        threading.Thread(target=self._pump, args=(self.p,), daemon=True).start()
    def _pump(self, p):
        with open(self.log, "a", encoding="utf-8") as f:
            for line in p.stdout:
                f.write(time.strftime("%H:%M:%S ") + line); f.flush(); self.ui.say(self.name, line.rstrip())
        code = p.wait()
        if self.want:
            self.ui.say(self.name, f"✗ Bị tắt (mã {code}) — tự bật lại sau 5 giây"); time.sleep(5)
            if self.want: self.start()
    def stop(self):
        self.want = False
        if self.p and self.p.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
            self.ui.say(self.name, "■ Đã tắt")
    def alive(self): return bool(self.p and self.p.poll() is None)

class App:
    def __init__(self, auto=False):
        self.q = queue.Queue()
        r = self.root = tk.Tk(); r.title("BlueRoblox — Bảng điều khiển máy chủ Windows"); r.geometry("1100x720"); r.configure(bg=BG)
        r.minsize(900, 600)
        st = ttk.Style(); st.theme_use("clam")
        st.configure("TNotebook", background=BG, borderwidth=0); st.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(14, 6))
        st.map("TNotebook.Tab", background=[("selected", BLUE)], foreground=[("selected", "white")])
        self.svcs = {n: Svc(n, self) for n in SERVICES}
        top = tk.Frame(r, bg=BG); top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="🐋 BlueRoblox", fg=BLUE, bg=BG, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(top, text="  máy chủ Windows · MEmu", fg=MUTED, bg=BG, font=("Segoe UI", 11)).pack(side="left", pady=(6, 0))
        self.adm = tk.Label(top, text="", bg=BG, font=("Segoe UI", 10)); self.adm.pack(side="right")
        # đèn trạng thái
        self.lights = {}
        grid = tk.Frame(r, bg=BG); grid.pack(fill="x", padx=14)
        items = [("memu", "MEmu"), ("adb", "adb (Google)"), ("key", "Khoá Supabase"), ("ws", "Trình phát hình :8000"),
                 ("api", "Cổng bảo vệ :8010"), ("daemon", "Nhận đơn (cloudphoned)"), ("cf", "Đường hầm Cloudflare"), ("web", "phone.blueroblox.shop")]
        for i, (k, t) in enumerate(items):
            c = tk.Frame(grid, bg=CARD, highlightbackground=LINE, highlightthickness=1); c.grid(row=i // 4, column=i % 4, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(i % 4, weight=1)
            dot = tk.Label(c, text="●", fg=MUTED, bg=CARD, font=("Segoe UI", 16)); dot.pack(side="left", padx=(10, 6), pady=6)
            box = tk.Frame(c, bg=CARD); box.pack(side="left", fill="x", expand=True)
            tk.Label(box, text=t, fg=FG, bg=CARD, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
            sub = tk.Label(box, text="đang kiểm tra…", fg=MUTED, bg=CARD, font=("Segoe UI", 9), anchor="w"); sub.pack(fill="x")
            self.lights[k] = (dot, sub)
        # nút
        bar1 = tk.Frame(r, bg=BG); bar1.pack(fill="x", padx=14, pady=(8, 2))
        bar2 = tk.Frame(r, bg=BG); bar2.pack(fill="x", padx=14, pady=(2, 8))
        def btn(text, cmd, color=CARD, fg=FG):
            bar = bar1 if len(bar1.winfo_children()) < 5 else bar2
            b = tk.Button(bar, text=text, command=cmd, bg=color, fg=fg, activebackground=BLUE, activeforeground="white", relief="flat",
                          font=("Segoe UI", 10, "bold"), padx=12, pady=7, cursor="hand2", bd=0)
            b.pack(side="left", padx=3); return b
        btn("🚀 TỰ ĐỘNG A→Z", self.auto, GOOD, "#062012")
        btn("① Cài đặt / Cập nhật", self.install)
        btn("② Dán khoá Supabase", self.set_key)
        btn("③ Nối Cloudflare", self.cloudflare)
        btn("▶ Bật tất cả", self.start_all, BLUE, "white")
        btn("■ Tắt tất cả", self.stop_all, "#3a2030", "#ffb3bb")
        btn("🧪 Tạo máy thử", self.test_phone)
        btn("📋 Danh sách máy", lambda: self.job("Danh sách máy", [PY, "phonectl.py", "list"]))
        btn("♻ Làm mới máy khách", self.remake)
        # nhật ký
        self.nb = ttk.Notebook(r); self.nb.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.texts = {}
        for n in ["Tổng quan"] + list(SERVICES) + ["Việc đang làm"]:
            f = tk.Frame(self.nb, bg=CARD)
            t = tk.Text(f, bg="#08102a", fg=FG, insertbackground=FG, font=("Consolas", 10), relief="flat", wrap="word")
            sb = tk.Scrollbar(f, command=t.yview); t.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y"); t.pack(fill="both", expand=True)
            t.tag_configure("err", foreground=BAD); t.tag_configure("ok", foreground=GOOD); t.tag_configure("dim", foreground=MUTED)
            self.nb.add(f, text=n); self.texts[n] = t
        self.status = tk.Label(r, text="", fg=MUTED, bg=BG, anchor="w", font=("Segoe UI", 9)); self.status.pack(fill="x", padx=16, pady=(0, 8))
        self.say("Tổng quan", "Chào bạn! Làm lần lượt: ① Cài đặt → ② Dán khoá Supabase → ③ Nối Cloudflare → 🧪 Tạo máy thử → ▶ Bật tất cả.")
        r.after(150, self._drain)
        threading.Thread(target=self._checker, daemon=True).start()
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        if auto: r.after(1500, self.start_all)

    # ---------- nhật ký ----------
    def say(self, tab, line): self.q.put((tab, line))
    def _drain(self):
        try:
            for _ in range(400):
                tab, line = self.q.get_nowait()
                for t in {tab, "Tổng quan"} if tab in SERVICES and ERR.search(line) else {tab}:
                    w = self.texts.get(t) or self.texts["Tổng quan"]
                    tag = "err" if ERR.search(line) else "ok" if re.search(r"(✓|✅|\[OK\]|Đã bật|sẵn sàng|XONG)", line) else None
                    w.insert("end", (f"[{tab}] " if t != tab else "") + line + "\n", tag)
                    if int(w.index("end-1c").split(".")[0]) > 3000: w.delete("1.0", "500.0")
                    w.see("end")
        except queue.Empty: pass
        self.root.after(150, self._drain)

    def light(self, k, ok, text):
        dot, sub = self.lights[k]
        dot.configure(fg=GOOD if ok is True else BAD if ok is False else WARN); sub.configure(text=text)

    # ---------- kiểm tra trạng thái (mỗi 4 giây) ----------
    def _checker(self):
        n = 0
        while True:
            c = conf()
            memu = next((d for d in [c.get("MEMU_DIR", ""), r"C:\Program Files\Microvirt\MEmu", r"D:\Program Files\Microvirt\MEmu"]
                         if d and os.path.isfile(os.path.join(d, "memuc.exe"))), "")
            L = self.root.after
            L(0, self.light, "memu", bool(memu), memu or "chưa thấy memuc.exe")
            adb = os.path.isfile(os.path.join(B, "platform-tools", "adb.exe"))
            L(0, self.light, "adb", adb, "đã có" if adb else "chưa cài — bấm ①")
            key = c.get("SUPABASE_SERVICE_KEY", "")
            L(0, self.light, "key", key.startswith("eyJ") or key.startswith("sb_secret_"), "đã dán" if len(key) > 30 else "chưa dán — bấm ②")
            L(0, self.light, "ws", port_open(8000), "đang chạy" if port_open(8000) else "tắt")
            L(0, self.light, "api", port_open(8010), "đang chạy" if port_open(8010) else "tắt")
            d = self.svcs["cloudphoned"].alive()
            L(0, self.light, "daemon", d, "đang nhận đơn" if d else "tắt")
            if n % 3 == 0:
                q = subprocess.run(["sc", "query", "cloudflared"], capture_output=True, text=True, creationflags=NOWIN).stdout or ""
                cf = "RUNNING" in q
                L(0, self.light, "cf", cf if "SERVICE_NAME" in q else False, "đang chạy" if cf else ("đã cài, đang tắt" if "SERVICE_NAME" in q else "chưa nối — bấm ③"))
                try:
                    with urllib.request.urlopen(urllib.request.Request("https://phone.blueroblox.shop/", headers={"User-Agent": "BlueRoblox-panel"}), timeout=8) as rr:
                        L(0, self.light, "web", True, f"mở được (HTTP {rr.status})")
                except urllib.error.HTTPError as e:
                    L(0, self.light, "web", e.code < 500, f"HTTP {e.code}" + (" — máy chủ chưa chạy" if e.code >= 500 else ""))
                except Exception as e:
                    L(0, self.light, "web", False, "không vào được")
            L(0, self.adm.configure, {"text": "🛡 Đang chạy quyền Admin" if is_admin() else "Quyền thường (nút ①③ sẽ hỏi quyền Admin)",
                                      "fg": GOOD if is_admin() else MUTED})
            n += 1; time.sleep(4)

    # ---------- việc chạy 1 lần ----------
    def job(self, title, cmd, cwd=B, done=None, wait=False):
        self.root.after(0, self.nb.select, len(SERVICES) + 1); self.say("Việc đang làm", f"\n===== {title} =====")
        def go():
            out = []
            try:
                p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=ENV, creationflags=NOWIN,
                                     text=True, encoding="utf-8", errors="replace")
                for line in p.stdout: self.say("Việc đang làm", line.rstrip()); out.append(line)
                code = p.wait(); self.say("Việc đang làm", f"===== xong ({'OK' if code == 0 else 'mã ' + str(code)}) =====")
                if done: self.root.after(0, done, code)
                return code, "".join(out)
            except Exception as e:
                self.say("Việc đang làm", f"✗ {e}"); return 99, str(e)
        if wait: return go()
        threading.Thread(target=go, daemon=True).start()

    def tail(self, path, title, secs=3600, wait=False):
        self.root.after(0, self.nb.select, len(SERVICES) + 1); self.say("Việc đang làm", f"\n===== {title} (cửa sổ Admin) =====")
        def go():
            import codecs
            dec = codecs.getincrementaldecoder("utf-8")(errors="replace"); t0, pos, buf = time.time(), 0, ""
            while time.time() - t0 < secs:
                try:
                    with open(path, "rb") as f: f.seek(pos); data = f.read(); pos += len(data)
                    buf += dec.decode(data).lstrip("\ufeff")
                    *lines, buf = buf.split("\n")
                    for line in lines:
                        if line.strip() == "XONG_JOB": self.say("Việc đang làm", f"===== {title}: xong ====="); return True
                        if line.strip(): self.say("Việc đang làm", line.rstrip())
                except FileNotFoundError:
                    if time.time() - t0 > 90: self.say("Việc đang làm", "✗ Cửa sổ Admin chưa chạy (bạn bấm Không / No?)"); return False
                time.sleep(1)
            return False
        if wait: return go()
        threading.Thread(target=go, daemon=True).start()

    def install(self):
        if not messagebox.askyesno("Cài đặt / Cập nhật", "Tải bản mới nhất từ GitHub và cài đủ (Node.js, adb, Cloudflare, trình phát hình)?\n"
                                   "Windows sẽ hỏi quyền Admin. Các dịch vụ sẽ tạm tắt trong lúc cài."): return
        self.stop_all()
        log = os.path.join(LOGS, "setup.log")
        try: os.remove(log)
        except OSError: pass
        run_admin(f"Set-Location C:\\\ncurl.exe -fsSL -o C:\\blue\\setup.ps1 {RAW}/windows/setup.ps1\n& C:\\blue\\setup.ps1", log)
        self.tail(log, "Cài đặt / Cập nhật")

    def set_key(self):
        k = simpledialog.askstring("Khoá Supabase", "Mở Supabase → Project Settings → API Keys → khoá service_role (Reveal → Copy)\n"
                                   "rồi dán vào đây. Khoá chỉ lưu trong C:\\blue\\cloudphone.conf, không gửi đi đâu.", show="•", parent=self.root)
        if not k: return False
        k = k.strip()
        if not (k.startswith("eyJ") or k.startswith("sb_secret_")): messagebox.showerror("Sai khoá", "Khoá service_role bắt đầu bằng eyJ… hoặc sb_secret_…"); return False
        set_conf("SUPABASE_SERVICE_KEY", k)
        for x, v in (("SUPABASE_URL", "https://nprftgodswlpzomcttal.supabase.co"), ("PUBLIC_BASE", "https://phone.blueroblox.shop"), ("NODE_ID", "win1")):
            if not conf().get(x): set_conf(x, v)
        self.say("Tổng quan", "✓ Đã lưu khoá Supabase. Bấm ▶ Bật tất cả (hoặc Bật lại) để dùng khoá mới.")
        return True

    def cf_token(self):
        s = simpledialog.askstring("Nối Cloudflare", "Cloudflare → Zero Trust → Networks → Tunnels → chọn đường hầm cũ → Configure\n"
                                   "→ phần Install connector chọn Windows → copy cả lệnh (có chữ eyJ…) rồi dán vào đây.", parent=self.root)
        if not s: return None
        m = re.search(r"(eyJ[A-Za-z0-9_\-=.]{40,})", s)
        if not m: messagebox.showerror("Không thấy mã", "Trong chữ vừa dán không có mã bắt đầu bằng eyJ…"); return None
        return m.group(1)

    def cf_install(self, token, wait=False):
        log = os.path.join(LOGS, "cloudflare.log")
        try: os.remove(log)
        except OSError: pass
        exe = r'$c = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source; if (-not $c) { $c = "C:\Program Files (x86)\cloudflared\cloudflared.exe" }'
        run_admin(f"{exe}\n& $c service uninstall 2>$null\n& $c service install {token}\nStart-Sleep 3\nsc.exe start cloudflared\nStart-Sleep 2\nsc.exe query cloudflared", log)
        return self.tail(log, "Nối Cloudflare", 180, wait=wait)

    def cloudflare(self):
        t = self.cf_token()
        if t: self.cf_install(t)

    def start_all(self):
        if not os.path.isfile(os.path.join(B, "platform-tools", "adb.exe")): self.say("Tổng quan", "✗ Chưa cài — bấm ① Cài đặt trước"); return
        if len(conf().get("SUPABASE_SERVICE_KEY", "")) < 30: self.say("Tổng quan", "✗ Chưa có khoá Supabase — bấm ② trước"); return
        subprocess.run([os.path.join(B, "platform-tools", "adb.exe"), "start-server"], capture_output=True, creationflags=NOWIN, env=ENV)
        for n in SERVICES: self.svcs[n].start(); time.sleep(0.5)
    def stop_all(self):
        for s in self.svcs.values(): s.stop()

    def test_phone(self):
        if not messagebox.askyesno("Tạo máy thử", "Tạo máy MEmu tên 'test1' (4GB RAM, 4 nhân) để thử? Mất 1–3 phút."): return
        self.job("Tạo máy thử test1", [PY, "phonectl.py", "create", "test1", "--ram", "4", "--cpu", "4"])

    def remake(self):
        def ask(code):
            txt = self.texts["Việc đang làm"].get("1.0", "end")
            m = re.findall(r"cần làm mới trên Windows: (\d+)", txt)
            if not m or m[-1] == "0": return
            if messagebox.askyesno("Làm mới máy khách", f"Tạo lại {m[-1]} máy khách trên MEmu (giữ chủ máy, cấu hình, hạn dùng; app/dữ liệu cũ MẤT)?\n"
                                   "Nên báo khách trước. Làm từng máy một, có thể mất khá lâu."):
                self.job("Làm mới máy khách", [PY, "run.py", "remake.py", "all"])
        self.job("Máy khách cần làm mới", [PY, "run.py", "remake.py", "list"], done=ask)

    # ================= TỰ ĐỘNG A→Z =================
    def _ui(self, fn, *a):
        """Gọi hộp thoại trên luồng giao diện rồi chờ kết quả."""
        ev, box = threading.Event(), {}
        def w():
            try: box["r"] = fn(*a)
            finally: ev.set()
        self.root.after(0, w); ev.wait(); return box.get("r")

    def auto(self):
        if getattr(self, "_auto_on", False): return
        self._auto_on = True
        threading.Thread(target=self._auto, daemon=True).start()

    def _auto(self):
        S = lambda m: self.say("Tổng quan", m)
        res = []
        def step(name, ok, note=""):
            res.append((name, ok, note)); S(("✓ " if ok else "✗ ") + name + (f" — {note}" if note else ""))
        try:
            self.root.after(0, self.nb.select, 0)
            S("\n🚀 ========== TỰ ĐỘNG A→Z: bắt đầu ==========")
            # 1) cài đặt
            if not (os.path.isfile(os.path.join(B, "platform-tools", "adb.exe")) and os.path.isfile(os.path.join(B, "ws-scrcpy", "dist", "index.js"))):
                S("1/6 Đang cài đặt (Windows sẽ hỏi quyền Admin → bấm Yes)…")
                self.stop_all(); log = os.path.join(LOGS, "setup.log")
                try: os.remove(log)
                except OSError: pass
                run_admin(f"Set-Location C:\\\ncurl.exe -fsSL -o C:\\blue\\setup.ps1 {RAW}/windows/setup.ps1\n& C:\\blue\\setup.ps1", log)
                self.tail(log, "Cài đặt", 3600, wait=True)
            ok = os.path.isfile(os.path.join(B, "ws-scrcpy", "dist", "index.js"))
            step("1/6 Cài đặt phần mềm", ok, "" if ok else "cài chưa xong — xem tab Việc đang làm")
            if not ok: return
            # 2) khoá Supabase (+ thử khoá)
            c = conf()
            if len(c.get("SUPABASE_SERVICE_KEY", "")) < 30: self._ui(self.set_key)
            c = conf(); okk = False
            try:
                rq = urllib.request.Request(c["SUPABASE_URL"].rstrip("/") + "/rest/v1/cloudphone_pricing?select=id&limit=1",
                                            headers={"apikey": c["SUPABASE_SERVICE_KEY"], "Authorization": "Bearer " + c["SUPABASE_SERVICE_KEY"]})
                okk = urllib.request.urlopen(rq, timeout=15).status == 200
            except Exception as e: S(f"   thử khoá: {e}")
            step("2/6 Khoá Supabase", okk, "" if okk else "khoá sai hoặc chưa dán — bấm ② dán lại")
            if not okk: return
            # 3) Cloudflare
            q = subprocess.run(["sc", "query", "cloudflared"], capture_output=True, text=True, creationflags=NOWIN).stdout or ""
            if "SERVICE_NAME" not in q:
                t = self._ui(self.cf_token)
                if t: self.cf_install(t, wait=True)
            elif "RUNNING" not in q:
                run_admin("sc.exe start cloudflared", os.path.join(LOGS, "cfstart.log")); time.sleep(8)
            q = subprocess.run(["sc", "query", "cloudflared"], capture_output=True, text=True, creationflags=NOWIN).stdout or ""
            step("3/6 Đường hầm Cloudflare", "RUNNING" in q, "" if "RUNNING" in q else "chưa chạy — bấm ③ dán lại lệnh connector")
            # 4) bật dịch vụ
            self.start_all()
            t0 = time.time()
            while time.time() - t0 < 90 and not (port_open(8000) and port_open(8010)): time.sleep(2)
            okd = port_open(8000) and port_open(8010) and self.svcs["cloudphoned"].alive()
            step("4/6 Bật trình phát hình + cổng bảo vệ + nhận đơn", okd, "" if okd else "xem tab ws-scrcpy / cpapi / cloudphoned")
            # 5) thử MEmu
            code, out = self.job("Danh sách máy", [PY, "phonectl.py", "list"], wait=True)
            if "chưa có máy nào" in out:
                S("5/6 Đang tạo máy thử test1 trên MEmu (1–3 phút)…")
                code, out = self.job("Tạo máy thử test1", [PY, "phonectl.py", "create", "test1", "--ram", "4", "--cpu", "4"], wait=True)
                okm = code == 0 and "✅" in out and "sẵn sàng" in out
                step("5/6 Tạo + điều khiển máy MEmu", okm, "" if okm else "lỗi tạo máy — chụp tab Việc đang làm gửi hỗ trợ")
                if okm: self.job("Xoá máy thử", [PY, "phonectl.py", "delete", "test1"], wait=True)
            else:
                step("5/6 Máy MEmu", True, "đã có máy đang chạy")
            # 6) web
            okw, note = False, ""
            try:
                rr = urllib.request.urlopen(urllib.request.Request("https://phone.blueroblox.shop/", headers={"User-Agent": "BlueRoblox-panel"}), timeout=15)
                okw = True; note = f"HTTP {rr.status}"
            except urllib.error.HTTPError as e:
                okw = e.code < 500; note = f"HTTP {e.code}" + ("" if okw else " — đường hầm chưa tới được máy này")
            except Exception as e: note = str(e)[:80]
            step("6/6 Web phone.blueroblox.shop", okw, note)
            bad = [r for r in res if not r[1]]
            S("🎉 ========== XONG: mọi thứ đã chạy! ==========" if not bad else f"⚠ ========== XONG, còn {len(bad)} mục lỗi (dòng ✗ ở trên) ==========")
            if not bad:
                code, out = self.job("Máy khách cần làm mới", [PY, "run.py", "remake.py", "list"], wait=True)
                m = re.findall(r"cần làm mới trên Windows: (\d+)", out)
                if m and m[-1] != "0" and self._ui(messagebox.askyesno, "Làm mới máy khách",
                        f"Có {m[-1]} máy khách cũ (từ server Ubuntu). Tạo lại tất cả trên MEmu ngay?\n(giữ chủ máy, cấu hình, hạn dùng; app/dữ liệu cũ mất)"):
                    self.job("Làm mới máy khách", [PY, "run.py", "remake.py", "all"], wait=True)
        except Exception as e:
            S(f"✗ Tự động bị lỗi: {e}")
        finally:
            self._auto_on = False

    def on_close(self):
        if any(s.alive() for s in self.svcs.values()):
            if not messagebox.askyesno("Thoát", "Thoát bảng điều khiển sẽ TẮT dịch vụ (khách không dùng được máy). Vẫn thoát?"): return
        self.stop_all(); self.root.destroy()

if __name__ == "__main__":
    try:   # chỉ mở 1 bảng điều khiển
        _lock = socket.socket(); _lock.bind(("127.0.0.1", 47123))
    except OSError:
        ctypes.windll.user32.MessageBoxW(0, "Bảng điều khiển BlueRoblox đang mở rồi.", "BlueRoblox", 0); sys.exit(0)
    App(auto="--auto" in sys.argv).root.mainloop()
