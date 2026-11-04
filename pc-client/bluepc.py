# -*- coding: utf-8 -*-
# =====================================================================================================
#  BlueRoblox PC — app máy tính cho KHÁCH: đăng nhập, xem danh sách máy, điều khiển máy mượt (giải mã phần cứng/CPU)
#
#  Chạy thử:   python bluepc.py                 (cần: pip install av pygame-ce numpy)
#  Đóng gói:   build.bat  → dist\BlueRobloxPC\BlueRobloxPC.exe (+ BlueRobloxPC.zip để đưa lên web)
#  Mở thẳng 1 máy:  BlueRobloxPC.exe blueroblox://phone/<mã máy>   (web có nút "Mở bằng app máy tính")
#
#  Mỗi cửa sổ máy là 1 tiến trình riêng (mở nhiều máy cùng lúc không giật nhau).
#  Không chứa khoá bí mật nào: chỉ khoá công khai Supabase (giống web/app Android) + phiên đăng nhập của chính khách.
# =====================================================================================================
import datetime, json, os, queue, re, subprocess, sys, threading, time, traceback, urllib.error, urllib.parse, urllib.request, webbrowser

APP_VER = "1.0.0"
SB = "https://nprftgodswlpzomcttal.supabase.co"
KEY = "sb_publishable_qfNcnOMv70nq5LIlYXDJzg_UtdWHVze"          # khoá CÔNG KHAI (giống web + app Android)
WEB = "https://blueroblox.shop"
PHONE_API = "https://phone.blueroblox.shop"
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
sys.path.insert(0, HERE)
DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BlueRobloxPC")
os.makedirs(DATA, exist_ok=True)
SESS, LOCKF, LOGF, PREFS = (os.path.join(DATA, x) for x in ("session.json", "session.lock", "log.txt", "settings.json"))
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def log(*a):
    try:
        with open(LOGF, "a", encoding="utf-8") as f: f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
        if os.path.getsize(LOGF) > 2_000_000: os.replace(LOGF, LOGF + ".old")
    except Exception: pass


def prefs(upd=None):
    try: p = json.load(open(PREFS, encoding="utf-8"))
    except Exception: p = {}
    if upd:
        p.update(upd)
        try: json.dump(p, open(PREFS, "w", encoding="utf-8"))
        except Exception: pass
    return p


# ================================================================ HTTP + Supabase
ERRORS = {
    "Invalid login credentials": "Sai email hoặc mật khẩu.", "Email not confirmed": "Email chưa xác nhận — mở hộp thư để xác nhận.",
    "SESSION_EXPIRED": "Phiên đăng nhập hết hạn, hãy đăng nhập lại.", "NOT_LOGGED_IN": "Hãy đăng nhập.",
    "NOT_ALLOWED": "Bạn không có quyền mở máy này.", "NOT_FOUND": "Không tìm thấy máy.", "MAINTENANCE": "Hệ thống đang bảo trì.",
}


class ApiError(Exception):
    def __init__(self, code, msg): super().__init__(msg); self.code = code


def http(method, url, body=None, headers=None, timeout=25):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    h = {"Accept": "application/json", "User-Agent": f"BlueRobloxPC/{APP_VER}"}
    if isinstance(body, (dict, list)): h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(0, "Không có kết nối mạng.") from e


def err_text(code, raw):
    try:
        j = json.loads(raw.decode("utf-8") or "{}")
        m = next((j.get(k) for k in ("error_description", "msg", "message", "error") if j.get(k)), "") if isinstance(j, dict) else ""
    except Exception:
        m = ""
    for k, v in ERRORS.items():
        if k in (m or ""): return v
    return m or f"Lỗi {code}"


class FileLock:
    def __init__(self, path): self.path = path; self.f = None
    def __enter__(self):
        self.f = open(self.path, "a+")
        for _ in range(200):
            try:
                if os.name == "nt":
                    import msvcrt; self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl; fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                time.sleep(0.05)
        return self
    def __exit__(self, *a):
        try:
            if os.name == "nt":
                import msvcrt; self.f.seek(0); msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl; fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        except OSError: pass
        self.f.close()


class Auth:
    """Phiên đăng nhập dùng chung giữa cửa sổ danh sách và các cửa sổ máy (khoá file khi làm mới mã)."""
    def __init__(self): self.s = self._load()

    def _load(self):
        try: return json.load(open(SESS, encoding="utf-8"))
        except Exception: return {}

    def _save(self, j):
        self.s = {"access": j.get("access_token", ""), "refresh": j.get("refresh_token") or self.s.get("refresh", ""),
                  "exp": time.time() + float(j.get("expires_in") or 3600) - 90,
                  "uid": (j.get("user") or {}).get("id") or self.s.get("uid", ""), "email": (j.get("user") or {}).get("email") or self.s.get("email", "")}
        tmp = SESS + ".tmp"; json.dump(self.s, open(tmp, "w", encoding="utf-8")); os.replace(tmp, SESS)

    @property
    def logged_in(self): return bool(self._load().get("refresh"))

    @property
    def email(self): return self.s.get("email", "")

    @property
    def uid(self): return self.s.get("uid", "")

    def login(self, email, pw):
        code, raw = http("POST", SB + "/auth/v1/token?grant_type=password", {"email": email.strip(), "password": pw}, {"apikey": KEY})
        if code != 200: raise ApiError(code, err_text(code, raw))
        with FileLock(LOCKF): self._save(json.loads(raw))

    def logout(self):
        self.s = {}
        try: os.remove(SESS)
        except OSError: pass

    def token(self, force=False):
        with FileLock(LOCKF):
            self.s = self._load()                     # cửa sổ khác có thể vừa làm mới
            if not force and self.s.get("access") and self.s.get("exp", 0) > time.time(): return self.s["access"]
            if not self.s.get("refresh"): raise ApiError(401, "Hãy đăng nhập.")
            code, raw = http("POST", SB + "/auth/v1/token?grant_type=refresh_token", {"refresh_token": self.s["refresh"]}, {"apikey": KEY})
            if code != 200:
                if code in (400, 401): self.logout(); raise ApiError(401, "Phiên đăng nhập hết hạn, hãy đăng nhập lại.")
                raise ApiError(code, err_text(code, raw))
            self._save(json.loads(raw))
            return self.s["access"]

    def rest(self, path):
        code, raw = http("GET", SB + "/rest/v1/" + path, headers={"apikey": KEY, "Authorization": "Bearer " + self.token()})
        if code == 401:
            code, raw = http("GET", SB + "/rest/v1/" + path, headers={"apikey": KEY, "Authorization": "Bearer " + self.token(True)})
        if code >= 300: raise ApiError(code, err_text(code, raw))
        return json.loads(raw.decode("utf-8") or "null")

    def rpc(self, fn, args=None):
        code, raw = http("POST", SB + "/rest/v1/rpc/" + fn, args or {}, {"apikey": KEY, "Authorization": "Bearer " + self.token()})
        if code >= 300: raise ApiError(code, err_text(code, raw))
        t = raw.decode("utf-8").strip()
        return json.loads(t) if t else None

    def phones(self):
        own = self.rest(f"cloud_phones?user_id=eq.{self.uid}&status=in.(active,pending,expired,error)&order=created_at.asc&select=*") or []
        try:
            for p in self.rpc("cp_shared_with_me") or []: p["shared"] = True; own.append(p)
        except Exception: pass
        return own

    def cp(self, method, base, path, body=None):
        code, raw = http(method, f"{base}/cpapi/{path}", body if body is not None else ({} if method == "POST" else None),
                         {"Authorization": "Bearer " + self.token()}, timeout=60)
        if code >= 300: raise ApiError(code, err_text(code, raw))
        try: return json.loads(raw.decode("utf-8") or "{}")
        except Exception: return {}


# ================================================================ Windows: đăng ký link blueroblox:// + bộ nhớ copy
def register_protocol():
    if os.name != "nt": return
    try:
        import winreg
        cmd = f'"{sys.executable}" "%1"' if FROZEN else f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.abspath(__file__)}" "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\blueroblox") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:BlueRoblox"); winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\blueroblox\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    except Exception as e:
        log("register_protocol:", e)


def clip_get():
    if os.name == "nt":
        try:
            import ctypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            u.GetClipboardData.restype = ctypes.c_void_p; k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            if not u.OpenClipboard(None): return ""
            try:
                h = u.GetClipboardData(13)
                if not h: return ""
                p = k.GlobalLock(h)
                try: return ctypes.wstring_at(p) if p else ""
                finally: k.GlobalUnlock(h)
            finally: u.CloseClipboard()
        except Exception: return ""
    try:
        import pygame; return pygame.scrap.get_text() or ""
    except Exception: return ""


def clip_put(text):
    if os.name == "nt":
        try:
            import ctypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            k.GlobalAlloc.restype = ctypes.c_void_p; k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]; k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]; u.SetClipboardData.restype = ctypes.c_void_p
            data = (text + "\0").encode("utf-16-le")
            h = k.GlobalAlloc(0x0042, len(data))
            p = k.GlobalLock(h); ctypes.memmove(p, data, len(data)); k.GlobalUnlock(h)
            if u.OpenClipboard(None):
                try: u.EmptyClipboard(); u.SetClipboardData(13, h)
                finally: u.CloseClipboard()
            return
        except Exception: pass
    try:
        import pygame; pygame.scrap.put_text(text)
    except Exception: pass


def spawn_viewer(pid):
    args = [sys.executable, "--view", pid] if FROZEN else [sys.executable, os.path.abspath(__file__), "--view", pid]
    kw = {}
    if os.name == "nt": kw["creationflags"] = 0x00000008 | 0x00000200       # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, **kw)


# ================================================================ CỬA SỔ MÁY (pygame + PyAV)
AK = {  # phím máy tính → mã phím Android
    "backspace": 67, "delete": 112, "return": 66, "enter": 66, "tab": 61, "escape": 111, "up": 19, "down": 20, "left": 21, "right": 22,
    "home": 122, "end": 123, "page up": 92, "page down": 93, "insert": 124,
}
META_CTRL, META_SHIFT, META_ALT = 0x1000, 0x1, 0x2
QUALITIES = [("auto", "Tự động"), ("hd", "Siêu nét"), ("bal", "Cân bằng"), ("eco", "Tiết kiệm")]
TOOLS = [("back", "Quay lại"), ("home", "Màn chính"), ("recents", "Đa nhiệm"), ("volup", "Âm +"), ("voldown", "Âm −"),
         ("rotate", "Xoay"), ("paste", "Dán chữ"), ("quality", "Chất lượng"), ("full", "Toàn màn"), ("info", "Thông số")]
BAR = 72
C_BG, C_BAR, C_ICON, C_HOT, C_TXT, C_DIM, C_ACC = (8, 12, 22), (17, 24, 39), (210, 220, 235), (47, 123, 255), (235, 240, 248), (130, 142, 160), (47, 123, 255)


