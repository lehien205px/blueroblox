package shop.blueroblox.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.Dialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.util.Random;

/** Màn điều khiển máy ảo: hình máy ở giữa, thanh nút Android + công cụ bằng giao diện app. */
public class ControlActivity extends Activity {
    private static final int REQ_APK = 51;
    private static final String[][] QUAL = {{"eco", "Tiết kiệm", "Saver"}, {"bal", "Cân bằng", "Balanced"}, {"hd", "Siêu nét", "Ultra HD"}};

    private final Handler h = new Handler(Looper.getMainLooper());
    private FrameLayout stage;
    private WebView web;
    private LinearLayout topBar, navBar, veil;
    private TextView title, info, veilText, fsExit;
    private ProgressBar veilSpin;
    private Button veilBtn;
    private JSONObject phone = new JSONObject();
    private String id;
    private String quality = "bal";
    private boolean full = false, gotVideo = false, landscapeDev = false;
    private int retries = 0;
    private SharedPreferences pref;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Ui.init(this);
        if (!Api.loggedIn()) { finish(); return; }
        Ui.edgeToEdge(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        pref = getSharedPreferences("control", MODE_PRIVATE);
        quality = pref.getString("q", "bal");
        id = getIntent().getStringExtra("id");

        LinearLayout root = Ui.col(this, 0);
        root.setBackgroundColor(Color.BLACK);

        // Thanh trên: quay lại · tên máy · chất lượng
        topBar = Ui.row(this, 8);
        topBar.setPadding(Ui.dp(8), Ui.dp(6), Ui.dp(12), Ui.dp(6));
        topBar.setBackgroundColor(Ui.BG);
        TextView back = Ui.text(this, "‹", 30, Ui.FG, false);
        back.setPadding(Ui.dp(10), 0, Ui.dp(10), Ui.dp(4));
        back.setContentDescription(Ui.L("Quay lại danh sách máy", "Back to phones"));
        back.setOnClickListener(v -> finish());
        topBar.addView(back, Ui.wrap());
        LinearLayout tt = Ui.col(this, 0);
        title = Ui.text(this, "…", 16, Ui.FG, true);
        title.setSingleLine(true);
        info = Ui.text(this, "", 12, Ui.MUTED, false);
        info.setSingleLine(true);
        tt.addView(title);
        tt.addView(info);
        topBar.addView(tt, Ui.weight(1));
        TextView q = Ui.pill(this, "", Ui.SKY);
        q.setOnClickListener(v -> pickQuality());
        q.setTag("q");
        topBar.addView(q, Ui.wrap());
        root.addView(topBar, Ui.fill());

        // Màn hình máy
        stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.BLACK);
        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        stage.addView(web, new FrameLayout.LayoutParams(-1, -1));
        veil = Ui.col(this, 12);
        veil.setGravity(Gravity.CENTER);
        veil.setPadding(Ui.dp(28), Ui.dp(28), Ui.dp(28), Ui.dp(28));
        veil.setBackgroundColor(Color.argb(235, 6, 10, 28));
        veilSpin = new ProgressBar(this);
        veil.addView(veilSpin, Ui.wrap());
        veilText = Ui.text(this, Ui.L("Đang kết nối máy…", "Connecting…"), 15, Ui.FG, false);
        veilText.setGravity(Gravity.CENTER);
        veil.addView(veilText, Ui.fill());
        veilBtn = Ui.button(this, Ui.L("Thử lại", "Retry"), Ui.PRIMARY);
        veilBtn.setOnClickListener(v -> connect());
        veilBtn.setVisibility(View.GONE);
        veil.addView(veilBtn, Ui.wrap());
        stage.addView(veil, new FrameLayout.LayoutParams(-1, -1));
        fsExit = Ui.text(this, "⤡", 18, Ui.FG, true);
        fsExit.setGravity(Gravity.CENTER);
        fsExit.setBackground(Ui.round(Color.argb(140, 14, 22, 56), 99, Ui.LINE2));
        fsExit.setContentDescription(Ui.L("Thoát toàn màn hình", "Exit full screen"));
        fsExit.setOnClickListener(v -> setFull(false));
        fsExit.setVisibility(View.GONE);
        FrameLayout.LayoutParams fp = new FrameLayout.LayoutParams(Ui.dp(40), Ui.dp(40), Gravity.TOP | Gravity.END);
        fp.setMargins(0, Ui.dp(10), Ui.dp(10), 0);
        stage.addView(fsExit, fp);
        root.addView(stage, new LinearLayout.LayoutParams(-1, 0, 1));

