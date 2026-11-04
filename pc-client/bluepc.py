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
import datetime, json, os, re, subprocess, sys, threading, time, traceback, urllib.error, urllib.parse, urllib.request, webbrowser

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


class Launcher:
    def __init__(self, open_id=None):
        import tkinter as tk
        self.tk = tk; self.auth = Auth(); self.open_id = open_id; self.rows = []; self.busy = False
        self.root = tk.Tk(); self.root.title("BlueRoblox PC"); self.root.configure(bg=BG); self.root.geometry("560x720"); self.root.minsize(460, 520)
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
        bg = {"primary": ACC, "ghost": CARD, "danger": "#3a1d27"}[kind]; fg = TXT
        b = self.tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground="#4b8cff" if kind == "primary" else LINE,
                           activeforeground=TXT, relief="flat", bd=0, padx=14, pady=7, font=("Segoe UI", 10, "bold"), cursor="hand2", **kw)
        return b

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
                    self.auth.login(em, pw); prefs({"email": em}); self.root.after(0, self.show_list)
                except Exception as e:
                    m = str(e); self.root.after(0, lambda m=m: (err.config(text=m), b.config(text="Đăng nhập")))
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
        lk = tk.Label(sub, text="Mua / gia hạn trên web ↗", font=self.f_s, fg=ACC, bg=BG, cursor="hand2"); lk.pack(side="right")
        lk.bind("<Button-1>", lambda *_: webbrowser.open(WEB))
        wrap = tk.Frame(self.body, bg=BG); wrap.pack(fill="both", expand=True, padx=10, pady=10)
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0); sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        self.list = tk.Frame(cv, bg=BG); win = cv.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set); cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))
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
                self.root.after(0, lambda: self.render(rows))
            except ApiError as e:
                m = str(e)
                if e.code == 401: self.root.after(0, lambda m=m: self.show_login(m))
                elif not quiet: self.root.after(0, lambda m=m: self.msg.config(text=m) if self.msg.winfo_exists() else None)
            except Exception as e:
                log("refresh:", traceback.format_exc())
        threading.Thread(target=job, daemon=True).start()

    def render(self, rows):
        tk = self.tk
        if not self.list.winfo_exists(): return
        for w in self.list.winfo_children(): w.destroy()
        if not rows:
            tk.Label(self.list, text="Bạn chưa có máy nào.", font=self.f_h2, fg=TXT, bg=BG).pack(pady=(40, 6))
            tk.Label(self.list, text="Mua máy trên web, rồi quay lại đây để điều khiển.", font=self.f_t, fg=DIM, bg=BG).pack()
            self.btn(self.list, "Mua máy trên web ↗", lambda: webbrowser.open(WEB)).pack(pady=14)
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
        bar = tk.Frame(c, bg=CARD); bar.pack(fill="x", padx=14, pady=(10, 12))
        active = p.get("status") == "active" and not p.get("locked")
        b = self.btn(bar, "▶  Điều khiển", lambda pid=str(p["id"]): spawn_viewer(pid)); b.pack(side="left")
        if not active: b.config(state="disabled", bg=LINE)
        if active: self.btn(bar, "Khởi động lại", lambda q=p: self.reboot(q), "ghost").pack(side="left", padx=8)
        self.btn(bar, "Gia hạn ↗", lambda: webbrowser.open(WEB), "ghost").pack(side="right")

    def reboot(self, p):
        import tkinter.messagebox as mb
        if not mb.askyesno("Khởi động lại", f"Khởi động lại máy “{p.get('name')}”? Mất khoảng 1 phút."): return
        def job():
            try:
                ep = self.auth.rpc("cloudphone_endpoint", {"p_id": p["id"]}) or {}
                base = ((ep.get("stream") or {}).get("api") or PHONE_API).rstrip("/")
                self.auth.cp("POST", base, f"{p['id']}/reboot")
                self.root.after(0, lambda: mb.showinfo("BlueRoblox", "Đang khởi động lại máy — chờ khoảng 1 phút rồi mở lại."))
            except Exception as e:
                m = str(e); self.root.after(0, lambda m=m: mb.showerror("BlueRoblox", m))
        threading.Thread(target=job, daemon=True).start()

    def logout(self):
        self.auth.logout(); self.show_login()

    def run(self): self.root.mainloop()


def selftest(out):
    """Mở thử mọi thư viện cửa sổ máy cần (giải mã H.264, cửa sổ GPU, tkinter) → ghi kết quả ra file."""
    res = []
    try:
        import av, numpy, pygame, tkinter, ssl, brstream                         # noqa: F401,E401
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