class Viewer:
    def __init__(self, pid):
        self.pid = pid; self.auth = Auth()
        self.lock = threading.Lock(); self.frame = None; self.vw = self.vh = 0
        self.state = ("connecting", "Đang kết nối…"); self.stats = {}; self.ping = 0; self.path = ""
        self.show_info = bool(prefs().get("info")); self.q = prefs().get("q", "auto")
        self.name = "BlueRoblox"; self.dec = None; self.need_key = True; self.last_kf_req = 0
        self.dec_errors = 0; self.mouse_down = False; self.kicked = False; self.cli = None; self.hover = None
        self.fps_n = 0; self.fps_t = time.time(); self.fps_show = 0.0
        self._ui_key = None; self._ui_surf = None; self._ui_tex = None

    # ---------- mạng ----------
    def connect(self):
        import brstream
        ep = self.auth.rpc("cloudphone_endpoint", {"p_id": self.pid}) or {}
        self.name = ep.get("name") or self.name
        if ep.get("status") and ep.get("status") != "active":
            raise ApiError(0, "Máy chưa chạy (đang tạo, tạm dừng hoặc đã hết hạn).")
        pref = prefs().get("path_" + str(ep.get("node")))
        self.cli = brstream.StreamClient(ep, lambda force=False: self.auth.token(force), self.pid, self.on_event, q=self.q, client="pc",
                                         prefer=tuple(pref) if pref else None)
        self.node = ep.get("node")
        self.cli.start()

    def on_event(self, k, d):
        if k == "video": self._decode(*d)
        elif k == "welcome":
            self.name = d.get("name") or self.name; self._reset_decoder(); self._post()
        elif k == "size": self._reset_decoder(); self.sized_once = False; self._post()
        elif k == "clip": clip_put(d)
        elif k == "stats": self.stats = d; self._post()
        elif k == "ping": self.ping = d
        elif k == "state":
            if d[0] == "closed" and self.state[0] in ("kicked", "error"): return      # giữ thông báo lỗi / "mở ở nơi khác"
            self.state = d
            if d[0] == "connected":
                self.path = d[1]
                if self.cli and self.cli.prefer: prefs({"path_" + str(self.node): list(self.cli.prefer)})
            if d[0] in ("reconnecting", "connecting"): self.need_key = True
            if d[0] == "kicked": self.kicked = True
            self._post()

    # ---------- giải mã ----------
    def _reset_decoder(self):
        import av
        ctx = av.CodecContext.create("h264", "r")
        try: ctx.thread_count = 1
        except Exception: pass
        try: ctx.thread_type = "SLICE"
        except Exception: pass
        try: ctx.options = {"flags": "+low_delay", "flags2": "+fast"}
        except Exception: pass
        self.dec = ctx; self.need_key = True

    def _decode(self, flags, pts, data):
        import av
        if self.dec is None: self._reset_decoder()
        if self.need_key and not (flags & 1): return self._ask_key()
        try:
            frames = self.dec.decode(av.Packet(data))
        except Exception as e:
            self.dec_errors += 1; log("decode:", e); self._reset_decoder(); return self._ask_key()
        self.need_key = False
        for fr in frames:
            arr = fr.to_ndarray(format="rgb24")
            with self.lock: self.frame = arr; self.vw, self.vh = fr.width, fr.height
            self.fps_n += 1
            self._post(new_frame=True)

    def _ask_key(self):
        if time.time() - self.last_kf_req > 1.0 and self.cli:
            self.last_kf_req = time.time(); self.cli.keyframe()

    def _post(self, new_frame=False):
        try:
            import pygame
            pygame.event.post(pygame.event.Event(self.EV_FRAME if new_frame else self.EV_UI))
        except Exception: pass

    # ---------- vòng chính ----------
    def run(self):
        import pygame
        pygame.init()
        self.EV_FRAME = pygame.event.custom_type(); self.EV_UI = pygame.event.custom_type()
        try: pygame.scrap.init()
        except Exception: pass
        self.font = pygame.font.SysFont("segoeui,arial", 15); self.font_s = pygame.font.SysFont("segoeui,arial", 12); self.font_b = pygame.font.SysFont("segoeui,arial", 18, bold=True)
        sw, sh = (pygame.display.get_desktop_sizes() or [(1280, 800)])[0]
        vh = int(sh * 0.82); vw = int(vh * 9 / 16)
        self.gpu = False
        try:
            from pygame._sdl2.video import Window, Renderer, Texture
            self.Texture = Texture
            self.win = Window("BlueRoblox", size=(vw + BAR, vh), resizable=True)
            self.ren = Renderer(self.win, vsync=False, accelerated=1)
            self.gpu = True
        except Exception as e:
            log("GPU renderer không dùng được, dùng CPU:", e)
            self.screen = pygame.display.set_mode((vw + BAR, vh), pygame.RESIZABLE)
        self.sized_once = False
        pygame.key.start_text_input()
        threading.Thread(target=self._connect_bg, daemon=True).start()
        clock = pygame.time.Clock(); running = True; dirty = True
        while running:
            evs = [pygame.event.wait(250)] + pygame.event.get()
            for e in evs:
                if e.type in (pygame.QUIT, getattr(pygame, "WINDOWCLOSE", -1)): running = False
                elif e.type == self.EV_FRAME: dirty = True
                elif e.type == self.EV_UI: dirty = True
                elif e.type in (getattr(pygame, "WINDOWRESIZED", -1), getattr(pygame, "VIDEORESIZE", -1), getattr(pygame, "WINDOWEXPOSED", -1)): dirty = True
                else:
                    dirty |= self._input(e, pygame)
            if self.vw and not self.sized_once: self._fit_window(pygame); dirty = True
            if dirty: self._draw(pygame); dirty = False
            clock.tick(500)
        if self.cli: self.cli.close()
        pygame.quit()

    def _connect_bg(self):
        try: self.connect()
        except Exception as e:
            self.state = ("error", str(e)); log("connect:", traceback.format_exc()); self._post()

    def _fit_window(self, pygame):
        """Lần đầu có hình: đổi cỡ cửa sổ theo đúng tỉ lệ màn hình máy."""
        self.sized_once = True
        try:
            sw, sh = pygame.display.get_desktop_sizes()[0]
            h = int(sh * 0.86); w = int(h * self.vw / self.vh)
            if w + BAR > sw * 0.95: w = int(sw * 0.95) - BAR; h = int(w * self.vh / self.vw)
            if self.gpu:
                self.win.size = (w + BAR, h); self.win.position = ((sw - w - BAR) // 2, max(0, (sh - h) // 2 - 10))
                self.win.title = f"{self.name} — BlueRoblox"
            else:
                self.screen = pygame.display.set_mode((w + BAR, h), pygame.RESIZABLE); pygame.display.set_caption(f"{self.name} — BlueRoblox")
        except Exception as e: log("fit:", e)

    def _size(self):
        if self.gpu: return self.win.size
        return self.screen.get_size()

    def _video_rect(self):
        W, H = self._size(); aw = max(1, W - BAR)
        if not self.vw: return (0, 0, aw, H)
        sc = min(aw / self.vw, H / self.vh); w, h = int(self.vw * sc), int(self.vh * sc)
        return ((aw - w) // 2, (H - h) // 2, w, h)

    # ---------- vẽ ----------
    def _draw(self, pygame):
        W, H = self._size()
        with self.lock: arr = self.frame
        surf_v = pygame.image.frombuffer(arr if arr.flags["C_CONTIGUOUS"] else arr.tobytes(), (arr.shape[1], arr.shape[0]), "RGB") if arr is not None else None
        key = (W, H, self.hover, self.q, tuple(self.state[:2]), self.show_info, surf_v is not None,
               int(self.fps_show) if self.show_info else 0, self.stats.get("kbps") if self.show_info else 0, self.ping if self.show_info else 0, self.path)
        if key != self._ui_key:                          # lớp nút / chữ chỉ vẽ lại khi có thay đổi (đỡ tốn CPU mỗi khung)
            ui = pygame.Surface((W, H), pygame.SRCALPHA); ui.fill((0, 0, 0, 0))
            self._draw_bar(pygame, ui, W, H)
            self._draw_overlay(pygame, ui, W, H, surf_v is not None)
            self._ui_surf = ui; self._ui_key = key
            self._ui_tex = self.Texture.from_surface(self.ren, ui) if self.gpu else None
        x, y, w, h = self._video_rect()
        if self.gpu:
            r = self.ren; r.draw_color = C_BG; r.clear()
            if surf_v is not None: self.Texture.from_surface(r, surf_v).draw(dstrect=(x, y, w, h))
            self._ui_tex.draw(dstrect=(0, 0, W, H))
            r.present()
        else:
            s = self.screen; s.fill(C_BG)
            if surf_v is not None: s.blit(pygame.transform.smoothscale(surf_v, (w, h)) if (w, h) != surf_v.get_size() else surf_v, (x, y))
            s.blit(self._ui_surf, (0, 0)); pygame.display.flip()
        if time.time() - self.fps_t >= 1:
            self.fps_show = self.fps_n / (time.time() - self.fps_t); self.fps_n = 0; self.fps_t = time.time()

    def _tool_rects(self, W, H):
        n = len(TOOLS); step = max(44, min(64, (H - 20) // n)); top = max(8, (H - step * n) // 2)
        return [(name, label, (W - BAR + 6, top + i * step, BAR - 12, step - 6)) for i, (name, label) in enumerate(TOOLS)]

    def _draw_bar(self, pygame, s, W, H):
        pygame.draw.rect(s, C_BAR + (255,), (W - BAR, 0, BAR, H))
        for name, label, (x, y, w, h) in self._tool_rects(W, H):
            hot = self.hover == name
            if hot: pygame.draw.rect(s, (47, 123, 255, 70), (x, y, w, h), border_radius=10)
            cx, cy = x + w // 2, y + int(h * 0.38); c = C_HOT if hot else C_ICON; r = max(7, min(11, h // 5))
            if name == "back": pygame.draw.polygon(s, c, [(cx + r, cy - r), (cx - r, cy), (cx + r, cy + r)], 2)
            elif name == "home": pygame.draw.circle(s, c, (cx, cy), r, 2)
            elif name == "recents": pygame.draw.rect(s, c, (cx - r, cy - r, 2 * r, 2 * r), 2, border_radius=3)
            elif name in ("volup", "voldown"):
                pygame.draw.polygon(s, c, [(cx - r, cy - 4), (cx - r // 2, cy - 4), (cx + 1, cy - r), (cx + 1, cy + r), (cx - r // 2, cy + 4), (cx - r, cy + 4)], 2)
                pygame.draw.line(s, c, (cx + 5, cy), (cx + r + 3, cy), 2)
                if name == "volup": pygame.draw.line(s, c, (cx + 5 + (r - 2) // 2, cy - (r - 2) // 2), (cx + 5 + (r - 2) // 2, cy + (r - 2) // 2), 2)
            elif name == "rotate":
                pygame.draw.arc(s, c, (cx - r, cy - r, 2 * r, 2 * r), 0.6, 5.6, 2); pygame.draw.polygon(s, c, [(cx + r - 1, cy - 7), (cx + r + 4, cy - 1), (cx + r - 6, cy - 1)])
            elif name == "paste":
                pygame.draw.rect(s, c, (cx - r + 2, cy - r + 2, 2 * r - 4, 2 * r), 2, border_radius=3); pygame.draw.rect(s, c, (cx - 4, cy - r - 1, 8, 5), 0, border_radius=2)
            elif name == "quality":
                t = self.font_s.render({"auto": "AUTO", "hd": "HD", "bal": "CB", "eco": "TK"}[self.q], True, c); s.blit(t, t.get_rect(center=(cx, cy)))
            elif name == "full":
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    ax, ay = cx + dx * r, cy + dy * r
                    pygame.draw.line(s, c, (ax, ay), (ax - dx * 6, ay), 2); pygame.draw.line(s, c, (ax, ay), (ax, ay - dy * 6), 2)
            elif name == "info":
                pygame.draw.circle(s, c, (cx, cy), r, 2); t = self.font_s.render("i", True, c); s.blit(t, t.get_rect(center=(cx, cy)))
            lb = self.font_s.render(label if name != "quality" else dict(QUALITIES)[self.q], True, C_DIM if not hot else C_TXT)
            s.blit(lb, lb.get_rect(center=(x + w // 2, y + int(h * 0.80))))

    def _draw_overlay(self, pygame, s, W, H, has_video):
        st = self.state[0]; aw = W - BAR
        if st != "connected" or not has_video:
            msg = self.state[1] if len(self.state) > 1 else ""
            if st == "connected" and not has_video: msg = "Đang lấy hình…"
            lines = [msg]
            if st == "kicked": lines = ["Máy đang được mở ở nơi khác.", "Bấm phím R hoặc bấm vào đây để mở lại tại đây."]
            if st == "error": lines = [msg, "Bấm phím R để thử lại."]
            box_h = 30 + 26 * len(lines); bw = min(aw - 40, 520)
            pygame.draw.rect(s, (10, 16, 30, 215), ((aw - bw) // 2, H // 2 - box_h // 2, bw, box_h), border_radius=14)
            for i, ln in enumerate(lines):
                t = (self.font_b if i == 0 else self.font).render(ln[:80], True, C_TXT if i == 0 else C_DIM)
                s.blit(t, t.get_rect(center=(aw // 2, H // 2 - box_h // 2 + 26 + 26 * i)))
        if self.show_info and st == "connected":
            d = self.stats
            txt = f"{self.fps_show:.0f} khung/s · {d.get('kbps', 0) / 1000:.1f} Mbps · trễ mạng {self.ping} ms · {self.path} · {str(d.get('enc', '')).upper()}"
            if d.get("drop"): txt += f" · bỏ {d['drop']}"
            t = self.font_s.render(txt, True, C_TXT)
            pygame.draw.rect(s, (0, 0, 0, 150), (6, 6, t.get_width() + 14, t.get_height() + 8), border_radius=8); s.blit(t, (13, 10))

    # ---------- chuột / bàn phím ----------
    def _norm(self, pos):
        x, y, w, h = self._video_rect()
        if not w or not h: return None
        nx, ny = (pos[0] - x) / w, (pos[1] - y) / h
        return (nx, ny) if 0 <= nx <= 1 and 0 <= ny <= 1 else None

    def _clamp(self, pos):
        x, y, w, h = self._video_rect()
        return (min(1, max(0, (pos[0] - x) / max(1, w))), min(1, max(0, (pos[1] - y) / max(1, h))))

    def _tool_at(self, pos):
        W, H = self._size()
        for name, _, (x, y, w, h) in self._tool_rects(W, H):
            if x <= pos[0] <= x + w and y <= pos[1] <= y + h: return name
        return None

    def _tool(self, name, pygame):
        c = self.cli
        if name in ("back", "home", "recents", "volup", "voldown", "rotate") and c: c.cmd(name)
        elif name == "paste" and c:
            t = clip_get()
            if t: c.clip_set(t, True)
        elif name == "quality":
            i = [q for q, _ in QUALITIES].index(self.q); self.q = QUALITIES[(i + 1) % len(QUALITIES)][0]; prefs({"q": self.q})
            if c: c.quality(self.q)
        elif name == "full": self._toggle_full(pygame)
        elif name == "info": self.show_info = not self.show_info; prefs({"info": self.show_info})

    def _toggle_full(self, pygame):
        try:
            if self.gpu:
                self.full = not getattr(self, "full", False)
                if self.full: self.win.set_fullscreen(True)
                else: self.win.set_windowed()
            else: pygame.display.toggle_fullscreen()
        except Exception as e: log("full:", e)

    def _retry(self):
        if self.state[0] in ("kicked", "error"):
            self.kicked = False; self.state = ("connecting", "Đang kết nối…"); self.need_key = True
            threading.Thread(target=self._connect_bg, daemon=True).start()

    def _input(self, e, pygame):
        c = self.cli; t = e.type
        if t == pygame.MOUSEMOTION:
            hv = self._tool_at(e.pos)
            ch = hv != self.hover; self.hover = hv
            if self.mouse_down and c: c.touch(2, 0, *self._clamp(e.pos))
            return ch
        if t == pygame.MOUSEBUTTONDOWN and e.button in (1, 2, 3):
            tl = self._tool_at(e.pos)
            if tl and e.button == 1: self._tool(tl, pygame); return True
            if self.state[0] in ("kicked", "error") and e.button == 1: self._retry(); return True
            p = self._norm(e.pos)
            if p and c:
                if e.button == 1: self.mouse_down = True; c.touch(0, 0, *p)
                elif e.button == 3: c.cmd("back")
                elif e.button == 2: c.cmd("home")
            return False
        if t == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.mouse_down and c: c.touch(1, 0, *self._clamp(e.pos))
            self.mouse_down = False; return False
        if t == pygame.MOUSEWHEEL and c:
            p = self._norm(pygame.mouse.get_pos())
            if p: c.scroll(p[0], p[1], int(-e.x * 120), int(e.y * 120))
            return False
        if t == pygame.TEXTINPUT and c:
            if e.text and not (pygame.key.get_mods() & pygame.KMOD_CTRL): c.text(e.text)
            return False
        if t in (pygame.KEYDOWN, pygame.KEYUP):
            down = t == pygame.KEYDOWN; mods = pygame.key.get_mods(); name = pygame.key.name(e.key)
            if down and e.key == pygame.K_F11: self._toggle_full(pygame); return True
            if down and e.key == pygame.K_F3: self.show_info = not self.show_info; return True
            if down and e.key == pygame.K_r and self.state[0] in ("kicked", "error"): self._retry(); return True
            if not c: return False
            if mods & pygame.KMOD_CTRL and e.key == pygame.K_v:
                if down:
                    txt = clip_get()
                    if txt: c.clip_set(txt, True)
                return False
            meta = (META_CTRL if mods & pygame.KMOD_CTRL else 0) | (META_SHIFT if mods & pygame.KMOD_SHIFT else 0) | (META_ALT if mods & pygame.KMOD_ALT else 0)
            code = AK.get(name)
            if code is None and mods & pygame.KMOD_CTRL and len(name) == 1 and name.isalpha(): code = 29 + ord(name) - ord("a")   # Ctrl+A/C/X/Z…
            if code is not None: c.key(0 if down else 1, code, meta)
            return False
        return False


def run_viewer(pid):
    try:
        Viewer(pid).run()
    except Exception:
        log("viewer:", traceback.format_exc())
        try:
            import tkinter.messagebox as mb
            mb.showerror("BlueRoblox", "Không mở được cửa sổ máy. Chi tiết trong:\n" + LOGF)
        except Exception: pass


# ================================================================ CỬA SỔ DANH SÁCH (tkinter)
BG, CARD, LINE, TXT, DIM, ACC, OK, WARN, BAD = "#0b1220", "#121b2f", "#1f2b45", "#eef2f8", "#8b97ad", "#2f7bff", "#22c38e", "#f0b429", "#ef5b5b"
STATUS = {"active": ("Đang chạy", OK), "pending": ("Đang tạo…", WARN), "expired": ("Hết hạn — chờ gia hạn", BAD), "error": ("Lỗi", BAD)}


def fmt_left(iso):
    try:
        d = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        s = (d - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if s <= 0: return "đã hết hạn"
        dd, hh, mm = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
        loc = d.astimezone().strftime("%H:%M %d/%m/%Y")
        return (f"còn {dd} ngày {hh} giờ" if dd else f"còn {hh} giờ {mm} phút") + f" · hết hạn {loc}"
    except Exception:
        return ""


# ================================================================ MUA MÁY · GIA HẠN · NẠP TIỀN (giống hệt web: cùng RPC Supabase, cùng giá)
CP_RAM, CP_CPU, CP_DAYS, CP_FPS, CP_QTY = list(range(1, 9)), list(range(1, 9)), [7, 15, 30, 60], [30, 60], [1, 2, 5, 10]
CP_RES = [("540x960", "Nhẹ máy"), ("720x1280", "HD"), ("720x1600", "HD+ dài"), ("1080x1920", "Full HD")]
CP_HARD = {"ram": 8, "cpu": 8, "fps": 60}                    # máy ảo tối đa 8GB · 8 nhân · 60 FPS (giống web)
CP_DEF = {"price_ram_gb_day": 2500, "price_cpu_day": 2000, "min_ram": 1, "max_ram": 16, "min_cpu": 1, "max_cpu": 8,
          "min_days": 1, "max_days": 90, "min_fps": 10, "max_fps": 120}
AND_HINT = {"9": "LDPlayer 9 · ổn định", "12": "MuMu · mới nhất", "7": "MuMu · nhẹ"}
RENEW_DAYS = [1, 3, 7, 15, 30, 60, 90]
TOP_AMTS = [50000, 100000, 200000, 500000, 1000000]
BANKS = {"970422": "MB Bank", "970436": "Vietcombank", "970407": "Techcombank", "970416": "ACB", "970432": "VPBank", "970418": "BIDV",
         "970415": "VietinBank", "970423": "TPBank", "970403": "Sacombank", "970405": "Agribank", "970441": "VIB", "970443": "SHB",
         "970448": "OCB", "970426": "MSB"}
ERRORS.update({                                                  # mã lỗi của SQL → câu dễ hiểu (PHONE_LOCKED phải đứng trước LOCKED)
    "INSUFFICIENT_BALANCE": "Số dư không đủ.", "PHONE_LOCKED": "Máy đang bị khoá, không gia hạn được. Liên hệ hỗ trợ.",
    "LOCKED": "Tài khoản đã bị khoá. Liên hệ hỗ trợ.", "MIN_AMOUNT": "Số tiền thấp hơn mức nạp tối thiểu.",
    "TOO_MANY": "Bạn có quá nhiều lệnh nạp đang chờ. Thử lại sau ít phút.",
    "BAD_RAM": "RAM nằm ngoài giới hạn cho phép.", "BAD_CPU": "Số nhân CPU nằm ngoài giới hạn cho phép.", "BAD_DAYS": "Số ngày nằm ngoài giới hạn cho phép.",
    "BAD_RES": "Độ phân giải không hợp lệ.", "BAD_FPS": "FPS nằm ngoài giới hạn cho phép.", "BAD_QTY": "Số lượng không hợp lệ.",
    "FPS_TOO_HIGH": "Máy dưới 4 nhân chỉ chọn tối đa 60 FPS.", "OUT_OF_STOCK": "Đã hết hàng.",
    "MAX_PER_USER": "Tài khoản đã đủ số máy cho phép — gia hạn máy đang có hoặc xoá máy cũ để thuê máy khác.",
    "FREE_OFF": "Ưu đãi miễn phí đã kết thúc.", "FREE_LIMIT": "Máy miễn phí vượt cấu hình tối đa cho phép.",
    "FREE_USED": "Mỗi tài khoản chỉ được dùng thử 1 máy miễn phí — bạn đã nhận rồi.", "FREE_ONE": "Máy miễn phí chỉ nhận 1 máy mỗi lần.",
    "FREE_NO_RENEW": "Máy miễn phí không gia hạn được — hãy thuê máy mới.",
    "OUT_OF_CAPACITY": "Máy chủ đã hết chỗ cho cấu hình này. Chọn cấu hình nhỏ hơn hoặc quay lại sau.",
    "SERVER_OFFLINE": "Máy chủ bản Android này đang bảo trì — chọn bản khác.", "BAD_ANDROID": "Bản Android này chưa bán — chọn bản khác.",
    "PHONE_GONE": "Máy đã bị xoá vì hết hạn quá 30 phút — hãy thuê máy mới.", "NO_PHONE": "Không tìm thấy máy.",
})


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def vnd(v):
    return f"{round(num(v)):,}".replace(",", ".") + "đ"


def parse_iso(s):
    try: return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError): return None


def now_utc(): return datetime.datetime.now(datetime.timezone.utc)


# ---- mã VietQR (chuẩn EMVCo / NAPAS 247) — giống hệt web: app ngân hàng quét là tự điền số tiền + nội dung ----
def _tlv(i, v): return i + str(len(v)).zfill(2) + v


def _crc16(s):
    c = 0xFFFF
    for ch in s:
        c ^= ord(ch) << 8
        for _ in range(8): c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return f"{c:04X}"


def vietqr(bin_, acc, amount, memo):
    cons = _tlv("00", "A000000727") + _tlv("01", _tlv("00", bin_) + _tlv("01", acc)) + _tlv("02", "QRIBFTTA")
    p = (_tlv("00", "01") + _tlv("01", "12") + _tlv("38", cons) + _tlv("53", "704") + _tlv("54", str(int(round(amount))))
         + _tlv("58", "VN") + _tlv("62", _tlv("08", memo)) + "6304")
    return p + _crc16(p)


def notify_admin(text):
    """Báo Telegram cho shop (giống web) — không chờ, lỗi thì thôi."""
    def job():
        try: http("POST", WEB + "/api/notify-admin", {"text": text}, timeout=8)
        except Exception: pass
    threading.Thread(target=job, daemon=True).start()


class Shop:
    """Đọc giá, chỗ trống, số dư, tài khoản ngân hàng — cùng bảng/RPC với web."""
    def __init__(self, auth): self.auth = auth

    def profile(self):
        rows = self.auth.rest(f"profiles?id=eq.{self.auth.uid}&select=*") or []
        return rows[0] if rows else {}

    def pricing(self):
        p = dict(CP_DEF)
        try:
            rows = self.auth.rest("cloudphone_pricing?id=eq.1&select=*") or []
            if rows: p.update({k: v for k, v in rows[0].items() if v is not None})
        except ApiError as e:
            if e.code == 401: raise
            log("pricing:", e)
        for k, h in (("max_ram", "ram"), ("max_cpu", "cpu"), ("free_max_ram", "ram"), ("free_max_cpu", "cpu"), ("max_fps", "fps")):
            if p.get(k) is not None: p[k] = min(int(num(p[k])) or CP_HARD[h], CP_HARD[h])     # không bán vượt sức máy ảo
        return p

    def status(self):
        try: return self.auth.rpc("cloudphone_status") or {}
        except ApiError as e:
            if e.code == 401: raise
            return {}

    def site(self):
        rows = self.auth.rest("settings?id=eq.1&select=data") or []
        return (rows[0].get("data") or {}) if rows else {}

    def live_count(self):
        try: return len(self.auth.rest(f"cloud_phones?user_id=eq.{self.auth.uid}&status=in.(pending,creating,active,expired,cancelling)&select=id") or [])
        except ApiError: return 0


def is_ctv(prof): return prof.get("ctv_status") == "approved" and num(prof.get("ctv_discount")) > 0
def ctv(prof, v): return round(v * (100 - num(prof.get("ctv_discount"))) / 100) if is_ctv(prof) else v
def per_day(pr, ram, cpu): return ram * num(pr.get("price_ram_gb_day")) + cpu * num(pr.get("price_cpu_day"))


def free_live(pr):
    if not pr.get("free_on"): return False
    u = pr.get("free_until")
    if not u: return True
    d = parse_iso(u)
    return bool(d and d > now_utc())


def and_list(pr):
    out = []
    for v in str(pr.get("android_versions") or "9").split(","):
        v = v.strip()
        if re.fullmatch(r"\d{1,2}", v) and v not in out: out.append(v)
    return out or ["9"]


def and_room(st, v):
    """Chỗ trống của máy chủ bản Android v → (online?, RAM trống, nhân trống) · None = máy chủ chưa báo theo từng bản."""
    m = st.get("android") if isinstance(st.get("android"), dict) else None
    if not m: return None
    a = m.get(v)
    if not isinstance(a, dict) or a.get("online") is False: return (False, 0, 0)
    return (True, num(a.get("free_ram")), num(a.get("free_cpu")))


def and_why(st, v, ram, cpu):
    """'' = còn chỗ · 'off' = máy chủ bản này đang bảo trì / chưa có · 'full' = không đủ RAM/CPU cho cấu hình đang chọn."""
    r = and_room(st, v)
    if r is None: return ""
    if not r[0]: return "off"
    return "full" if r[1] < ram or r[2] < cpu else ""


class Chips:
    """Hàng nút chọn 1 trong nhiều (giống web): bấm → set_(giá trị) → on_change()."""
    def __init__(self, app, parent, items, get, set_, on_change, why=None, wrap=0):
        tk = app.tk; self.get, self.why = get, why
        self.frame = tk.Frame(parent, bg=BG); self.labels = []
        row = None
        for i, (val, text) in enumerate(items):
            if row is None or (wrap and i % wrap == 0):
                row = tk.Frame(self.frame, bg=BG); row.pack(fill="x", anchor="w")
            lb = tk.Label(row, text=text, font=("Segoe UI", 10, "bold"), bg=CARD, fg=TXT, padx=12, pady=6, justify="center",
                          highlightthickness=1, highlightbackground=LINE, cursor="hand2")
            lb.pack(side="left", padx=(0, 6), pady=3)
            def click(_e, v=val):
                if self.why and self.why(v): return
                set_(v); on_change()
            lb.bind("<Button-1>", click)
            self.labels.append((val, lb))
        self.paint()

    def paint(self):
        cur = self.get()
        for v, lb in self.labels:
            off = bool(self.why and self.why(v)); on = (v == cur) and not off
            lb.config(bg=ACC if on else CARD, fg="#55607a" if off else TXT, cursor="arrow" if off else "hand2",
                      highlightbackground=ACC if on else LINE)


class Dlg:
    """Cửa sổ con (mua / gia hạn / nạp): phần trên cuộn được, phần dưới (tổng tiền + nút) đứng yên."""
    def __init__(self, app, title, w=580, h=700):
        tk = app.tk; self.app = app; self.alive = True
        self.win = d = tk.Toplevel(app.root); d.title(title); d.configure(bg=BG); d.transient(app.root)
        app.root.update_idletasks()
        sw, sh = d.winfo_screenwidth(), d.winfo_screenheight(); h = min(h, sh - 80)
        x = app.root.winfo_rootx() + (app.root.winfo_width() - w) // 2; y = app.root.winfo_rooty() + 20
        d.geometry(f"{w}x{h}+{max(0, min(x, sw - w))}+{max(0, min(y, sh - h - 40))}"); d.minsize(440, 380)
        d.bind("<Escape>", lambda e: self.close())
        d.protocol("WM_DELETE_WINDOW", self.close)
        d.after(80, self._grab)
        self.foot = tk.Frame(d, bg=CARD, highlightbackground=LINE, highlightthickness=1); self.foot.pack(side="bottom", fill="x")
        wrap = tk.Frame(d, bg=BG); wrap.pack(side="top", fill="both", expand=True)
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0); sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        self.body = tk.Frame(cv, bg=BG); win = cv.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set); cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        app.scrollers[str(d)] = cv

    def _grab(self):
        try: self.win.grab_set(); self.win.focus_force()
        except Exception: pass

    def close(self):
        self.alive = False
        self.app.scrollers.pop(str(self.win), None)
        try: self.win.grab_release()
        except Exception: pass
        try: self.win.destroy()
        except Exception: pass

    def clear(self):
        for w in self.body.winfo_children(): w.destroy()
        for w in self.foot.winfo_children(): w.destroy()

    def label(self, parent, text, font=None, fg=TXT, **kw):
        return self.app.tk.Label(parent, text=text, font=font or self.app.f_t, fg=fg, bg=parent["bg"], anchor="w", justify="left", **kw)

    def section(self, title, right=""):
        tk = self.app.tk
        f = tk.Frame(self.body, bg=BG); f.pack(fill="x", padx=18, pady=(12, 2))
        hr = tk.Frame(f, bg=BG); hr.pack(fill="x")
        self.label(hr, title, ("Segoe UI", 10, "bold"), DIM).pack(side="left")
        if right: self.label(hr, right, self.app.f_s, DIM).pack(side="right")
        return f

    def loading(self, text="Đang tải…"):
        self.clear(); self.label(self.body, text, self.app.f_t, DIM).pack(padx=18, pady=30)

    def bg(self, job, ok, fail=None):
        """Chạy việc mạng ở luồng nền → gọi ok/fail trên luồng giao diện (cửa sổ đã đóng thì thôi)."""
        def run():
            try:
                r = job()
                self.app.ui(lambda: self.alive and ok(r))
            except Exception as e:
                m = str(e) or "Có lỗi, thử lại."; log("shop:", traceback.format_exc())
                if isinstance(e, ApiError) and e.code == 401: self.app.ui(lambda: (self.close(), self.app.show_login(m)))
                elif fail: self.app.ui(lambda: self.alive and fail(m))
        threading.Thread(target=run, daemon=True).start()


class OrderDlg(Dlg):
    """Thuê máy mới — y như bảng thuê trên web (giá, giới hạn, chỗ trống từng bản Android, máy miễn phí, CTV)."""
    def __init__(self, app):
        super().__init__(app, "Thuê điện thoại đám mây", 600, 760)
        self.loading("Đang tải bảng giá…")
        def load():
            sh = app.shop
            return sh.pricing(), sh.status(), sh.profile(), sh.live_count()
        self.bg(load, self._loaded, lambda m: self.loading(m))

    def _loaded(self, r):
        self.pr, self.st, self.prof, have = r
        p = self.pr; cl = lambda v, lo, hi: max(int(num(lo)), min(int(num(hi)), v))
        mpu = int(num(p.get("max_per_user")))
        self.left = 20 if mpu <= 0 or self.prof.get("role") == "admin" else mpu - have
        if self.left <= 0:
            self.loading(f"Mỗi tài khoản chỉ được thuê tối đa {mpu} máy — bạn đã có {have} máy.\nGia hạn máy đang có, hoặc xoá máy cũ (trên web) để thuê máy khác.")
            return
        if self.st.get("maint") and self.prof.get("role") != "admin":           # admin vẫn thuê được khi bảo trì (giống web) để thử máy
            self.loading("Hệ thống đang bảo trì" + (": " + str(self.st.get("maint_msg")) if self.st.get("maint_msg") else "") + ". Vui lòng quay lại sau."); return
        self.s = {"ram": cl(2, p["min_ram"], p["max_ram"]), "cpu": cl(2, p["min_cpu"], p["max_cpu"]), "days": cl(7, p["min_days"], p["max_days"]),
                  "res": "720x1280", "fps": cl(30, p["min_fps"], p["max_fps"]), "qty": 1, "android": "9", "free": False}
        avs = and_list(p); ok = [v for v in avs if not and_why(self.st, v, self.s["ram"], self.s["cpu"])]
        self.s["android"] = "9" if "9" in ok else (ok[0] if ok else ("9" if "9" in avs else avs[0]))
        self.build()

    # ---------- vẽ ----------
    def build(self):
        tk = self.app.tk; p, s = self.pr, self.s; self.clear(); self.rows = []
        lo = lambda k: int(num(p.get(k)))
        if self.prof.get("role") == "admin" and (self.st.get("maint") or self.st.get("online") is False):
            self.label(self.body, "⚠ Đang bật BẢO TRÌ — khách không thuê được. Tài khoản admin vẫn thuê được (không giới hạn chỗ trống) để thử máy.",
                       ("Segoe UI", 10, "bold"), WARN, wraplength=540).pack(fill="x", padx=18, pady=(14, 0))
        if free_live(p):
            fb = tk.Frame(self.body, bg=CARD, highlightbackground=WARN if s["free"] else LINE, highlightthickness=1); fb.pack(fill="x", padx=18, pady=(14, 0))
            v = tk.BooleanVar(value=s["free"])
            tk.Checkbutton(fb, text=f"  ★ Nhận máy MIỄN PHÍ {lo('free_days') or 1} ngày", variable=v, command=lambda: self.set_free(v.get()),
                           font=("Segoe UI", 11, "bold"), fg=TXT, bg=CARD, selectcolor=BG, activebackground=CARD, activeforeground=TXT,
                           anchor="w", cursor="hand2").pack(fill="x", padx=10, pady=(8, 0))
            self.label(fb, f"Tối đa {lo('free_max_ram')}GB RAM · {lo('free_max_cpu')} nhân · mỗi tài khoản dùng thử 1 máy. Hết hạn máy tự tắt, không gia hạn được.",
                       self.app.f_s, DIM, wraplength=520).pack(fill="x", padx=12, pady=(0, 8))
        ch = lambda key: (lambda v: self.s.__setitem__(key, v))
        get = lambda key: (lambda: self.s[key])
        avs = and_list(p)
        f = self.section("Hệ điều hành")
        self.rows.append(Chips(self.app, f, [(v, f"Android {v}\n{AND_HINT.get(v, '')}".strip()) for v in avs], get("android"), ch("android"), self.upd,
                               why=lambda v: and_why(self.st, v, self.s["ram"], self.s["cpu"]) if self.prof.get("role") != "admin" else ""))
        self.rows[-1].frame.pack(fill="x")
        self.and_note = self.label(f, "", self.app.f_s, DIM); self.and_note.pack(fill="x")
        mr = min(lo("max_ram"), lo("free_max_ram") or 99) if s["free"] else lo("max_ram")
        mc = min(lo("max_cpu"), lo("free_max_cpu") or 99) if s["free"] else lo("max_cpu")
        f = self.section("RAM", f"{lo('min_ram')}–{mr} GB")
        self.rows.append(Chips(self.app, f, [(v, f"{v}GB") for v in CP_RAM if lo("min_ram") <= v <= mr], get("ram"), ch("ram"), self.upd)); self.rows[-1].frame.pack(fill="x")
        f = self.section("CPU (nhân)", f"{lo('min_cpu')}–{mc} nhân")
        self.rows.append(Chips(self.app, f, [(v, str(v)) for v in CP_CPU if lo("min_cpu") <= v <= mc], get("cpu"), ch("cpu"), self.upd)); self.rows[-1].frame.pack(fill="x")
        if not s["free"]:
            f = self.section("Số ngày", f"{lo('min_days')}–{lo('max_days')} ngày")
            line = tk.Frame(f, bg=BG); line.pack(fill="x")
            self.days_x = tk.Entry(line, width=6, font=("Segoe UI", 11), bg=CARD, fg=TXT, insertbackground=TXT, relief="flat")
            def typed(_e=None):
                try: v = int(self.days_x.get())
                except ValueError: return
                if lo("min_days") <= v <= lo("max_days"): self.s["days"] = v; self.upd()
            self.rows.append(Chips(self.app, line, [(v, f"{v} ngày") for v in CP_DAYS if lo("min_days") <= v <= lo("max_days")], get("days"),
                                   lambda v: (self.s.__setitem__("days", v), self.days_x.delete(0, "end")), self.upd))
            self.rows[-1].frame.pack(side="left")
            self.label(line, "  khác:", self.app.f_s, DIM).pack(side="left"); self.days_x.pack(side="left", ipady=4)
            self.days_x.bind("<KeyRelease>", typed)
        f = self.section("Độ phân giải")
        self.rows.append(Chips(self.app, f, [(r, f"{n}\n{r}") for r, n in CP_RES], get("res"), ch("res"), self.upd)); self.rows[-1].frame.pack(fill="x")
        f = self.section("FPS (khung hình / giây)")
        self.rows.append(Chips(self.app, f, [(v, f"{v} FPS") for v in CP_FPS if lo("min_fps") <= v <= lo("max_fps")], get("fps"), ch("fps"), self.upd)); self.rows[-1].frame.pack(fill="x")
        if not s["free"] and self.left > 1:
            f = self.section("Số lượng máy")
            self.rows.append(Chips(self.app, f, [(v, f"{v} máy") for v in CP_QTY if v <= min(20, self.left)], get("qty"), ch("qty"), self.upd)); self.rows[-1].frame.pack(fill="x")
        note = (p.get("order_note") or "").strip() or ("Máy mới tinh, không dính dữ liệu hay tài khoản Gmail của ai. Hết hạn máy tạm dừng — "
                                                       "gia hạn trong 30 phút, quá thời gian máy và dữ liệu bị xoá.")
        self.label(self.body, note, self.app.f_s, DIM, wraplength=530).pack(fill="x", padx=18, pady=(14, 18))
        # ---- chân: tổng tiền + nút ----
        ft = self.foot
        self.calc = self.label(ft, "", self.app.f_s, DIM); self.calc.pack(fill="x", padx=16, pady=(10, 0))
        r = tk.Frame(ft, bg=CARD); r.pack(fill="x", padx=16)
        self.total = self.label(r, "", ("Segoe UI", 18, "bold"), TXT); self.total.pack(side="left")
        self.bal = self.label(r, "", self.app.f_t, DIM); self.bal.pack(side="right")
        self.err = self.label(ft, "", self.app.f_s, BAD, wraplength=540); self.err.pack(fill="x", padx=16)
        bb = tk.Frame(ft, bg=CARD); bb.pack(fill="x", padx=16, pady=(6, 12))
        self.app.btn(bb, "Huỷ", self.close, "soft").pack(side="left")
        self.go = self.app.btn(bb, "Thuê ngay", self.buy); self.go.pack(side="right")
        self.upd()

    def set_free(self, on):
        p, s = self.pr, self.s; s["free"] = bool(on)
        if on and self.prof.get("role") != "admin":
            s["ram"] = min(s["ram"], int(num(p.get("free_max_ram"))) or s["ram"]); s["cpu"] = min(s["cpu"], int(num(p.get("free_max_cpu"))) or s["cpu"]); s["qty"] = 1
        if on: s["days"] = int(num(p.get("free_days"))) or 1
        self.build()

    def price(self):
        s = self.s
        return 0 if s["free"] else ctv(self.prof, per_day(self.pr, s["ram"], s["cpu"]) * s["days"]) * s["qty"]

    def check(self):
        p, s, st, adm = self.pr, self.s, self.st, self.prof.get("role") == "admin"
        lo = lambda k: num(p.get(k))
        if st.get("maint") and not adm: return "Hệ thống đang bảo trì, vui lòng quay lại sau."
        if st.get("online") is False and not adm: return "Máy chủ điện thoại đang tạm ngưng, vui lòng quay lại sau."
        if s["free"] and not adm:
            if s["ram"] > lo("free_max_ram"): return f"Máy miễn phí tối đa {int(lo('free_max_ram'))}GB RAM."
            if s["cpu"] > lo("free_max_cpu"): return f"Máy miễn phí tối đa {int(lo('free_max_cpu'))} nhân."
        if not lo("min_ram") <= s["ram"] <= lo("max_ram"): return f"RAM phải từ {int(lo('min_ram'))} đến {int(lo('max_ram'))} GB."
        if not lo("min_cpu") <= s["cpu"] <= lo("max_cpu"): return f"CPU phải từ {int(lo('min_cpu'))} đến {int(lo('max_cpu'))} nhân."
        if not s["free"] and not lo("min_days") <= s["days"] <= lo("max_days"): return f"Số ngày phải từ {int(lo('min_days'))} đến {int(lo('max_days'))}."
        if not lo("min_fps") <= s["fps"] <= lo("max_fps"): return f"FPS phải từ {int(lo('min_fps'))} đến {int(lo('max_fps'))}."
        if s["cpu"] < 4 and s["fps"] > 60: return "Máy dưới 4 nhân chỉ chọn tối đa 60 FPS."
        if s["qty"] > self.left and not adm: return f"Tài khoản của bạn chỉ còn được thuê thêm {self.left} máy."
        if p.get("stock") is not None and s["qty"] > lo("stock") and not adm:
            return "Đã hết hàng." if lo("stock") <= 0 else f"Chỉ còn {int(lo('stock'))} máy — chọn số lượng nhỏ hơn."
        if s["android"] not in and_list(p): return "Hãy chọn hệ điều hành (bản Android)."
        r = and_room(st, s["android"])
        if r is not None and not adm:
            if not r[0]: return f"Máy chủ Android {s['android']} đang bảo trì — chọn bản khác."
            if s["ram"] * s["qty"] > r[1] or s["cpu"] * s["qty"] > r[2]:
                return (f"Máy chủ Android {s['android']} chỉ còn trống {int(r[1])}GB RAM và {int(r[2])} nhân — chọn ít máy hơn, "
                        "cấu hình nhỏ hơn hoặc bản Android khác.")
        elif st and not adm and (s["ram"] * s["qty"] > num(st.get("free_ram")) or s["cpu"] * s["qty"] > num(st.get("free_cpu"))):
            return f"Máy chủ chỉ còn trống {int(num(st.get('free_ram')))}GB RAM và {int(num(st.get('free_cpu')))} nhân — chọn ít máy hơn hoặc cấu hình nhỏ hơn."
        return ""

    def upd(self):
        if not self.alive: return
        p, s = self.pr, self.s
        if s["android"] not in and_list(p) or (self.prof.get("role") != "admin" and and_why(self.st, s["android"], s["ram"], s["cpu"])):  # hết chỗ → tự đổi bản còn chỗ
            ok = [v for v in and_list(p) if not and_why(self.st, v, s["ram"], s["cpu"])]
            if ok: s["android"] = "9" if "9" in ok else ok[0]
        for r in self.rows: r.paint()
        bad, price, bal = self.check(), self.price(), num(self.prof.get("balance"))
        why = {v: and_why(self.st, v, s["ram"], s["cpu"]) for v in and_list(p)}           # giải thích vì sao bản Android bị mờ
        self.and_note.config(text="   ".join(f"Android {v}: " + ("máy chủ đang bảo trì" if w == "off" else "hết chỗ cho cấu hình này")
                                             for v, w in why.items() if w and self.prof.get("role") != "admin"))
        if s["free"]:
            self.calc.config(text=f"Miễn phí {int(num(p.get('free_days'))) or 1} ngày · Android {s['android']} · {s['ram']}GB · {s['cpu']} nhân")
        else:
            self.calc.config(text=f"({s['ram']}GB × {vnd(p.get('price_ram_gb_day'))} + {s['cpu']} nhân × {vnd(p.get('price_cpu_day'))}) × {s['days']} ngày"
                                  + (f" − {int(num(self.prof.get('ctv_discount')))}% CTV" if is_ctv(self.prof) else "") + (f" × {s['qty']} máy" if s["qty"] > 1 else ""))
        self.total.config(text="—" if bad else vnd(price)); self.bal.config(text="Số dư: " + vnd(bal))
        warn = "" if bad else ("Số dư không đủ — cần nạp thêm " + vnd(price - bal) + "." if price > bal
                               else "⚠ Cấu hình này chưa đủ chơi Roblox (cần tối thiểu 4GB · 4 nhân) — hợp treo app, game nhẹ." if s["ram"] < 4 or s["cpu"] < 4 else "")
        self.err.config(text=bad or warn, fg=BAD if bad or price > bal else WARN)
        self.go.config(text="Nhận máy miễn phí" if s["free"] else ("Nạp tiền" if not bad and price > bal else "Thuê ngay" + (f" {s['qty']} máy" if s["qty"] > 1 else "")),
                       state="disabled" if bad else "normal", bg=LINE if bad else ACC)

    def buy(self):
        import tkinter.messagebox as mb
        bad = self.check()
        if bad: self.err.config(text=bad, fg=BAD); return
        s, price, bal = self.s, self.price(), num(self.prof.get("balance"))
        if not s["free"] and price > bal:
            need = price - bal; self.close(); self.app.open_topup(need); return
        what = f"Android {s['android']} · {s['qty']} máy · {s['ram']}GB · {s['cpu']} nhân · " + (f"{s['days']} ngày · " if not s["free"] else "") + f"{s['res']} · {s['fps']} FPS"
        if not mb.askyesno("Xác nhận thuê máy", what + ("\n\nMáy miễn phí — không trừ tiền." if s["free"] else f"\n\nTrừ {vnd(price)} từ số dư?"), parent=self.win): return
        self.go.config(state="disabled", text="Đang tạo đơn…")
        args = {"p_ram": s["ram"], "p_cpu": s["cpu"], "p_days": s["days"], "p_res": s["res"], "p_name": None, "p_fps": s["fps"],
                "p_qty": 1 if s["free"] else s["qty"], "p_android": s["android"]}
        if s["free"]: args["p_free"] = True
        def done(_r):
            notify_admin(f"📱 <b>Đơn ĐT đám mây mới{' (MIỄN PHÍ)' if s['free'] else ''}</b> — app PC\n{s['qty']} máy × Android {s['android']} · "
                         f"{s['ram']}GB / {s['cpu']} nhân / {s['days']} ngày / {s['res']} / {s['fps']} FPS\nGiá: {vnd(price)}\n{self.app.auth.email}")
            self.close(); self.app.refresh()
            mb.showinfo("BlueRoblox", ("Đã tạo đơn " + str(s["qty"]) + " máy!" if s["qty"] > 1 else "Đã tạo đơn!") +
                        " Máy đang khởi tạo (khoảng 2–3 phút) — danh sách tự cập nhật khi máy chạy.", parent=self.app.root)
        def fail(m):
            self.err.config(text=m, fg=BAD); self.upd()
        self.bg(lambda: self.app.auth.rpc("cloudphone_order_custom", args), done, fail)


class RenewDlg(Dlg):
    """Gia hạn 1 máy: chọn số ngày → trừ số dư (giá theo RAM/CPU của máy, có giảm CTV)."""
    def __init__(self, app, phone):
        super().__init__(app, "Gia hạn " + (phone.get("name") or "máy"), 540, 520)
        self.p = phone; self.days = 30
        self.loading("Đang tải giá…")
        self.bg(lambda: (app.shop.pricing(), app.shop.profile()), self._loaded, lambda m: self.loading(m))

    def _loaded(self, r):
        tk = self.app.tk; self.pr, self.prof = r; p = self.p; self.clear()
        mx = int(num(self.pr.get("max_days"))) or 90; self.days = min(30, mx)
        self.label(self.body, p.get("name") or "Máy", self.app.f_h2).pack(fill="x", padx=18, pady=(16, 0))
        self.label(self.body, f"{p.get('ram')}GB RAM · {p.get('cpu')} nhân · Android {p.get('android') or '9'} · {fmt_left(p.get('expires_at'))}",
                   self.app.f_t, DIM, wraplength=490).pack(fill="x", padx=18)
        f = self.section("Gia hạn thêm", f"tối đa {mx} ngày")
        self.ch = Chips(self.app, f, [(d, f"{d} ngày") for d in RENEW_DAYS if d <= mx], lambda: self.days, lambda v: self._set(v, True), self.upd, wrap=4)
        self.ch.frame.pack(fill="x")
        line = tk.Frame(f, bg=BG); line.pack(fill="x", pady=(4, 0))
        self.label(line, "Số ngày khác:", self.app.f_s, DIM).pack(side="left")
        self.x = tk.Entry(line, width=6, font=("Segoe UI", 11), bg=CARD, fg=TXT, insertbackground=TXT, relief="flat"); self.x.pack(side="left", padx=6, ipady=4)
        def typed(_e=None):
            try: v = int(self.x.get())
            except ValueError: return
            if 1 <= v <= mx: self._set(v, False); self.upd()
        self.x.bind("<KeyRelease>", typed)
        ft = self.foot
        self.sum = self.label(ft, "", self.app.f_t, TXT); self.sum.pack(fill="x", padx=16, pady=(10, 0))
        self.err = self.label(ft, "", self.app.f_s, BAD, wraplength=500); self.err.pack(fill="x", padx=16)
        bb = tk.Frame(ft, bg=CARD); bb.pack(fill="x", padx=16, pady=(6, 12))
        self.app.btn(bb, "Huỷ", self.close, "soft").pack(side="left")
        self.go = self.app.btn(bb, "Gia hạn", self.renew); self.go.pack(side="right")
        self.upd()

    def _set(self, v, clear):
        self.days = v
        if clear: self.x.delete(0, "end")

    def total(self): return ctv(self.prof, per_day(self.pr, int(num(self.p.get("ram"))), int(num(self.p.get("cpu"))))) * self.days

    def upd(self):
        self.ch.paint()
        per = ctv(self.prof, per_day(self.pr, int(num(self.p.get("ram"))), int(num(self.p.get("cpu"))))); tot = per * self.days; bal = num(self.prof.get("balance"))
        exp = parse_iso(self.p.get("expires_at")); base = max(now_utc(), exp) if exp else now_utc()
        new = (base + datetime.timedelta(days=self.days)).astimezone().strftime("%H:%M %d/%m/%Y")
        self.sum.config(text=f"Đơn giá {vnd(per)} / ngày" + (f" (CTV −{int(num(self.prof.get('ctv_discount')))}%)" if is_ctv(self.prof) else "")
                        + f"  ·  Hạn mới: {new}\nThanh toán: {vnd(tot)}   ·   Số dư: {vnd(bal)}")
        self.err.config(text="" if tot <= bal else "Số dư không đủ — cần nạp thêm " + vnd(tot - bal) + ".")
        self.go.config(text=f"Gia hạn {self.days} ngày" if tot <= bal else "Nạp tiền")

    def renew(self):
        import tkinter.messagebox as mb
        tot, bal = self.total(), num(self.prof.get("balance"))
        if tot > bal: self.close(); self.app.open_topup(tot - bal); return
        if not mb.askyesno("Gia hạn", f"Gia hạn “{self.p.get('name')}” thêm {self.days} ngày, trừ {vnd(tot)} từ số dư?", parent=self.win): return
        self.go.config(state="disabled", text="Đang gia hạn…"); d = self.days
        def done(_r):
            self.close(); self.app.refresh()
            mb.showinfo("BlueRoblox", f"Đã gia hạn +{d} ngày.", parent=self.app.root)
        def fail(m):
            self.err.config(text=m); self.go.config(state="normal"); self.upd()
        self.bg(lambda: self.app.auth.rpc("cloudphone_renew", {"p_id": self.p["id"], "p_days": d}), done, fail)


class TopupDlg(Dlg):
    """Nạp tiền bằng chuyển khoản: tạo mã nạp (create_topup) → mã VietQR → tự kiểm tra 5 giây/lần, ngân hàng báo có là cộng số dư."""
    def __init__(self, app, need=0):
        super().__init__(app, "Nạp tiền", 600, 640)
        self.need = need; self.top = None
        self.loading()
        self.bg(lambda: (app.shop.site(), app.shop.profile()), self._loaded, lambda m: self.loading(m))

    def _loaded(self, r):
        tk = self.app.tk; self.site, self.prof = r; self.clear()
        self.min = max(1000, int(num(self.site.get("minTop")) or 10000))
        amt = max(self.min, int(-(-self.need // 1000) * 1000)) if self.need > 0 else max(self.min, 50000)
        self.amt = amt
        self.label(self.body, "Nạp tiền vào tài khoản", self.app.f_h2).pack(fill="x", padx=18, pady=(16, 0))
        self.label(self.body, "Số dư hiện tại: " + vnd(self.prof.get("balance")) + (f"   ·   Cần thêm {vnd(self.need)}" if self.need > 0 else ""),
                   self.app.f_t, DIM).pack(fill="x", padx=18)
        f = self.section("Số tiền nạp", f"tối thiểu {vnd(self.min)}")
        items = sorted(set([a for a in TOP_AMTS if a >= self.min] + ([amt] if self.need > 0 else [])))
        self.ch = Chips(self.app, f, [(a, vnd(a)) for a in items], lambda: self.amt, lambda v: (setattr(self, "amt", v), self.x.delete(0, "end")), self.upd, wrap=3)
        self.ch.frame.pack(fill="x")
        line = tk.Frame(f, bg=BG); line.pack(fill="x", pady=(6, 0))
        self.label(line, "Số khác:", self.app.f_s, DIM).pack(side="left")
        self.x = tk.Entry(line, width=12, font=("Segoe UI", 11), bg=CARD, fg=TXT, insertbackground=TXT, relief="flat"); self.x.pack(side="left", padx=6, ipady=4)
        self.label(line, "đ", self.app.f_t, DIM).pack(side="left")
        def typed(_e=None):
            v = int(re.sub(r"\D", "", self.x.get()) or 0)
            if v: self.amt = v; self.upd()
        self.x.bind("<KeyRelease>", typed)
        self.label(self.body, "Chuyển khoản ngân hàng: quét mã QR bằng app ngân hàng bất kỳ, tiền tự cộng vào số dư sau khoảng 1 phút.",
                   self.app.f_s, DIM, wraplength=540).pack(fill="x", padx=18, pady=(14, 4))
        lk = self.label(self.body, "Nạp bằng USDT / LTC trên web ↗", self.app.f_s, ACC, cursor="hand2"); lk.pack(fill="x", padx=18)
        lk.bind("<Button-1>", lambda e: webbrowser.open(WEB + "/?topup=1"))
        ft = self.foot
        self.err = self.label(ft, "", self.app.f_s, BAD, wraplength=540); self.err.pack(fill="x", padx=16, pady=(8, 0))
        bb = tk.Frame(ft, bg=CARD); bb.pack(fill="x", padx=16, pady=(4, 12))
        self.app.btn(bb, "Huỷ", self.close, "soft").pack(side="left")
        self.go = self.app.btn(bb, "Tạo mã chuyển khoản", self.make); self.go.pack(side="right")
        if not str(self.site.get("bankAcc") or "").strip():
            self.err.config(text="Shop chưa cài tài khoản ngân hàng — tạm thời nạp trên web."); self.go.config(state="disabled", bg=LINE)
        self.upd()

    def upd(self):
        self.ch.paint()
        if self.top is None and hasattr(self, "go") and str(self.site.get("bankAcc") or "").strip():
            ok = self.amt >= self.min
            self.err.config(text="" if ok else "Số tiền tối thiểu là " + vnd(self.min) + ".")
            self.go.config(text=f"Tạo mã chuyển khoản {vnd(self.amt)}", state="normal" if ok else "disabled", bg=ACC if ok else LINE)

    def make(self):
        if self.amt < self.min: return
        self.go.config(state="disabled", text="Đang tạo mã…"); a = self.amt
        def fail(m): self.err.config(text=m); self.upd()
        self.bg(lambda: self.app.auth.rpc("create_topup", {"p_amount": a}), self._pay, fail)

    def _pay(self, t):
        tk = self.app.tk; self.top = t or {}; self.clear(); self.win.title("Chuyển khoản " + vnd(self.top.get("amount")))
        bin_ = str(self.site.get("bankBin") or "970422"); acc = re.sub(r"\D", "", str(self.site.get("bankAcc") or ""))
        name = str(self.site.get("bankName") or ""); amount = int(num(self.top.get("amount"))); memo = str(self.top.get("memo") or "")
        wrap = tk.Frame(self.body, bg=BG); wrap.pack(fill="x", padx=18, pady=16)
        cv = tk.Canvas(wrap, bg="white", highlightthickness=0)
        try:
            import qrcodegen
            q = qrcodegen.QrCode.encode_text(vietqr(bin_, acc, amount, memo), qrcodegen.QrCode.Ecc.MEDIUM)
            n = q.get_size(); sc = max(3, 248 // (n + 8)); side = (n + 8) * sc
            cv.config(width=side, height=side)
            for y in range(n):
                for x in range(n):
                    if q.get_module(x, y): cv.create_rectangle((x + 4) * sc, (y + 4) * sc, (x + 5) * sc, (y + 5) * sc, fill="black", width=0)
        except Exception:
            log("qr:", traceback.format_exc()); cv.config(width=248, height=60, bg=CARD)
            cv.create_text(124, 30, text="Không tạo được mã QR —\nchuyển khoản theo thông tin bên cạnh", fill=TXT, font=self.app.f_s)
        cv.pack(side="left", anchor="n")
        info = tk.Frame(wrap, bg=BG); info.pack(side="left", fill="both", expand=True, padx=(16, 0))
        def row(lab, val, copy=None, big=False, col=TXT):
            r = tk.Frame(info, bg=BG); r.pack(fill="x", pady=3)
            self.label(r, lab, self.app.f_s, DIM).pack(fill="x")
            vr = tk.Frame(r, bg=BG); vr.pack(fill="x")
            self.label(vr, val, ("Segoe UI", 13 if big else 11, "bold"), col).pack(side="left")
            if copy: self.app.btn(vr, "Chép", lambda: self.copy(copy), "soft", padx=8, pady=2).pack(side="right")
        row("Ngân hàng", BANKS.get(bin_, bin_)); row("Số tài khoản", acc, acc); row("Chủ tài khoản", name)
        row("Số tiền", vnd(amount), str(amount)); row("Nội dung chuyển khoản (ghi ĐÚNG)", memo, memo, True, WARN)
        self.label(self.body, "Mở app ngân hàng → Quét mã QR: số tiền và nội dung tự điền. Chuyển tay thì ghi đúng nội dung ở trên, "
                              "nếu không tiền sẽ không tự cộng. Mã có hiệu lực 20 phút.", self.app.f_s, DIM, wraplength=540).pack(fill="x", padx=18)
        ft = self.foot
        self.state = self.label(ft, "⏳ Đang chờ ngân hàng báo có… (tự kiểm tra, không cần bấm gì)", ("Segoe UI", 10, "bold"), WARN, wraplength=540)
        self.state.pack(fill="x", padx=16, pady=(10, 0))
        bb = tk.Frame(ft, bg=CARD); bb.pack(fill="x", padx=16, pady=(6, 12))
        self.done_btn = self.app.btn(bb, "Đóng", self.close, "soft"); self.done_btn.pack(side="right")
        threading.Thread(target=self._poll, daemon=True).start()

    def copy(self, v):
        try: self.app.root.clipboard_clear(); self.app.root.clipboard_append(v)
        except Exception: pass
        if hasattr(self, "state") and self.state.winfo_exists(): self.state.config(text=f"Đã chép: {v}   ·   ⏳ vẫn đang chờ ngân hàng báo có…", fg=WARN)

    def _poll(self):
        t0, tid = time.time(), self.top.get("id")
        while self.alive and time.time() - t0 < 25 * 60:
            time.sleep(5)
            if not self.alive: return
            try: rows = self.app.auth.rest(f"topups?id=eq.{tid}&select=status,amount") or []
            except Exception: continue
            if rows and rows[0].get("status") == "done":
                amt = rows[0].get("amount"); self.app.ui(lambda: self._done(amt)); return
            if not rows and time.time() - t0 > 60:
                self.app.ui(lambda: self.alive and self.state.config(
                    text="Mã nạp này đã hết hạn. Nếu bạn đã chuyển tiền mà chưa được cộng, liên hệ hỗ trợ kèm nội dung chuyển khoản.", fg=BAD)); return
        if self.alive:
            self.app.ui(lambda: self.alive and self.state.config(
                text="Chưa thấy tiền về. Nếu đã chuyển đúng nội dung mà quá 15 phút chưa cộng, liên hệ hỗ trợ kèm nội dung chuyển khoản.", fg=BAD))

    def _done(self, amt):
        if not self.alive: return
        self.state.config(text=f"✓ Đã nhận {vnd(amt)} — số dư đã được cộng.", fg=OK)
        self.done_btn.config(text="Xong", bg=ACC)
        self.app.refresh()


class UpgradeDlg(Dlg):
    """Nâng RAM / CPU cho máy đang chạy: trả thêm cho thời gian còn lại (máy chủ tính giá, có giảm CTV) — app và dữ liệu giữ nguyên."""
    def __init__(self, app, phone):
        super().__init__(app, "Nâng cấp " + (phone.get("name") or "máy"), 560, 560)
        self.p = phone; self.r0, self.c0 = int(num(phone.get("ram"))), int(num(phone.get("cpu"))); self.nr, self.nc = self.r0, self.c0
        self.cost, self.qid = 0, 0
        self.loading("Đang tải giá…")
        self.bg(lambda: (app.shop.pricing(), app.shop.profile()), self._loaded, lambda m: self.loading(m))

    def _loaded(self, r):
        tk = self.app.tk; self.pr, self.prof = r; p = self.p; self.clear()
        mr, mc = int(num(self.pr.get("max_ram"))) or 8, int(num(self.pr.get("max_cpu"))) or 8
        self.label(self.body, p.get("name") or "Máy", self.app.f_h2).pack(fill="x", padx=18, pady=(16, 0))
        self.label(self.body, f"Hiện tại {self.r0}GB RAM · {self.c0} nhân · {fmt_left(p.get('expires_at'))}", self.app.f_t, DIM, wraplength=500).pack(fill="x", padx=18)
        rams = sorted({v for v in (2, 3, 4, 6, 8, 10, 12, 16, mr) if self.r0 < v <= mr})
        cpus = sorted({v for v in (2, 3, 4, 6, 8, 10, 12, 16, mc) if self.c0 < v <= mc})
        f = self.section("RAM")
        self.cr = Chips(self.app, f, [(self.r0, f"Giữ {self.r0}GB")] + [(v, f"{v}GB") for v in rams], lambda: self.nr, lambda v: setattr(self, "nr", v), self.upd)
        self.cr.frame.pack(fill="x")
        if not rams: self.label(f, "RAM đã tối đa.", self.app.f_s, DIM).pack(fill="x")
        f = self.section("CPU")
        self.cc = Chips(self.app, f, [(self.c0, f"Giữ {self.c0} nhân")] + [(v, f"{v} nhân") for v in cpus], lambda: self.nc, lambda v: setattr(self, "nc", v), self.upd)
        self.cc.frame.pack(fill="x")
        if not cpus: self.label(f, "CPU đã tối đa.", self.app.f_s, DIM).pack(fill="x")
        self.label(self.body, "Máy tự khởi động lại khoảng 1 phút để nhận cấu hình mới — app và dữ liệu giữ nguyên. "
                              "Các lần gia hạn sau tính theo cấu hình mới.", self.app.f_s, DIM, wraplength=510).pack(fill="x", padx=18, pady=(14, 10))
        ft = self.foot
        self.sum = self.label(ft, "", self.app.f_t, TXT); self.sum.pack(fill="x", padx=16, pady=(10, 0))
        self.err = self.label(ft, "", self.app.f_s, BAD, wraplength=500); self.err.pack(fill="x", padx=16)
        bb = tk.Frame(ft, bg=CARD); bb.pack(fill="x", padx=16, pady=(6, 12))
        self.app.btn(bb, "Huỷ", self.close, "soft").pack(side="left")
        self.go = self.app.btn(bb, "Nâng cấp ngay", self.upgrade); self.go.pack(side="right")
        self.upd()

    def _local_quote(self):
        """Giá dự tính giống web (máy chủ vẫn tính lại chính xác lúc trừ tiền)."""
        exp = parse_iso(self.p.get("expires_at")); left = max(0.0, (exp - now_utc()).total_seconds() / 86400) if exp else 0.0
        raw = round(((self.nr - self.r0) * num(self.pr.get("price_ram_gb_day")) + (self.nc - self.c0) * num(self.pr.get("price_cpu_day"))) * left)
        pct = num(self.prof.get("ctv_discount")) if is_ctv(self.prof) else 0
        return int(-(-(raw * (100 - pct)) // 100))

    def upd(self):
        self.cr.paint(); self.cc.paint()
        same = self.nr == self.r0 and self.nc == self.c0
        if same: self.cost = 0; self._draw(); return
        self.cost = self._local_quote(); self._draw()
        self.qid += 1; q, nr, nc = self.qid, self.nr, self.nc
        def got(v):
            if q != self.qid: return
            try: self.cost = int(num(v))
            except Exception: pass
            self._draw()
        self.bg(lambda: self.app.auth.rpc("cloudphone_upgrade_quote", {"p_id": self.p["id"], "p_ram": nr, "p_cpu": nc}), got, lambda m: None)

    def _draw(self):
        same = self.nr == self.r0 and self.nc == self.c0; bal = num(self.prof.get("balance"))
        self.sum.config(text="Chọn RAM hoặc CPU muốn nâng." if same else
                        f"Cấu hình mới: {self.nr}GB RAM · {self.nc} nhân\nTrả thêm cho thời gian còn lại: {vnd(self.cost)}   ·   Số dư: {vnd(bal)}")
        self.err.config(text="" if same or self.cost <= bal else "Số dư không đủ — cần nạp thêm " + vnd(self.cost - bal) + ".")
        self.go.config(state="disabled" if same else "normal", bg=LINE if same else ACC, text="Nạp tiền" if not same and self.cost > bal else "Nâng cấp ngay")

    def upgrade(self):
        import tkinter.messagebox as mb
        if self.nr == self.r0 and self.nc == self.c0: return
        bal = num(self.prof.get("balance"))
        if self.cost > bal: need = self.cost - bal; self.close(); self.app.open_topup(need); return
        if not mb.askyesno("Nâng cấp", f"Nâng “{self.p.get('name')}” lên {self.nr}GB RAM · {self.nc} nhân, trừ khoảng {vnd(self.cost)} từ số dư?\n"
                                       "Máy sẽ tự khởi động lại khoảng 1 phút.", parent=self.win): return
        self.go.config(state="disabled", text="Đang nâng cấp…"); nr, nc = self.nr, self.nc
        def done(_r):
            self.close(); self.app.refresh()
            mb.showinfo("BlueRoblox", f"Đã nâng lên {nr}GB · {nc} nhân — máy nhận cấu hình mới trong khoảng 1 phút.", parent=self.app.root)
        def fail(m):
            self.err.config(text=m); self.go.config(state="normal"); self._draw()
        self.bg(lambda: self.app.auth.rpc("cloudphone_upgrade", {"p_id": self.p["id"], "p_ram": nr, "p_cpu": nc}), done, fail)


ERRORS.update({"UPGRADE_PENDING": "Máy đang được nâng cấp, đợi khoảng 1 phút rồi thử lại.", "FREE_NO_UPGRADE": "Máy miễn phí không nâng cấp được — thuê máy trả phí để nâng.",
               "PHONE_NOT_ACTIVE": "Máy phải đang chạy mới làm được việc này.", "BAD_UPGRADE": "Cấu hình nâng cấp không hợp lệ."})


class Launcher:
    def __init__(self, open_id=None):
        import tkinter as tk
        self.tk = tk; self.auth = Auth(); self.shop = Shop(self.auth); self.open_id = open_id; self.rows = []; self.busy = False
        self.scrollers = {}; self._fast = None; self._q = queue.Queue()
        self.root = tk.Tk(); self.root.title("BlueRoblox PC"); self.root.configure(bg=BG); self.root.geometry("600x760"); self.root.minsize(480, 540)
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.after(40, self._pump)
        try:
            ico = os.path.join(getattr(sys, "_MEIPASS", HERE), "icon.png")
            if os.path.isfile(ico): self.root.iconphoto(True, tk.PhotoImage(file=ico))
        except Exception: pass
        self.f_h1 = ("Segoe UI", 18, "bold"); self.f_h2 = ("Segoe UI", 12, "bold"); self.f_t = ("Segoe UI", 10); self.f_s = ("Segoe UI", 9)
        self.body = tk.Frame(self.root, bg=BG); self.body.pack(fill="both", expand=True)
        if self.auth.logged_in: self.show_list()
        else: self.show_login()

    def clear(self):
        for w in self.body.winfo_children(): w.destroy()

    def btn(self, parent, text, cmd, kind="primary", **kw):
        bg = {"primary": ACC, "ghost": CARD, "soft": LINE, "danger": "#3a1d27"}[kind]; fg = TXT
        o = dict(padx=14, pady=7); o.update(kw)
        b = self.tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground="#4b8cff" if kind == "primary" else "#2a3a5c",
                           activeforeground=TXT, relief="flat", bd=0, font=("Segoe UI", 10, "bold"), cursor="hand2", **o)
        return b

    def ui(self, fn):
        """Luồng nền gửi việc cập nhật giao diện về luồng chính (Tk chỉ an toàn khi gọi từ luồng chính)."""
        self._q.put(fn)

    def _pump(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try: fn()
                except Exception: log("ui:", traceback.format_exc())
        except queue.Empty: pass
        try: self.root.after(40, self._pump)
        except Exception: pass

    def _wheel(self, e):
        """Lăn chuột cuộn đúng cửa sổ đang trỏ vào (danh sách máy hoặc hộp thoại mua / nạp)."""
        try: cv = self.scrollers.get(str(e.widget.winfo_toplevel()))
        except Exception: return
        if cv is not None and cv.winfo_exists(): cv.yview_scroll(int(-e.delta / 120), "units")

    # ---------- mua / nạp / gia hạn / nâng cấp / huỷ (cửa sổ con) ----------
    def open_order(self): OrderDlg(self)
    def open_topup(self, need=0): TopupDlg(self, need)
    def open_renew(self, p): RenewDlg(self, p)
    def open_upgrade(self, p): UpgradeDlg(self, p)

    def cancel(self, p):
        import tkinter.messagebox as mb
        if not mb.askyesno("Huỷ máy", f"Huỷ máy “{p.get('name')}”?\n\nMáy và TOÀN BỘ dữ liệu trong máy sẽ bị XOÁ vĩnh viễn, không hoàn tiền.",
                           icon="warning", default="no", parent=self.root): return
        def job():
            try:
                self.auth.rpc("cloudphone_cancel", {"p_id": p["id"]})
                self.ui(lambda: (self.refresh(), mb.showinfo("BlueRoblox", "Đang huỷ máy — máy sẽ biến mất khỏi danh sách trong ít phút.", parent=self.root)))
            except Exception as e:
                m = str(e); self.ui(lambda m=m: mb.showerror("BlueRoblox", m, parent=self.root))
        threading.Thread(target=job, daemon=True).start()

    def more(self, p, widget):
        """Menu "Khác" của 1 máy: nâng cấp RAM/CPU, huỷ máy."""
        m = self.tk.Menu(self.root, tearoff=0, bg=CARD, fg=TXT, activebackground=ACC, activeforeground=TXT, font=("Segoe UI", 10))
        can_up = p.get("status") == "active" and not p.get("locked") and not p.get("is_free") and p.get("pending_ram") is None
        m.add_command(label="Nâng cấp RAM / CPU", command=lambda: self.open_upgrade(p), state="normal" if can_up else "disabled")
        m.add_separator()
        m.add_command(label="Huỷ máy (xoá vĩnh viễn)", command=lambda: self.cancel(p))
        try: m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally: m.grab_release()

    # ---------- đăng nhập ----------
    def show_login(self, msg=""):
        tk = self.tk; self.clear()
        box = tk.Frame(self.body, bg=BG); box.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(box, text="BlueRoblox", font=("Segoe UI", 26, "bold"), fg=TXT, bg=BG).pack()
        tk.Label(box, text="Điện thoại đám mây — điều khiển trên máy tính", font=self.f_t, fg=DIM, bg=BG).pack(pady=(2, 22))
        ents = {}
        for k, lab, show in (("email", "Email", ""), ("pw", "Mật khẩu", "•")):
            tk.Label(box, text=lab, font=self.f_t, fg=DIM, bg=BG, anchor="w").pack(fill="x")
            e = tk.Entry(box, font=("Segoe UI", 12), bg=CARD, fg=TXT, insertbackground=TXT, relief="flat", show=show, width=30)
            e.pack(fill="x", ipady=8, pady=(3, 12)); ents[k] = e
        ents["email"].insert(0, prefs().get("email", ""))
        err = tk.Label(box, text=msg, font=self.f_t, fg=BAD, bg=BG, wraplength=320, justify="left"); err.pack(fill="x")
        def go(*_):
            if self.busy: return
            em, pw = ents["email"].get().strip(), ents["pw"].get()
            if not em or not pw: err.config(text="Nhập email và mật khẩu."); return
            self.busy = True; b.config(text="Đang đăng nhập…")
            def job():
                try:
                    self.auth.login(em, pw); prefs({"email": em}); self.ui(self.show_list)
                except Exception as e:
                    m = str(e); self.ui(lambda m=m: (err.config(text=m), b.config(text="Đăng nhập")))
                finally: self.busy = False
            threading.Thread(target=job, daemon=True).start()
        b = self.btn(box, "Đăng nhập", go); b.pack(fill="x", pady=(8, 10))
        ents["pw"].bind("<Return>", go); ents["email"].bind("<Return>", lambda *_: ents["pw"].focus_set())
        lk = tk.Label(box, text="Chưa có tài khoản? Đăng ký trên web  ↗", font=self.f_t, fg=ACC, bg=BG, cursor="hand2"); lk.pack()
        lk.bind("<Button-1>", lambda *_: webbrowser.open(WEB))
        (ents["pw"] if ents["email"].get() else ents["email"]).focus_set()

    # ---------- danh sách máy ----------
    def show_list(self):
        tk = self.tk; self.clear()
        top = tk.Frame(self.body, bg=BG); top.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(top, text="Máy của tôi", font=self.f_h1, fg=TXT, bg=BG).pack(side="left")
        self.btn(top, "Đăng xuất", self.logout, "ghost").pack(side="right")
        self.btn(top, "⟳ Làm mới", self.refresh, "ghost").pack(side="right", padx=6)
        sub = tk.Frame(self.body, bg=BG); sub.pack(fill="x", padx=18)
        tk.Label(sub, text=self.auth.email, font=self.f_s, fg=DIM, bg=BG).pack(side="left")
        lk = tk.Label(sub, text="Mở web ↗", font=self.f_s, fg=ACC, bg=BG, cursor="hand2"); lk.pack(side="right")
        lk.bind("<Button-1>", lambda *_: webbrowser.open(WEB))
        shop = tk.Frame(self.body, bg=CARD, highlightbackground=LINE, highlightthickness=1); shop.pack(fill="x", padx=18, pady=(10, 0))
        bl = tk.Frame(shop, bg=CARD); bl.pack(side="left", padx=14, pady=8)
        tk.Label(bl, text="Số dư", font=self.f_s, fg=DIM, bg=CARD, anchor="w").pack(fill="x")
        self.bal = tk.Label(bl, text="…", font=("Segoe UI", 15, "bold"), fg=TXT, bg=CARD, anchor="w"); self.bal.pack(fill="x")
        self.btn(shop, "+ Thuê máy", self.open_order).pack(side="right", padx=(6, 12), pady=10)
        self.btn(shop, "Nạp tiền", self.open_topup, "soft").pack(side="right", pady=10)
        wrap = tk.Frame(self.body, bg=BG); wrap.pack(fill="both", expand=True, padx=10, pady=10)
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0); sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        self.list = tk.Frame(cv, bg=BG); win = cv.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set); cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.scrollers[str(self.root)] = cv
        self.msg = tk.Label(self.list, text="Đang tải…", font=self.f_t, fg=DIM, bg=BG); self.msg.pack(pady=30)
        self.refresh()
        self._tick()

    def _tick(self):
        if hasattr(self, "list") and self.list.winfo_exists():
            self.refresh(quiet=True)
            self.root.after(20000, self._tick)

    def refresh(self, quiet=False):
        def job():
            try:
                rows = self.auth.phones()
                try: prof = self.shop.profile()
                except ApiError as e:
                    if e.code == 401: raise
                    prof = None
                def show():
                    if prof is not None and hasattr(self, "bal") and self.bal.winfo_exists():
                        self.bal.config(text=vnd(prof.get("balance")) + (f"  · CTV −{int(num(prof.get('ctv_discount')))}%" if is_ctv(prof) else ""))
                    self.render(rows)
                self.ui(show)
            except ApiError as e:
                m = str(e)
                if e.code == 401: self.ui(lambda m=m: self.show_login(m))
                elif not quiet: self.ui(lambda m=m: self.msg.config(text=m) if self.msg.winfo_exists() else None)
            except Exception as e:
                log("refresh:", traceback.format_exc())
        threading.Thread(target=job, daemon=True).start()

    def render(self, rows):
        tk = self.tk
        if not self.list.winfo_exists(): return
        for w in self.list.winfo_children(): w.destroy()
        if self._fast: self.root.after_cancel(self._fast); self._fast = None
        if any(p.get("status") in ("pending", "cancelling") or p.get("pending_ram") is not None for p in rows):
            self._fast = self.root.after(6000, lambda: self.refresh(quiet=True))         # máy đang tạo / nâng cấp → cập nhật nhanh hơn
        if not rows:
            tk.Label(self.list, text="Bạn chưa có máy nào.", font=self.f_h2, fg=TXT, bg=BG).pack(pady=(40, 6))
            tk.Label(self.list, text="Thuê máy ngay trong app — chọn cấu hình, thanh toán bằng số dư.", font=self.f_t, fg=DIM, bg=BG).pack()
            self.btn(self.list, "+ Thuê máy", self.open_order).pack(pady=14)
            self.msg = tk.Label(self.list, bg=BG); return
        self.msg = tk.Label(self.list, bg=BG)
        for p in rows: self._card(p)
        if self.open_id:
            pid, self.open_id = self.open_id, None
            if any(str(p.get("id")) == pid for p in rows): spawn_viewer(pid)

    def _card(self, p):
        tk = self.tk
        c = tk.Frame(self.list, bg=CARD, highlightbackground=LINE, highlightthickness=1); c.pack(fill="x", padx=8, pady=6)
        row = tk.Frame(c, bg=CARD); row.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(row, text=p.get("name") or "Máy", font=self.f_h2, fg=TXT, bg=CARD).pack(side="left")
        st, col = STATUS.get(p.get("status"), (p.get("status") or "", DIM))
        tk.Label(row, text="● " + st, font=("Segoe UI", 9, "bold"), fg=col, bg=CARD).pack(side="right")
        info = f"{p.get('ram')}GB RAM · {p.get('cpu')} nhân · {p.get('res') or ''} · {p.get('fps') or 30} FPS · Android {p.get('android') or '9'}"
        if p.get("shared"): info += " · được chia sẻ"
        tk.Label(c, text=info, font=self.f_t, fg=DIM, bg=CARD, anchor="w").pack(fill="x", padx=14)
        if p.get("expires_at"): tk.Label(c, text="⏱ " + fmt_left(p["expires_at"]), font=self.f_s, fg=DIM, bg=CARD, anchor="w").pack(fill="x", padx=14, pady=(2, 0))
        if p.get("status") == "error" and p.get("error"): tk.Label(c, text=p["error"], font=self.f_s, fg=BAD, bg=CARD, anchor="w", wraplength=440, justify="left").pack(fill="x", padx=14)
        if p.get("pending_ram") is not None:
            tk.Label(c, text=f"⏳ Đang nâng cấp lên {p.get('pending_ram')}GB · {p.get('pending_cpu')} nhân…", font=self.f_s, fg=WARN, bg=CARD, anchor="w").pack(fill="x", padx=14)
        bar = tk.Frame(c, bg=CARD); bar.pack(fill="x", padx=14, pady=(10, 12))
        st = p.get("status"); active = st == "active" and not p.get("locked")
        b = self.btn(bar, "▶  Điều khiển", lambda pid=str(p["id"]): spawn_viewer(pid)); b.pack(side="left")
        if not active: b.config(state="disabled", bg=LINE)
        if active: self.btn(bar, "Khởi động lại", lambda q=p: self.reboot(q), "ghost").pack(side="left", padx=8)
        if p.get("shared"): return                                  # máy người khác chia sẻ: chỉ điều khiển
        mo = self.btn(bar, "Khác ▾", None, "soft", padx=10); mo.config(command=lambda q=p, w=mo: self.more(q, w)); mo.pack(side="right")
        if st in ("active", "expired") and not p.get("is_free"):
            self.btn(bar, "Gia hạn", lambda q=p: self.open_renew(q), "soft").pack(side="right", padx=(0, 6))

    def reboot(self, p):
        import tkinter.messagebox as mb
        if not mb.askyesno("Khởi động lại", f"Khởi động lại máy “{p.get('name')}”? Mất khoảng 1 phút."): return
        def job():
            try:
                ep = self.auth.rpc("cloudphone_endpoint", {"p_id": p["id"]}) or {}
                base = ((ep.get("stream") or {}).get("api") or PHONE_API).rstrip("/")
                self.auth.cp("POST", base, f"{p['id']}/reboot")
                self.ui(lambda: mb.showinfo("BlueRoblox", "Đang khởi động lại máy — chờ khoảng 1 phút rồi mở lại."))
            except Exception as e:
                m = str(e); self.ui(lambda m=m: mb.showerror("BlueRoblox", m))
        threading.Thread(target=job, daemon=True).start()

    def logout(self):
        self.auth.logout(); self.show_login()

    def run(self): self.root.mainloop()


def selftest(out):
    """Mở thử mọi thư viện cửa sổ máy cần (giải mã H.264, cửa sổ GPU, tkinter) → ghi kết quả ra file."""
    res = []
    try:
        import av, numpy, pygame, tkinter, ssl, brstream, qrcodegen              # noqa: F401,E401
        from pygame._sdl2.video import Window, Renderer, Texture                 # noqa: F401
        ctx = av.CodecContext.create("h264", "r"); ctx.options = {"flags": "+low_delay"}
        res.append(f"av {av.__version__} · pygame {pygame.version.ver} · numpy {numpy.__version__} · {ssl.OPENSSL_VERSION}")
        pygame.init()
        try:
            w = Window("selftest", size=(64, 64), hidden=True); r = Renderer(w)
            s = pygame.image.frombuffer(numpy.zeros((16, 16, 3), numpy.uint8).tobytes(), (16, 16), "RGB")
            Texture.from_surface(r, s).draw(dstrect=(0, 0, 16, 16)); r.present(); res.append("gpu ok")
        except Exception as e: res.append(f"gpu: {e} (dùng CPU)")
        pygame.quit()
        res.insert(0, "OK")
    except Exception:
        res.insert(0, "FAIL"); res.append(traceback.format_exc())
    with open(out, "w", encoding="utf-8") as f: f.write("\n".join(res) + "\n")


def main():
    if os.name == "nt":
        try:
            import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(2)      # chữ / hình nét trên màn hình phóng to 125–150%
        except Exception: pass
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--selftest":                 # GitHub Actions: kiểm tra bản exe có đủ thư viện trước khi đưa khách
        return selftest(args[1])
    if len(args) >= 2 and args[0] == "--view":
        return run_viewer(args[1])
    link = next((a for a in args if a.lower().startswith("blueroblox:")), "")
    m = UUID_RE.search(link)
    open_id = m.group(0) if m else None
    register_protocol()
    if open_id and Auth().logged_in:
        spawn_viewer(open_id); return                     # bấm "Mở bằng app" trên web → mở thẳng cửa sổ máy
    Launcher(open_id).run()


if __name__ == "__main__":
    main()