        // Thanh nút Android
        navBar = Ui.row(this, 0);
        navBar.setBackgroundColor(Ui.BG);
        navBar.setPadding(Ui.dp(6), Ui.dp(2), Ui.dp(6), Ui.dp(2));
        navBar.addView(Ui.tool(this, "◁", Ui.L("Quay lại", "Back"), v -> key("back", 4)), Ui.weight(1));
        navBar.addView(Ui.tool(this, "○", Ui.L("Màn chính", "Home"), v -> key("home", 3)), Ui.weight(1));
        navBar.addView(Ui.tool(this, "▢", Ui.L("Đa nhiệm", "Recents"), v -> key("recents", 187)), Ui.weight(1));
        navBar.addView(Ui.tool(this, "⋯", Ui.L("Công cụ", "Tools"), v -> tools()), Ui.weight(1));
        root.addView(navBar, Ui.fill());

        Ui.insets(root, true, true);
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setAllowFileAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setUserAgentString(s.getUserAgentString() + " BlueRobloxApp/" + Api.version);
        CookieManager.getInstance().setAcceptCookie(true);
        web.addJavascriptInterface(new Bridge(), "BRApp");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return !Api.PHONE.contains(String.valueOf(r.getUrl().getHost()));
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) fail(Ui.L("Không tải được trình điều khiển. Kiểm tra mạng rồi thử lại.", "Could not load the player. Check your network."));
            }
            @Override public void onPageFinished(WebView v, String url) {
                h.postDelayed(() -> { if (!gotVideo) veilText.setText(Ui.L("Đang nhận hình từ máy…", "Receiving video…")); }, 1500);
                h.postDelayed(() -> { if (!gotVideo) veil.setVisibility(View.GONE); }, 7000);   // máy cũ không gửi tín hiệu → vẫn hiện hình
            }
        });
        updQ();
        load();
    }

    @Override protected void onDestroy() {
        h.removeCallbacksAndMessages(null);
        if (web != null) { web.loadUrl("about:blank"); web.destroy(); }
        super.onDestroy();
    }

    // ---------------- Kết nối ----------------
    private void load() {
        showVeil(Ui.L("Đang mở máy…", "Opening phone…"), true);
        Api.<JSONObject[]>bg(() -> {
            JSONArray a = (JSONArray) Api.get("cloud_phones?id=eq." + id + "&select=*");
            if (a.length() == 0) throw new Exception(Ui.L("Không tìm thấy máy.", "Phone not found."));
            JSONObject p = a.getJSONObject(0);
            JSONObject inf = Api.cp("GET", id + "/info", null);
            return new JSONObject[]{p, inf};
        }, r -> {
            phone = r[0];
            JSONObject inf = r[1];
            title.setText(phone.optString("name"));
            String res = phone.optString("res", "720x1280");
            String[] wh = res.split("x");
            landscapeDev = wh.length == 2 && Integer.parseInt(wh[0]) > Integer.parseInt(wh[1]);
            info.setText(phone.optInt("ram") + "GB · " + phone.optInt("cpu") + Ui.L(" nhân · ", " cores · ") + res + " · " + phone.optInt("fps", 30) + " FPS");
            if (inf.optBoolean("maint")) { fail("🛠 " + inf.optString("maint_msg", Ui.L("Đang bảo trì", "Under maintenance"))); return; }
            if (inf.optBoolean("locked")) { fail("🔒 " + inf.optString("lock_msg")); return; }
            if (!"active".equals(phone.optString("status"))) { fail(Ui.L("Máy chưa chạy (đang tạo hoặc đã hết hạn).", "This phone is not running.")); return; }
            connect();
        }, this::fail);
    }

    private void connect() {
        gotVideo = false;
        showVeil(Ui.L("Đang kết nối máy…", "Connecting…"), true);
        Api.bg(() -> { try { Api.cp("POST", id + "/key", new JSONObject().put("code", 224)); } catch (Exception ignored) { } return true; }, x -> { }, m -> { });   // bật sáng màn máy ảo
        Api.bg(Api::cpSession, ck -> {
            CookieManager cm = CookieManager.getInstance();
            if (ck != null) cm.setCookie(Api.PHONE, ck);
            cm.flush();
            web.loadUrl(streamUrl());
        }, this::fail);
    }

    private String streamUrl() {
        try {
            int port = phone.optInt("port");
            String udid = "127.0.0.1:" + port, res = phone.optString("res", "720x1280");
            String[] wh = res.split("x");
            String sid = Integer.toString(new Random().nextInt(1 << 30), 36);
            String ws = "wss://phone.blueroblox.shop/?action=proxy-adb&remote=" + URLEncoder.encode("tcp:8886", "UTF-8") + "&udid=" + URLEncoder.encode(udid, "UTF-8");
            return Api.PHONE + "/?v=" + sid + "#!action=stream&udid=" + URLEncoder.encode(udid, "UTF-8") + "&player=webcodecs&ws=" + URLEncoder.encode(ws, "UTF-8")
                    + "&captureKeyboard=1&cpq=" + quality + "&cpfps=" + phone.optInt("fps", 30) + "&cpw=" + wh[0] + "&cph=" + (wh.length > 1 ? wh[1] : "1280") + "&cpn=" + sid;
        } catch (Exception e) { return Api.PHONE; }
    }

    private void showVeil(String t, boolean spin) {
        veil.setVisibility(View.VISIBLE);
        veilText.setText(t);
        veilSpin.setVisibility(spin ? View.VISIBLE : View.GONE);
        veilBtn.setVisibility(spin ? View.GONE : View.VISIBLE);
    }

    private void fail(String m) { showVeil(m, false); }

    /** Nhận tín hiệu từ trình phát hình (đã có hình / rớt mạng / tốc độ). */
    private class Bridge {
        @JavascriptInterface
        public void onEvent(String json) {
            h.post(() -> {
                try {
                    JSONObject m = new JSONObject(json);
                    String t = m.optString("t");
                    if ("ok".equals(t)) { gotVideo = true; retries = 0; veil.setVisibility(View.GONE); }
                    else if ("closed".equals(t)) {
                        if (isFinishing()) return;
                        if (retries < 6) {
                            retries++;
                            showVeil(Ui.L("Mất kết nối — đang nối lại (" + retries + ")…", "Reconnecting (" + retries + ")…"), true);
                            h.postDelayed(ControlActivity.this::connect, Math.min(8000, 1200L * retries));
                        } else fail(Ui.L("Mất kết nối với máy. Kiểm tra mạng rồi bấm Thử lại.", "Connection lost. Check your network and retry."));
                    } else if ("clip".equals(t)) {          // máy ảo vừa copy chữ → đưa vào bộ nhớ copy của điện thoại
                        String text = m.optString("text");
                        if (!text.isEmpty()) {
                            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                            if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("Cloud Phone", text));
                            Ui.toast(ControlActivity.this, "📋 " + Ui.L("Đã copy từ máy ảo: ", "Copied from cloud phone: ") + (text.length() > 60 ? text.substring(0, 60) + "…" : text));
                        }
                    } else if ("pasteKey".equals(t)) {
                        cpPost("key", new JSONObject().put("code", 279), null);
                    } else if ("stat".equals(t)) {
                        int kb = m.optInt("kbps");
                        info.setText(phone.optInt("ram") + "GB · " + phone.optString("res") + " · " + (kb >= 1000 ? String.format(java.util.Locale.US, "%.1f Mbps", kb / 1000f) : kb + " kbps"));
                    }
                } catch (Exception ignored) { }
            });
        }
    }

    // ---------------- Phím Android ----------------
    private void key(String name, int code) {
        String title = "back".equals(name) ? "Back" : "home".equals(name) ? "Home" : "recents".equals(name) ? "Overview"
                : "volup".equals(name) ? "Volume up" : "voldown".equals(name) ? "Volume down" : "Power";
        String js = "(function(){var b=document.querySelector('button[title=\"" + title + "\"]');if(!b)return '0';"
                + "b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));setTimeout(function(){b.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));},50);return '1';})()";
        web.evaluateJavascript(js, r -> {
            if (r == null || !r.contains("1"))   // trình phát chưa sẵn sàng → gửi qua máy chủ
                Api.bg(() -> Api.cp("POST", id + "/key", new JSONObject().put("code", code)), x -> { }, m -> Ui.toast(this, m));
        });
    }

    // ---------------- Công cụ ----------------
    private void tools() {
        Ui.Wrap g = new Ui.Wrap(this, 6);
        final Dialog[] d = new Dialog[1];
        int w = (getResources().getDisplayMetrics().widthPixels - Ui.dp(64)) / 4;
        Object[][] items = {
                {"🔊", Ui.L("Tăng âm", "Vol +"), (Runnable) () -> key("volup", 24)},
                {"🔉", Ui.L("Giảm âm", "Vol −"), (Runnable) () -> key("voldown", 25)},
                {"⏻", Ui.L("Tắt/mở màn", "Power"), (Runnable) () -> key("power", 26)},
                {"🔄", Ui.L("Xoay màn", "Rotate"), (Runnable) this::rotate},
                {"📷", Ui.L("Chụp màn", "Screenshot"), (Runnable) this::shot},
                {"📋", Ui.L("Dán từ ĐT", "Paste"), (Runnable) this::pasteFromPhone},
                {"📄", Ui.L("Copy từ máy", "Copy out"), (Runnable) this::copyFromPhone},
                {"⌨️", Ui.L("Gõ chữ", "Type text"), (Runnable) this::typeText},
                {"🌐", Ui.L("Mở web", "Open link"), (Runnable) this::openUrl},
                {"📦", Ui.L("Cài APK", "Install APK"), (Runnable) this::pickApk},
                {"🔗", Ui.L("Cài từ link", "From link"), (Runnable) this::fromLink},
                {"▦", Ui.L("Ứng dụng", "Apps"), (Runnable) this::apps},
                {"✨", Ui.L("Chất lượng", "Quality"), (Runnable) this::pickQuality},
                {"⛶", Ui.L("Toàn màn", "Full screen"), (Runnable) () -> setFull(true)},
                {"📱", Ui.L("Đổi máy", "Switch"), (Runnable) this::switchPhone},
                {"↻", Ui.L("Tải lại hình", "Reload"), (Runnable) () -> { retries = 0; connect(); }},
                {"⚡", Ui.L("Khởi động lại", "Reboot"), (Runnable) this::reboot},
        };
        for (Object[] it : items) {
            Runnable r = (Runnable) it[2];
            LinearLayout t = Ui.tool(this, (String) it[0], (String) it[1], v -> { if (d[0] != null) d[0].dismiss(); r.run(); });
            t.setLayoutParams(new FrameLayout.LayoutParams(w, -2));
            t.setMinimumWidth(w);
            g.addView(t, new android.view.ViewGroup.LayoutParams(w, -2));
        }
        d[0] = Ui.sheet(this, Ui.L("Công cụ", "Tools"), g);
        d[0].show();
    }

    private void cpPost(String path, JSONObject body, String okMsg) {
        Api.bg(() -> Api.cp("POST", id + "/" + path, body), r -> { if (okMsg != null) Ui.toast(this, okMsg); }, m -> Ui.toast(this, m));
    }

    private void rotate() {
        try { cpPost("rotate", new JSONObject().put("dir", landscapeDev ? 0 : 1), Ui.L("Đã xoay màn hình.", "Rotated.")); landscapeDev = !landscapeDev; }
        catch (Exception ignored) { }
    }

    private void shot() {
        Ui.toast(this, Ui.L("Đang chụp…", "Capturing…"));
        Api.bg(() -> Api.cpBytes(id + "/shot"), png -> {
            if (Build.VERSION.SDK_INT < 29) { Ui.toast(this, Ui.L("Cần Android 10 trở lên để lưu ảnh.", "Android 10+ required to save.")); return; }
            try {
                ContentValues cv = new ContentValues();
                cv.put(MediaStore.MediaColumns.DISPLAY_NAME, "cloudphone_" + System.currentTimeMillis() + ".png");
                cv.put(MediaStore.MediaColumns.MIME_TYPE, "image/png");
                cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/BlueRoblox");
                Uri u = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
                if (u == null) throw new Exception();
                try (OutputStream o = getContentResolver().openOutputStream(u)) { if (o != null) o.write(png); }
                Ui.toast(this, Ui.L("Đã lưu ảnh vào Thư viện › Pictures/BlueRoblox", "Saved to Pictures/BlueRoblox"));
            } catch (Exception e) { Ui.toast(this, Ui.L("Không lưu được ảnh.", "Could not save.")); }
        }, m -> Ui.toast(this, m));
    }

    private void askText(String title, String hint, String okLabel, int type, Api.Cb<String> on) {
        LinearLayout c = Ui.col(this, 12);
        EditText e = Ui.input(this, hint, type);
        c.addView(e, Ui.fill());
        Button ok = Ui.button(this, okLabel, Ui.PRIMARY);
        c.addView(ok, Ui.fill());
        Dialog d = Ui.sheet(this, title, c);
        ok.setOnClickListener(v -> { String s = e.getText().toString(); if (s.trim().isEmpty()) return; d.dismiss(); on.ok(s); });
        d.show();
        e.requestFocus();
    }

    private void typeText() {
        askText(Ui.L("Gõ chữ vào máy", "Type into phone"), Ui.L("Nội dung (bấm vào ô nhập trên máy trước)", "Text (tap an input on the phone first)"),
                Ui.L("Gửi vào máy", "Send"), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE,
                this::pasteText);
    }

    /** Dán chữ vào máy ảo: đặt vào bộ nhớ copy của máy (giữ được chữ có dấu) rồi bấm Dán; máy chưa hỗ trợ thì gõ từng chữ. */
    private void pasteText(String text) {
        web.evaluateJavascript("(function(){try{return !!(window.__cp&&__cp.clipSet(" + JSONObject.quote(text) + "));}catch(e){return false;}})()", r -> {
            if ("true".equals(r)) {
                h.postDelayed(() -> { try { cpPost("key", new JSONObject().put("code", 279), Ui.L("Đã dán vào máy.", "Pasted.")); } catch (Exception ignored) { } }, 250);
            } else {
                try { cpPost("text", new JSONObject().put("text", text), Ui.L("Đã gửi chữ vào máy.", "Sent.")); } catch (Exception ignored) { }
            }
        });
    }

    private void pasteFromPhone() {
        ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        String text = "";
        try {
            if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip() != null && cm.getPrimaryClip().getItemCount() > 0) {
                CharSequence c = cm.getPrimaryClip().getItemAt(0).coerceToText(this);
                if (c != null) text = c.toString();
            }
        } catch (Exception ignored) { }
        if (text.isEmpty()) { Ui.toast(this, Ui.L("Bộ nhớ copy của điện thoại đang trống — gõ chữ ở đây.", "Your clipboard is empty — type it here.")); typeText(); return; }
        pasteText(text);
    }

    private void copyFromPhone() {
        web.evaluateJavascript("(function(){try{return !!(window.__cp&&__cp.clipGet());}catch(e){return false;}})()", r -> {
            if ("true".equals(r)) Ui.toast(this, Ui.L("Đang lấy chữ từ máy…", "Getting text…"));
            else Ui.toast(this, Ui.L("Trên máy ảo hãy bôi đen chữ rồi bấm Copy — chữ sẽ tự về điện thoại của bạn.", "Select text on the cloud phone and tap Copy — it comes to your phone automatically."));
        });
    }

    private void openUrl() {
        askText(Ui.L("Mở link trên máy", "Open link on phone"), "https://…", Ui.L("Mở", "Open"), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI,
                s -> { try { cpPost("url", new JSONObject().put("url", s.trim()), Ui.L("Đã mở trên máy.", "Opened.")); } catch (Exception ignored) { } });
    }

    private void fromLink() {
        askText(Ui.L("Cài APK từ link", "Install APK from link"), Ui.L("Link tải trực tiếp file .apk", "Direct .apk link"), Ui.L("Tải & cài", "Install"),
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI, s -> {
                    Dialog[] pd = progress(Ui.L("Máy chủ đang tải file…", "Downloading on server…"));
                    Api.bg(() -> {
                        JSONObject j = Api.cp("POST", id + "/fromurl", new JSONObject().put("url", s.trim()));
                        return waitJob(j.optString("job"), pd);
                    }, msg -> { pd[0].dismiss(); Ui.toast(this, msg); }, m -> { pd[0].dismiss(); Ui.toast(this, m); });
                });
    }

    private void reboot() {
        Ui.confirm(this, Ui.L("Khởi động lại máy?", "Reboot phone?"), Ui.L("Máy tắt khoảng 1 phút rồi tự bật lại. Dữ liệu vẫn giữ.", "The phone restarts in about a minute. Data is kept."),
                Ui.L("Khởi động lại", "Reboot"), () -> {
                    cpPost("reboot", new JSONObject(), Ui.L("Đang khởi động lại…", "Rebooting…"));
                    showVeil(Ui.L("Máy đang khởi động lại, khoảng 1 phút…", "Rebooting, about a minute…"), true);
                    h.postDelayed(() -> { retries = 0; connect(); }, 45000);
                });
    }

    private void pickQuality() {
        LinearLayout c = Ui.col(this, 10);
        final Dialog[] d = new Dialog[1];
        String[] desc = {Ui.L("Nhẹ mạng, 30 FPS — hợp 4G yếu", "Light, 30 FPS — weak 4G"), Ui.L("Rõ nét, mượt — khuyên dùng", "Sharp & smooth — recommended"),
                Ui.L("Đúng độ phân giải máy — cần mạng khoẻ", "Full resolution — needs fast network")};
        for (int i = 0; i < QUAL.length; i++) {
            final String k = QUAL[i][0];
            Button b = Ui.button(this, Ui.L(QUAL[i][1], QUAL[i][2]) + " — " + desc[i], k.equals(quality) ? Ui.PRIMARY : Ui.GHOST);
            b.setOnClickListener(v -> { d[0].dismiss(); quality = k; pref.edit().putString("q", k).apply(); updQ(); retries = 0; connect(); });
            c.addView(b, Ui.fill());
        }
        d[0] = Ui.sheet(this, Ui.L("Chất lượng hình", "Video quality"), c);
        d[0].show();
    }

    private void updQ() {
        View v = topBar.findViewWithTag("q");
        for (String[] q : QUAL) if (q[0].equals(quality) && v instanceof TextView) ((TextView) v).setText("✨ " + Ui.L(q[1], q[2]));
    }

    private void switchPhone() {
        Api.bg(Api::phones, list -> {
            LinearLayout c = Ui.col(this, 10);
            final Dialog[] d = new Dialog[1];
            for (int i = 0; i < list.length(); i++) {
                JSONObject p = list.optJSONObject(i);
                if (!"active".equals(p.optString("status")) || MainActivity.locked(p)) continue;
                boolean me = p.optString("id").equals(id);
                Button b = Ui.button(this, (me ? "● " : "") + p.optString("name") + " · " + p.optInt("ram") + "GB", me ? Ui.PRIMARY : Ui.GHOST);
                b.setOnClickListener(v -> { d[0].dismiss(); if (!me) { id = p.optString("id"); retries = 0; load(); } });
                c.addView(b, Ui.fill());
            }
            if (c.getChildCount() == 0) c.addView(Ui.muted(this, Ui.L("Không có máy nào đang chạy.", "No running phones.")));
            d[0] = Ui.sheet(this, Ui.L("Chọn máy", "Choose phone"), c);
            d[0].show();
        }, m -> Ui.toast(this, m));
    }

    private void apps() {
        Api.bg(() -> Api.cp("GET", id + "/apps", null), j -> {
            JSONArray a = j.optJSONArray("apps");
            LinearLayout c = Ui.col(this, 8);
            if (a == null || a.length() == 0) c.addView(Ui.muted(this, Ui.L("Chưa có ứng dụng nào do bạn cài.", "No user-installed apps.")));
            else for (int i = 0; i < a.length(); i++) {
                String pkg = a.optString(i);
                LinearLayout r = Ui.row(this, 6);
                TextView n = Ui.text(this, pkg, 13, Ui.FG, false);
                n.setSingleLine(true);
                n.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
                r.addView(n, Ui.weight(1));
                Button open = Ui.button(this, Ui.L("Mở", "Open"), Ui.GHOST);
                open.setMinWidth(0);
                open.setOnClickListener(v -> { try { cpPost("launch", new JSONObject().put("pkg", pkg), Ui.L("Đã mở ", "Opened ") + pkg); } catch (Exception ignored) { } });
                r.addView(open, Ui.wrap());
                Button stop = Ui.button(this, Ui.L("Dừng", "Stop"), Ui.GHOST);
                stop.setMinWidth(0);
                stop.setOnClickListener(v -> { try { cpPost("stop", new JSONObject().put("pkg", pkg), Ui.L("Đã dừng ", "Stopped ") + pkg); } catch (Exception ignored) { } });
                r.addView(stop, Ui.wrap());
                Button del = Ui.button(this, Ui.L("Gỡ", "Remove"), Ui.DANGER);
                del.setMinWidth(0);
                del.setOnClickListener(v -> Ui.confirm(this, Ui.L("Gỡ ứng dụng?", "Uninstall?"), pkg, Ui.L("Gỡ", "Remove"), () -> {
                    try { cpPost("uninstall", new JSONObject().put("pkg", pkg), Ui.L("Đã gỡ ", "Removed ") + pkg); r.setVisibility(View.GONE); } catch (Exception ignored) { }
                }));
                r.addView(del, Ui.wrap());
                c.addView(r, Ui.fill());
            }
            Ui.sheet(this, Ui.L("Ứng dụng trên máy", "Apps on phone"), c).show();
        }, m -> Ui.toast(this, m));
    }

    // ---------------- Cài APK / gửi file từ điện thoại ----------------
    private void pickApk() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("*/*");
        try { startActivityForResult(i, REQ_APK); }
        catch (Exception e) { Ui.toast(this, Ui.L("Không mở được trình chọn file.", "Cannot open file picker.")); }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != REQ_APK || res != RESULT_OK || data == null || data.getData() == null) return;
        Uri u = data.getData();
        String name = "file";
        long size = -1;
        try (Cursor c = getContentResolver().query(u, null, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME), si = c.getColumnIndex(OpenableColumns.SIZE);
                if (ni >= 0) name = c.getString(ni);
                if (si >= 0) size = c.getLong(si);
            }
        } catch (Exception ignored) { }
        if (size <= 0) { Ui.toast(this, Ui.L("Không đọc được file.", "Cannot read file.")); return; }
        if (size > 4L * 1073741824L) { Ui.toast(this, Ui.L("File lớn hơn 4GB.", "File larger than 4GB.")); return; }
        upload(u, name, size);
    }

    private void upload(Uri u, String name, long size) {
        boolean apk = name.toLowerCase(java.util.Locale.ROOT).endsWith(".apk");
        Dialog[] pd = progress(Ui.L("Đang gửi ", "Uploading ") + name);
        final String fname = name;
        Api.bg(() -> {
            String upid = Long.toString(System.currentTimeMillis(), 36) + Integer.toString(new Random().nextInt(1 << 30), 36);
            int CH = 8 * 1024 * 1024;
            long off = 0;
            String job = null;
            try (InputStream in = getContentResolver().openInputStream(u)) {
                if (in == null) throw new Exception(Ui.L("Không đọc được file.", "Cannot read file."));
                byte[] buf = new byte[CH];
                while (off < size) {
                    int n = 0;
                    while (n < CH) { int k = in.read(buf, n, CH - n); if (k < 0) break; n += k; }
                    if (n == 0) break;
                    byte[] chunk = n == CH ? buf : java.util.Arrays.copyOf(buf, n);
                    String path = id + "/upload?id=" + upid + "&name=" + URLEncoder.encode(fname, "UTF-8") + "&total=" + size + "&off=" + off;
                    JSONObject j = null;
                    for (int t = 1; ; t++) {
                        try { j = Api.cpUpload(path, chunk); break; }
                        catch (Api.ApiError e) { if (e.status < 500 || t > 4) throw e; Thread.sleep(1500L * t); }
                        catch (java.io.IOException e) { if (t > 4) throw e; Thread.sleep(1500L * t); }
                    }
                    off += n;
                    final int pct = (int) (off * 100 / size);
                    Api.ui(() -> setProgress(pd, Ui.L("Đang gửi ", "Uploading ") + fname + " · " + pct + "%"));
                    if (j.has("job")) { job = j.optString("job"); break; }
                }
            }
            if (job == null) throw new Exception(Ui.L("Gửi file chưa xong, thử lại.", "Upload incomplete, retry."));
            Api.ui(() -> setProgress(pd, apk ? Ui.L("Đang cài ứng dụng…", "Installing…") : Ui.L("Đang chép vào máy…", "Copying…")));
            return waitJob(job, pd);
        }, msg -> { pd[0].dismiss(); Ui.toast(this, msg); }, m -> { pd[0].dismiss(); Ui.toast(this, m); });
    }

    private String waitJob(String job, Dialog[] pd) throws Exception {
        for (int i = 0; i < 900; i++) {
            Thread.sleep(1500);
            JSONObject j = Api.cp("GET", "job?id=" + job, null);
            String st = j.optString("state"), msg = j.optString("msg");
            Api.ui(() -> setProgress(pd, msg));
            if ("done".equals(st)) return "✅ " + msg;
            if ("error".equals(st)) throw new Exception(msg);
        }
        throw new Exception(Ui.L("Quá lâu, kiểm tra lại trên máy.", "Timed out."));
    }

    private Dialog[] progress(String text) {
        LinearLayout c = Ui.col(this, 12);
        ProgressBar pb = new ProgressBar(this);
        c.addView(pb, Ui.wrap());
        TextView t = Ui.text(this, text, 14, Ui.FG, false);
        t.setTag("ptext");
        c.addView(t, Ui.fill());
        Dialog d = Ui.sheet(this, null, c);
        d.setCancelable(false);
        d.show();
        return new Dialog[]{d};
    }

    private void setProgress(Dialog[] pd, String text) {
        try {
            View v = pd[0].getWindow().getDecorView().findViewWithTag("ptext");
            if (v instanceof TextView) ((TextView) v).setText(text);
        } catch (Exception ignored) { }
    }

    // ---------------- Toàn màn hình ----------------
    private void setFull(boolean on) {
        full = on;
        topBar.setVisibility(on ? View.GONE : View.VISIBLE);
        navBar.setVisibility(on ? View.GONE : View.VISIBLE);
        fsExit.setVisibility(on ? View.VISIBLE : View.GONE);
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                if (on) { c.hide(WindowInsets.Type.systemBars()); c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE); }
                else c.show(WindowInsets.Type.systemBars());
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(on ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    : View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (full) { setFull(false); return; }
        super.onBackPressed();
    }
}
