package shop.blueroblox.app;

import android.Manifest;
import android.app.Activity;
import android.app.Dialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Màn chính: Máy của tôi · Thuê máy · Tài khoản. */
public class MainActivity extends Activity {
    static final int[] RAM = {2, 4, 6, 8, 12, 16, 24, 32}, CPU = {2, 4, 6, 8}, DAYS = {1, 3, 7, 15, 30, 60}, FPS = {30, 60, 90, 120}, QTY = {1, 2, 3, 5, 10};
    static final String[][] RES = {{"540x960", "Nhẹ máy"}, {"720x1280", "HD"}, {"720x1600", "HD+ dài"}, {"1080x1920", "Full HD"}, {"1080x2400", "FHD+ 20:9"}, {"1280x720", "HD ngang"}};

    private FrameLayout content;
    private TextView balTop;
    private final TextView[] nav = new TextView[3];
    private int tab = 0;
    private boolean loaded = false;
    private final Handler h = new Handler(Looper.getMainLooper());
    private final Runnable poll = this::refresh;

    JSONArray phones = new JSONArray();
    JSONObject prof = new JSONObject(), price = new JSONObject(), status = new JSONObject();

    // lựa chọn khi thuê máy
    int sRam = 2, sCpu = 2, sDays = 7, sFps = 30, sQty = 1;
    String sRes = "720x1280", sAnd = "";
    boolean sFree = false;

    /** Phiên bản Android admin đang bán (Android 12 lên đầu — chạy ổn nhất). */
    java.util.List<String> andList() {
        java.util.List<String> l = new java.util.ArrayList<>();
        for (String v : price.optString("android_versions", "11").split(",")) { v = v.trim(); if (v.matches("\\d{1,2}") && !l.contains(v)) l.add(v); }
        if (l.isEmpty()) l.add("11");
        if (l.remove("12")) l.add(0, "12");
        return l;
    }
    boolean isAdmin() { return "admin".equals(prof.optString("role")); }
    /** Ưu đãi miễn phí đang mở (admin bật + chưa hết giờ). */
    boolean freeLive() {
        if (!price.optBoolean("free_on", false)) return false;
        String u = price.optString("free_until", "");
        if (u.isEmpty() || "null".equals(u)) return true;
        if (android.os.Build.VERSION.SDK_INT < 26) return true;
        try { return java.time.OffsetDateTime.parse(u).toInstant().toEpochMilli() > System.currentTimeMillis(); } catch (Exception e) { return true; }
    }
    /** Số máy của chính tài khoản đang có (đang tạo / chạy / tạm dừng). */
    int myLive() {
        int n = 0;
        for (int i = 0; i < phones.length(); i++) {
            JSONObject p = phones.optJSONObject(i); if (p == null || p.optBoolean("shared", false)) continue;
            if (java.util.Arrays.asList("pending", "creating", "active", "expired", "cancelling").contains(p.optString("status"))) n++;
        }
        return n;
    }
    int maxPerUser() { return isAdmin() ? 0 : price.optInt("max_per_user", 0); }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Ui.init(this);
        if (!Api.loggedIn()) { toLogin(); return; }
        Ui.edgeToEdge(this);

        LinearLayout root = Ui.col(this, 0);
        root.setBackground(Ui.bubbles(this));

        // Đầu trang: cá voi Cloud Blue + tên + số dư + nút Sáng/Tối
        LinearLayout top = Ui.row(this, 10);
        top.setPadding(Ui.dp(14), Ui.dp(10), Ui.dp(14), Ui.dp(10));
        top.setBackground(Ui.round(Ui.LIGHT ? Color.argb(246, 243, 247, 252) : Color.argb(246, 7, 18, 38), 0, 0));
        top.addView(Ui.whale(this, 34), new LinearLayout.LayoutParams(Ui.dp(50), Ui.dp(34)));
        SpannableString bn = new SpannableString("BlueRoblox");
        bn.setSpan(new ForegroundColorSpan(Ui.LIGHT ? Ui.BLUE : Color.rgb(90, 168, 255)), 0, 4, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        TextView brand = Ui.text(this, "", 19, Ui.FG, true);
        brand.setText(bn);
        brand.setSingleLine(true);
        top.addView(brand, Ui.weight(1));
        balTop = Ui.pill(this, "…", Ui.SKY);
        balTop.setOnClickListener(v -> openTopup());
        top.addView(balTop, Ui.wrap());
        TextView th = Ui.text(this, Ui.LIGHT ? "🌙" : "☀️", 17, Ui.FG, false);
        th.setGravity(Gravity.CENTER);
        th.setContentDescription(Ui.L("Đổi chế độ Sáng / Tối", "Switch light / dark mode"));
        Ui.ripple(th, Ui.PANEL, 99, Ui.RING);
        th.setOnClickListener(v -> { Ui.setLight(this, !Ui.LIGHT); recreate(); });
        top.addView(th, new LinearLayout.LayoutParams(Ui.dp(40), Ui.dp(40)));
        root.addView(top, Ui.fill());

        content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));

        // Thanh điều hướng dưới
        LinearLayout bar = Ui.row(this, 0);
        bar.setBackground(Ui.ringed(Ui.PANEL, 0));
        bar.setPadding(Ui.dp(6), Ui.dp(4), Ui.dp(6), Ui.dp(4));
        String[][] items = {{"📱", Ui.L("Máy của tôi", "My phones")}, {"➕", Ui.L("Thuê máy", "Rent")}, {"👤", Ui.L("Tài khoản", "Account")}};
        for (int i = 0; i < 3; i++) {
            final int t = i;
            TextView n = Ui.text(this, items[i][0] + "\n" + items[i][1], 12, Ui.MUTED, true);
            n.setGravity(Gravity.CENTER);
            n.setPadding(0, Ui.dp(8), 0, Ui.dp(8));
            n.setOnClickListener(v -> show(t));
            Ui.ripple(n, Color.TRANSPARENT, 14, 0);
            nav[i] = n;
            bar.addView(n, Ui.weight(1));
        }
        root.addView(bar, Ui.fill());
        Ui.insets(root, true, true);
        setContentView(root);

        askNotifyPermission();
        Updater.check(this, false);                 // có bản app mới → hiện thông báo cập nhật ngay trong app
        show(getIntent().getIntExtra("tab", 0));
        refresh();
    }

    @Override protected void onResume() { super.onResume(); if (Api.loggedIn()) refresh(); }
    @Override protected void onPause() { super.onPause(); h.removeCallbacks(poll); }

    private void toLogin() {
        startActivity(new Intent(this, LoginActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        finish();
    }

    private void askNotifyPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 7);
    }

    // ---------------- Tải dữ liệu ----------------
    void refresh() {
        h.removeCallbacks(poll);
        Api.<Object[]>bg(() -> {
            JSONObject[] o = {Api.profile(), Api.pricing(), Api.status()};
            JSONArray ps = Api.phones();
            return new Object[]{o, ps};
        }, r -> {
            JSONObject[] o = (JSONObject[]) r[0];
            prof = o[0]; price = o[1]; status = o[2]; phones = (JSONArray) r[1];
            balTop.setText("💰 " + Ui.vnd(prof.optDouble("balance", 0)));
            ExpiryAlarm.schedule(this, phones);
            if (tab == 0) renderPhones();
            else if (tab == 2) renderAccount();
            else if (!loaded) renderRent();          // lần đầu: vẽ lại bảng thuê với giá và giới hạn thật
            loaded = true;
            boolean pending = false;
            for (int i = 0; i < phones.length(); i++) if ("pending".equals(phones.optJSONObject(i).optString("status"))) pending = true;
            h.postDelayed(poll, pending ? 8000 : 30000);
        }, m -> {
            if (!Api.loggedIn()) { toLogin(); return; }
            Ui.toast(this, m);
            h.postDelayed(poll, 30000);
        });
    }

    void show(int t) {
        tab = t;
        for (int i = 0; i < 3; i++) {
            nav[i].setTextColor(i == t ? Ui.SKY : Ui.MUTED);
            nav[i].setBackground(i == t ? Ui.round(Ui.LIGHT ? Color.argb(28, 43, 110, 240) : Color.argb(40, 90, 175, 255), 14, 0) : null);
        }
        if (t == 0) renderPhones(); else if (t == 1) renderRent(); else renderAccount();
    }

    private ScrollView page(LinearLayout body) {
        body.setPadding(Ui.dp(16), Ui.dp(6), Ui.dp(16), Ui.dp(24));
        ScrollView sv = Ui.scroll(this, body);
        content.removeAllViews();
        content.addView(sv, new FrameLayout.LayoutParams(-1, -1));
        return sv;
    }

    // ---------------- Thời gian / giá ----------------
    static long parseTime(String s) {
        if (s == null || s.isEmpty() || s.equals("null")) return 0;
        try {
            String t = s.replace(" ", "T").replaceAll("\\.\\d+", "");
            if (t.endsWith("Z")) t = t.substring(0, t.length() - 1) + "+00:00";
            if (t.matches(".*[+-]\\d\\d$")) t = t + ":00";
            Date d = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).parse(t);
            return d == null ? 0 : d.getTime();
        } catch (Exception e) { return 0; }
    }

    static String fdate(long ms) { return new SimpleDateFormat("HH:mm dd/MM/yyyy", Locale.getDefault()).format(new Date(ms)); }

    static String left(long ms) {
        long d = ms - System.currentTimeMillis();
        if (d <= 0) return Ui.L("Đã hết hạn", "Expired");
        long m = d / 60000, hh = m / 60, dd = hh / 24;
        if (dd >= 1) return Ui.L("Còn " + dd + " ngày " + (hh % 24) + " giờ", dd + "d " + (hh % 24) + "h left");
        if (hh >= 1) return Ui.L("Còn " + hh + " giờ " + (m % 60) + " phút", hh + "h " + (m % 60) + "m left");
        return Ui.L("Còn " + m + " phút", m + " min left");
    }

    boolean isCtv() { return "approved".equals(prof.optString("ctv_status")) && prof.optDouble("ctv_discount", 0) > 0; }
    double ctv(double v) { return isCtv() ? Math.round(v * (100 - prof.optDouble("ctv_discount", 0)) / 100) : v; }
    double perDay(double ram, double cpu) { return ram * price.optDouble("price_ram_gb_day", 2500) + cpu * price.optDouble("price_cpu_day", 2000); }

    static boolean locked(JSONObject p) {
        if (!p.optBoolean("locked", false)) return false;
        long u = parseTime(p.optString("locked_until", ""));
        return u == 0 || u > System.currentTimeMillis();
    }

    // ---------------- TAB 1: Máy của tôi ----------------
    void renderPhones() {
        LinearLayout body = Ui.col(this, 14);
        if (status.optBoolean("maint", false)) {
            LinearLayout m = Ui.card(this);
            m.setBackground(Ui.round(Color.argb(30, 255, 200, 97), 18, Color.argb(120, 255, 200, 97)));
            m.addView(Ui.text(this, "🛠 " + Ui.L("Đang bảo trì", "Under maintenance"), 15, Ui.WAIT, true));
            String msg = status.optString("maint_msg", "");
            m.addView(Ui.muted(this, msg.isEmpty() || msg.equals("null") ? Ui.L("Máy của bạn vẫn được giữ, không bị tính hết hạn trong lúc bảo trì.", "Your phones are kept during maintenance.") : msg));
            body.addView(m, Ui.fill());
        }
        LinearLayout head = Ui.row(this, 10);
        head.addView(Ui.h1(this, Ui.L("Máy của tôi", "My phones")), Ui.weight(1));
        TextView rf = Ui.pill(this, "⟳ " + Ui.L("Làm mới", "Refresh"), Ui.SKY);
        rf.setOnClickListener(v -> { Ui.toast(this, Ui.L("Đang làm mới…", "Refreshing…")); refresh(); });
        head.addView(rf, Ui.wrap());
        body.addView(head, Ui.fill());

        // Nhập mã chia sẻ / chuyển nhượng
        LinearLayout cr = Ui.row(this, 8);
        EditText code = Ui.input(this, Ui.L("🔑 Có mã chia sẻ? Nhập vào đây", "🔑 Have a share code? Enter it"), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        cr.addView(code, Ui.weight(1));
        Button cb = Ui.button(this, Ui.L("Nhập mã", "Redeem"), Ui.GHOST);
        cb.setOnClickListener(v -> claim(code, cb));
        cr.addView(cb, Ui.wrap());
        body.addView(cr, Ui.fill());

        if (phones.length() == 0) {
            LinearLayout e = Ui.card(this);
            e.setGravity(Gravity.CENTER_HORIZONTAL);
            TextView ic = Ui.text(this, "📱", 44, Ui.FG, false);
            ic.setGravity(Gravity.CENTER);
            e.addView(ic, Ui.fill());
            TextView t = Ui.h2(this, Ui.L("Bạn chưa thuê máy nào", "You have no phones yet"));
            t.setGravity(Gravity.CENTER);
            e.addView(t, Ui.fill());
            TextView d = Ui.muted(this, Ui.L("Chọn RAM, CPU, số ngày — máy Android sẵn sàng sau khoảng 2–3 phút.", "Pick RAM, CPU and days — your Android phone is ready in 2–3 minutes."));
            d.setGravity(Gravity.CENTER);
            e.addView(d, Ui.fill());
            Button go = Ui.button(this, Ui.L("Thuê máy ngay", "Rent a phone"), Ui.PRIMARY);
            go.setOnClickListener(v -> show(1));
            e.addView(go, Ui.fill());
            body.addView(e, Ui.fill());
        }
        for (int i = 0; i < phones.length(); i++) body.addView(phoneCard(phones.optJSONObject(i)), Ui.fill());
        page(body);
    }

    private View phoneCard(JSONObject p) {
        LinearLayout c = Ui.card(this);
        String st = p.optString("status");
        boolean lk = locked(p);
        long exp = parseTime(p.optString("expires_at"));
        long remain = exp - System.currentTimeMillis();

        LinearLayout r1 = Ui.row(this, 10);
        r1.addView(Ui.text(this, p.optString("name", "Máy"), 17, Ui.FG, true), Ui.weight(1));
        String label; int col;
        if (lk) { label = "🔒 " + Ui.L("Bị khoá", "Locked"); col = Ui.BAD; }
        else if ("active".equals(st)) { label = "● " + Ui.L("Đang chạy", "Running"); col = Ui.OK; }
        else if ("pending".equals(st)) { label = "◌ " + Ui.L("Đang tạo…", "Creating…"); col = Ui.WAIT; }
        else if ("expired".equals(st)) { label = "⏸ " + Ui.L("Tạm dừng", "Paused"); col = Ui.BAD; }
        else { label = Ui.L("Lỗi", "Error"); col = Ui.BAD; }
        r1.addView(Ui.pill(this, label, col), Ui.wrap());
        c.addView(r1, Ui.fill());

        c.addView(Ui.muted(this, p.optInt("ram") + "GB RAM · " + p.optInt("cpu") + Ui.L(" nhân", " cores") + " · " + p.optString("res", "") + " · " + p.optInt("fps", 30) + " FPS"), Ui.fill());

        if (exp > 0) {
            int ec = remain <= 0 ? Ui.BAD : remain < 86400000L ? Ui.WAIT : Ui.MUTED;
            String t = "⏱ " + left(exp) + " · " + Ui.L("hết hạn ", "expires ") + fdate(exp);
            if ("expired".equals(st)) {
                long del = exp + 30 * 60000L - System.currentTimeMillis();
                t = "⚠️ " + Ui.L("Đã hết hạn — gia hạn trong " + Math.max(0, del / 60000) + " phút nữa kẻo bị xoá", "Expired — renew within " + Math.max(0, del / 60000) + " min or it will be deleted");
            }
            c.addView(Ui.text(this, t, 13.5f, ec, remain < 86400000L), Ui.fill());
        }
        if (lk) {
            String why = p.optString("lock_reason", "");
            c.addView(Ui.text(this, Ui.L("Máy đang bị khoá", "This phone is locked") + (why.isEmpty() || why.equals("null") ? "" : ": " + why), 13, Ui.BAD, false), Ui.fill());
        }
        if ("pending".equals(st)) c.addView(Ui.muted(this, Ui.L("Máy đang khởi động và cài app sẵn, khoảng 2–3 phút. Trang tự cập nhật.", "Booting and installing apps, about 2–3 minutes.")), Ui.fill());
        boolean shared = p.optBoolean("shared", false);
        if (shared) {
            long se = parseTime(p.optString("share_expires", ""));
            c.addView(Ui.text(this, "👥 " + Ui.L("Được chia sẻ bởi ", "Shared by ") + p.optString("owner_name", "?")
                    + (se > 0 ? Ui.L(" · đến ", " · until ") + fdate(se) : Ui.L(" · theo hạn máy", " · until expiry")), 13, Ui.SKY, false), Ui.fill());
        }

        LinearLayout acts = Ui.row(this, 8);
        Button ctl = Ui.button(this, "▶ " + Ui.L("Điều khiển", "Control"), Ui.PRIMARY);
        boolean canCtl = "active".equals(st) && !lk && !status.optBoolean("maint", false);
        ctl.setEnabled(canCtl);
        ctl.setAlpha(canCtl ? 1f : .45f);
        ctl.setOnClickListener(v -> startActivity(new Intent(this, ControlActivity.class).putExtra("id", p.optString("id"))));
        acts.addView(ctl, Ui.weight(1.4f));
        if (shared) {
            Button lv = Ui.button(this, "🚪 " + Ui.L("Rời máy", "Leave"), Ui.GHOST);
            lv.setOnClickListener(v -> Ui.confirm(this, Ui.L("Rời máy này?", "Leave this phone?"),
                    Ui.L("Bạn sẽ không điều khiển được máy này nữa (chủ máy có thể chia sẻ lại).", "You will lose access (the owner can share it again)."),
                    Ui.L("Rời máy", "Leave"), () -> Api.bg(() -> Api.rpc("cp_share_revoke", new JSONObject().put("p_id", p.optString("share_id"))),
                            r -> { Ui.toast(this, Ui.L("Đã rời máy.", "You left the phone.")); refresh(); }, msg -> Ui.toast(this, msg))));
            acts.addView(lv, Ui.weight(1));
            c.addView(acts, Ui.fill());
            return c;
        }
        Button rn = Ui.button(this, Ui.L("Gia hạn", "Renew"), Ui.GHOST);
        rn.setOnClickListener(v -> renew(p));
        rn.setEnabled(!lk && ("active".equals(st) || "expired".equals(st)));
        acts.addView(rn, Ui.weight(1));
        Button more = Ui.button(this, "⋯", Ui.GHOST);
        more.setOnClickListener(v -> moreMenu(p));
        acts.addView(more, Ui.wrap());
        c.addView(acts, Ui.fill());
        return c;
    }

    void renew(JSONObject p) {
        int max = price.optInt("max_days", 365);
        final int[] days = {Math.min(30, max)};
        LinearLayout c = Ui.col(this, 12);
        c.addView(Ui.muted(this, p.optInt("ram") + "GB · " + p.optInt("cpu") + Ui.L(" nhân · ", " cores · ") + left(parseTime(p.optString("expires_at")))));
        Ui.Wrap w = new Ui.Wrap(this, 8);
        int[] opts = {1, 3, 7, 15, 30, 60, 90};
        TextView sum = Ui.text(this, "", 14, Ui.FG, false);
        EditText x = Ui.input(this, Ui.L("Hoặc tự nhập số ngày", "Or type days"), InputType.TYPE_CLASS_NUMBER);
        Button ok = Ui.button(this, Ui.L("Gia hạn", "Renew"), Ui.PRIMARY);
        Runnable draw = () -> {
            for (int i = 0; i < w.getChildCount(); i++) Ui.setChip((TextView) w.getChildAt(i), (int) w.getChildAt(i).getTag() == days[0]);
            double per = ctv(perDay(p.optInt("ram"), p.optInt("cpu"))), total = per * days[0], bal = prof.optDouble("balance", 0);
            long base = Math.max(System.currentTimeMillis(), parseTime(p.optString("expires_at")));
            sum.setText(Ui.L("Đơn giá: ", "Price: ") + Ui.vnd(per) + Ui.L(" / ngày", " / day") + (isCtv() ? " (CTV −" + prof.optInt("ctv_discount") + "%)" : "")
                    + "\n" + Ui.L("Hạn mới: ", "New expiry: ") + fdate(base + days[0] * 86400000L)
                    + "\n" + Ui.L("Số dư: ", "Balance: ") + Ui.vnd(bal)
                    + "\n" + Ui.L("Thanh toán: ", "Total: ") + Ui.vnd(total));
            boolean enough = bal >= total;
            ok.setText(enough ? Ui.L("Gia hạn " + days[0] + " ngày", "Renew " + days[0] + " days") : Ui.L("Nạp thêm tiền", "Top up"));
        };
        for (int d : opts) {
            if (d > max) continue;
            TextView ch = Ui.chip(this, d + Ui.L(" ngày", "d"), false);
            ch.setTag(d);
            ch.setOnClickListener(v -> { days[0] = (int) v.getTag(); x.setText(""); draw.run(); });
            w.addView(ch);
        }
        c.addView(w, Ui.fill());
        c.addView(x, Ui.fill());
        x.addTextChangedListener(new Simple(() -> {
            try { int v = Integer.parseInt(x.getText().toString()); if (v >= 1 && v <= max) { days[0] = v; draw.run(); } } catch (Exception ignored) { }
        }));
        LinearLayout sb = Ui.card(this);
        sb.addView(sum);
        c.addView(sb, Ui.fill());
        c.addView(ok, Ui.fill());
        Dialog d = Ui.sheet(this, Ui.L("Gia hạn ", "Renew ") + p.optString("name"), c);
        ok.setOnClickListener(v -> {
            double total = ctv(perDay(p.optInt("ram"), p.optInt("cpu"))) * days[0];
            if (prof.optDouble("balance", 0) < total) { d.dismiss(); openTopup(); return; }
            ok.setEnabled(false);
            Api.bg(() -> Api.rpc("cloudphone_renew", new JSONObject().put("p_id", p.optString("id")).put("p_days", days[0])), r -> {
                d.dismiss();
                Ui.toast(this, Ui.L("Đã gia hạn +" + days[0] + " ngày cho ", "Renewed +" + days[0] + " days: ") + p.optString("name"));
                refresh();
                h.postDelayed(this::refresh, 5000);
            }, m -> { ok.setEnabled(true); Ui.toast(this, m); });
        });
        draw.run();
        d.show();
    }

    /** Khách tự nâng RAM / CPU: trả thêm cho phần thời gian còn lại (giá do máy chủ tính, có giảm CTV). */
    void upgrade(JSONObject p) {
        final int R0 = p.optInt("ram"), C0 = p.optInt("cpu");
        final int maxR = price.optInt("max_ram", 16), maxC = price.optInt("max_cpu", 8);
        final int[] nr = {R0}, nc = {C0};
        final long[] cost = {0};
        final boolean gpu = p.optInt("port") >= 5600;          // máy GPU / máy MEmu (Windows): đổi cấu hình phải khởi động lại
        LinearLayout c = Ui.col(this, 12);
        c.addView(Ui.muted(this, Ui.L("Hiện tại: ", "Now: ") + R0 + "GB RAM · " + C0 + Ui.L(" nhân · ", " cores · ") + left(parseTime(p.optString("expires_at")))));
        TextView lr = Ui.text(this, "RAM", 14, Ui.FG, true); c.addView(lr);
        Ui.Wrap wr = new Ui.Wrap(this, 8); c.addView(wr, Ui.fill());
        TextView lc = Ui.text(this, "CPU", 14, Ui.FG, true); c.addView(lc);
        Ui.Wrap wc = new Ui.Wrap(this, 8); c.addView(wc, Ui.fill());
        TextView sum = Ui.text(this, "", 14, Ui.FG, false);
        LinearLayout sb = Ui.card(this); sb.addView(sum); c.addView(sb, Ui.fill());
        c.addView(Ui.muted(this, gpu
                ? Ui.L("Máy sẽ tự khởi động lại khoảng 1 phút để nhận cấu hình mới — app và dữ liệu giữ nguyên.", "The phone restarts (~1 min) to apply — apps and data stay.")
                : Ui.L("Máy không khởi động lại, app và dữ liệu giữ nguyên — tài nguyên mới có sau khoảng 30 giây.", "No restart, apps and data stay — applies in ~30s.")));
        Button ok = Ui.button(this, Ui.L("Nâng cấp ngay", "Upgrade now"), Ui.PRIMARY);
        c.addView(ok, Ui.fill());
        Runnable[] draw = new Runnable[1];
        Runnable quote = () -> {
            if (nr[0] == R0 && nc[0] == C0) { cost[0] = 0; draw[0].run(); return; }
            final int qr = nr[0], qc = nc[0];
            Api.bg(() -> Api.rpc("cloudphone_upgrade_quote", new JSONObject().put("p_id", p.optString("id")).put("p_ram", qr).put("p_cpu", qc)),
                    r -> { if (qr == nr[0] && qc == nc[0]) { try { cost[0] = Long.parseLong(String.valueOf(r).replaceAll("[^0-9]", "")); } catch (Exception e) { cost[0] = 0; } draw[0].run(); } },
                    m -> { sum.setText(m.contains("upgrade") ? Ui.L("Máy chủ chưa cập nhật tính năng nâng cấp.", "Upgrade isn't available yet.") : m); });
        };
        draw[0] = () -> {
            for (int i = 0; i < wr.getChildCount(); i++) Ui.setChip((TextView) wr.getChildAt(i), (int) wr.getChildAt(i).getTag() == nr[0]);
            for (int i = 0; i < wc.getChildCount(); i++) Ui.setChip((TextView) wc.getChildAt(i), (int) wc.getChildAt(i).getTag() == nc[0]);
            boolean same = nr[0] == R0 && nc[0] == C0;
            double bal = prof.optDouble("balance", 0);
            sum.setText(same ? Ui.L("Chọn RAM hoặc CPU muốn nâng.", "Pick more RAM or CPU.")
                    : Ui.L("Cấu hình mới: ", "New: ") + nr[0] + "GB · " + nc[0] + Ui.L(" nhân", " cores")
                    + "\n" + Ui.L("Trả thêm cho thời gian còn lại: ", "Pay for the remaining time: ") + Ui.vnd(cost[0])
                    + "\n" + Ui.L("Số dư: ", "Balance: ") + Ui.vnd(bal));
            ok.setEnabled(!same);
            ok.setText(!same && bal < cost[0] ? Ui.L("Nạp thêm tiền", "Top up") : Ui.L("Nâng cấp ngay", "Upgrade now"));
        };
        int[] rams = {2, 3, 4, 6, 8, 10, 12, 16, 24, 32, 48, 64}, cpus = {2, 3, 4, 6, 8, 10, 12, 16, 24, 32};
        java.util.TreeSet<Integer> rs = new java.util.TreeSet<>(), cs = new java.util.TreeSet<>();
        rs.add(R0); for (int v : rams) if (v > R0 && v <= maxR) rs.add(v); if (maxR > R0) rs.add(maxR);
        cs.add(C0); for (int v : cpus) if (v > C0 && v <= maxC) cs.add(v); if (maxC > C0) cs.add(maxC);
        for (int v : rs) { TextView ch = Ui.chip(this, (v == R0 ? Ui.L("Giữ ", "Keep ") : "") + v + "GB", false); ch.setTag(v);
            ch.setOnClickListener(x -> { nr[0] = (int) x.getTag(); draw[0].run(); quote.run(); }); wr.addView(ch); }
        for (int v : cs) { TextView ch = Ui.chip(this, (v == C0 ? Ui.L("Giữ ", "Keep ") : "") + v + Ui.L(" nhân", " cores"), false); ch.setTag(v);
            ch.setOnClickListener(x -> { nc[0] = (int) x.getTag(); draw[0].run(); quote.run(); }); wc.addView(ch); }
        Dialog d = Ui.sheet(this, "⬆️ " + Ui.L("Nâng cấp ", "Upgrade ") + p.optString("name"), c);
        ok.setOnClickListener(v -> {
            if (prof.optDouble("balance", 0) < cost[0]) { d.dismiss(); openTopup(); return; }
            ok.setEnabled(false);
            Api.bg(() -> Api.rpc("cloudphone_upgrade", new JSONObject().put("p_id", p.optString("id")).put("p_ram", nr[0]).put("p_cpu", nc[0])), r -> {
                d.dismiss();
                Ui.toast(this, Ui.L("Đã nâng lên " + nr[0] + "GB · " + nc[0] + " nhân — máy nhận cấu hình mới trong giây lát.", "Upgraded to " + nr[0] + "GB · " + nc[0] + " cores."));
                refresh(); h.postDelayed(this::refresh, 5000);
            }, m -> {
                ok.setEnabled(true);
                Ui.toast(this, m.contains("UPGRADE_PENDING") ? Ui.L("Máy đang nâng cấp, đợi chút rồi thử lại.", "An upgrade is already in progress.")
                        : m.contains("FREE_NO_UPGRADE") ? Ui.L("Máy miễn phí không nâng cấp được.", "Free phones can't be upgraded.")
                        : m.contains("PHONE_NOT_ACTIVE") ? Ui.L("Máy phải đang chạy mới nâng cấp được.", "The phone must be running.") : m);
            });
        });
        draw[0].run();
        d.show();
    }

    void moreMenu(JSONObject p) {
        LinearLayout c = Ui.col(this, 10);
        Button sh = Ui.button(this, "👥 " + Ui.L("Chia sẻ / chuyển nhượng", "Share / transfer"), Ui.GHOST);
        Button rn = Ui.button(this, "✏️ " + Ui.L("Đổi tên", "Rename"), Ui.GHOST);
        Button up = Ui.button(this, "⬆️ " + Ui.L("Nâng cấp RAM / CPU", "Upgrade RAM / CPU"), Ui.GHOST);
        boolean canUp = "active".equals(p.optString("status")) && !locked(p) && !p.optBoolean("is_free") && !p.optBoolean("shared");
        up.setEnabled(canUp); up.setAlpha(canUp ? 1f : .45f);
        boolean canShare = ("active".equals(p.optString("status")) || "pending".equals(p.optString("status"))) && !locked(p);
        sh.setEnabled(canShare); sh.setAlpha(canShare ? 1f : .45f);
        c.addView(up, Ui.fill()); c.addView(sh, Ui.fill()); c.addView(rn, Ui.fill());
        Dialog d = Ui.sheet(this, p.optString("name"), c);
        up.setOnClickListener(v -> { d.dismiss(); upgrade(p); });
        sh.setOnClickListener(v -> { d.dismiss(); share(p); });
        rn.setOnClickListener(v -> { d.dismiss(); rename(p); });
        d.show();
    }

    void claim(EditText code, Button b) {
        String s = code.getText().toString().toUpperCase(Locale.US).replaceAll("[^A-Z0-9]", "");
        if (s.length() < 6) { Ui.toast(this, Ui.L("Nhập mã gồm 8 ký tự.", "Enter the 8-character code.")); return; }
        b.setEnabled(false);
        Api.bg(() -> Api.rpc("cp_share_claim", new JSONObject().put("p_code", s)), r -> {
            b.setEnabled(true);
            JSONObject o = r instanceof JSONObject ? (JSONObject) r : new JSONObject();
            Ui.toast(this, "transfer".equals(o.optString("kind")) ? Ui.L("🎉 Máy \"" + o.optString("name") + "\" giờ là của bạn!", "🎉 \"" + o.optString("name") + "\" is now yours!")
                    : Ui.L("✓ Đã thêm máy \"" + o.optString("name") + "\" được chia sẻ cho bạn.", "✓ \"" + o.optString("name") + "\" was shared with you."));
            refresh();
        }, msg -> { b.setEnabled(true); Ui.toast(this, msg); });
    }

    private void copyCode(String code) {
        android.content.ClipboardManager cm = (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (cm != null) cm.setPrimaryClip(android.content.ClipData.newPlainText("code", code));
        Ui.toast(this, Ui.L("Đã copy mã: ", "Copied: ") + code);
    }

    private View codeBox(String code, String note) {
        LinearLayout b = Ui.card(this);
        LinearLayout r = Ui.row(this, 10);
        TextView t = Ui.text(this, code, 24, Ui.FG, true);
        t.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        t.setLetterSpacing(.15f);
        r.addView(t, Ui.weight(1));
        Button cp = Ui.button(this, "📋 Copy", Ui.PRIMARY);
        cp.setOnClickListener(v -> copyCode(code));
        r.addView(cp, Ui.wrap());
        b.addView(r, Ui.fill());
        b.addView(Ui.muted(this, note), Ui.fill());
        return b;
    }

    void share(JSONObject p) {
        LinearLayout c = Ui.col(this, 12);
        c.addView(Ui.h2(this, Ui.L("Chia sẻ dùng chung", "Share access")));
        c.addView(Ui.muted(this, Ui.L("Người nhập mã được điều khiển máy như bạn (không gia hạn, huỷ hay chia sẻ tiếp được). Mỗi mã cho 1 người, thu hồi lúc nào cũng được.",
                "Whoever enters the code can control this phone like you (not renew, cancel or re-share). One code per person; revoke any time.")));
        final int[] days = {7};
        Ui.Wrap w = new Ui.Wrap(this, 8);
        int[] opts = {1, 3, 7, 30, 0};
        for (int d : opts) {
            TextView ch = Ui.chip(this, d == 0 ? Ui.L("Theo hạn máy", "Until expiry") : d + Ui.L(" ngày", "d"), d == days[0]);
            ch.setTag(d);
            ch.setOnClickListener(v -> { days[0] = (int) v.getTag(); for (int i = 0; i < w.getChildCount(); i++) Ui.setChip((TextView) w.getChildAt(i), (int) w.getChildAt(i).getTag() == days[0]); });
            w.addView(ch);
        }
        c.addView(w, Ui.fill());
        Button mk = Ui.button(this, Ui.L("Tạo mã chia sẻ", "Create share code"), Ui.PRIMARY);
        c.addView(mk, Ui.fill());
        LinearLayout newBox = Ui.col(this, 8), list = Ui.col(this, 8);
        c.addView(newBox, Ui.fill()); c.addView(list, Ui.fill());

        c.addView(Ui.h2(this, Ui.L("Chuyển nhượng máy", "Transfer ownership")));
        c.addView(Ui.muted(this, Ui.L("Người nhập mã thành chủ mới của máy (giữ nguyên hạn còn lại và dữ liệu). Bạn sẽ mất máy này, mọi chia sẻ cũ bị huỷ.",
                "Whoever enters the code becomes the new owner (time left and data are kept). You lose this phone; all shares are cancelled.")));
        Button tr = Ui.button(this, Ui.L("Tạo mã chuyển nhượng", "Create transfer code"), Ui.DANGER);
        c.addView(tr, Ui.fill());
        LinearLayout trBox = Ui.col(this, 8);
        c.addView(trBox, Ui.fill());

        Runnable[] load = new Runnable[1];
        load[0] = () -> Api.bg(() -> Api.rpc("cp_share_list", new JSONObject().put("p_phone", p.optString("id"))), r -> {
            list.removeAllViews();
            JSONArray a = r instanceof JSONArray ? (JSONArray) r : new JSONArray();
            if (a.length() > 0) list.addView(Ui.muted(this, Ui.L("Mã đang hoạt động (", "Active codes (") + a.length() + ")"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject s = a.optJSONObject(i);
                LinearLayout row = Ui.row(this, 8);
                long se = parseTime(s.optString("expires_at", ""));
                String u = s.optString("user", ""); boolean used = !u.isEmpty() && !u.equals("null");
                row.addView(Ui.text(this, s.optString("code") + "\n" + (used ? "👤 " + u : Ui.L("Chưa ai dùng", "Not used yet"))
                        + (se > 0 ? Ui.L(" · đến ", " · until ") + fdate(se) : Ui.L(" · theo hạn máy", " · until expiry")), 13, Ui.FG, false), Ui.weight(1));
                Button rv = Ui.button(this, used ? Ui.L("Thu hồi", "Revoke") : Ui.L("Huỷ mã", "Cancel"), Ui.DANGER);
                rv.setOnClickListener(v -> Api.bg(() -> Api.rpc("cp_share_revoke", new JSONObject().put("p_id", s.optString("id"))),
                        x -> { Ui.toast(this, Ui.L("Đã thu hồi.", "Revoked.")); load[0].run(); }, msg -> Ui.toast(this, msg)));
                row.addView(rv, Ui.wrap());
                list.addView(row, Ui.fill());
            }
        }, msg -> { list.removeAllViews(); list.addView(Ui.text(this, msg, 13, Ui.BAD, false)); });

        mk.setOnClickListener(v -> {
            mk.setEnabled(false);
            Api.bg(() -> Api.rpc("cp_share_create", new JSONObject().put("p_phone", p.optString("id")).put("p_kind", "share").put("p_days", days[0] == 0 ? JSONObject.NULL : days[0])), r -> {
                mk.setEnabled(true);
                String code = r instanceof JSONObject ? ((JSONObject) r).optString("code") : "";
                newBox.removeAllViews();
                newBox.addView(codeBox(code, Ui.L("Gửi mã cho người cần dùng. Họ vào app hoặc blueroblox.shop → ĐT đám mây → nhập mã.",
                        "Send this code. They open the app or blueroblox.shop → Cloud phones → enter the code.")), Ui.fill());
                load[0].run();
            }, msg -> { mk.setEnabled(true); Ui.toast(this, msg); });
        });
        tr.setOnClickListener(v -> Ui.confirm(this, Ui.L("Tạo mã chuyển nhượng?", "Create transfer code?"),
                Ui.L("Ai nhập mã này sẽ thành chủ máy \"" + p.optString("name") + "\", bạn mất máy. Mã chuyển nhượng cũ (nếu có) bị huỷ.",
                        "Whoever enters it becomes the owner of \"" + p.optString("name") + "\". Any older transfer code is cancelled."),
                Ui.L("Tạo mã", "Create"), () -> Api.bg(() -> Api.rpc("cp_share_create", new JSONObject().put("p_phone", p.optString("id")).put("p_kind", "transfer").put("p_days", JSONObject.NULL)), r -> {
                    String code = r instanceof JSONObject ? ((JSONObject) r).optString("code") : "";
                    trBox.removeAllViews();
                    trBox.addView(codeBox(code, Ui.L("Chỉ đưa mã sau khi đã nhận tiền. Muốn huỷ: tạo mã chuyển nhượng mới (mã cũ mất hiệu lực).",
                            "Only hand this over after you've been paid. To cancel, create a new transfer code.")), Ui.fill());
                }, msg -> Ui.toast(this, msg))));
        Dialog d = Ui.sheet(this, "👥 " + p.optString("name"), c);
        d.setOnDismissListener(x -> refresh());
        load[0].run();
        d.show();
    }

    void rename(JSONObject p) {
        LinearLayout c = Ui.col(this, 12);
        EditText n = Ui.input(this, Ui.L("Tên máy", "Phone name"), InputType.TYPE_CLASS_TEXT);
        n.setText(p.optString("name"));
        c.addView(n, Ui.fill());
        Button ok = Ui.button(this, Ui.L("Lưu tên", "Save"), Ui.PRIMARY);
        c.addView(ok, Ui.fill());
        Dialog d = Ui.sheet(this, Ui.L("Đổi tên máy", "Rename phone"), c);
        ok.setOnClickListener(v -> {
            String s = n.getText().toString().trim();
            if (s.isEmpty()) return;
            Api.bg(() -> Api.rpc("cloudphone_rename", new JSONObject().put("p_id", p.optString("id")).put("p_name", s)),
                    r -> { d.dismiss(); refresh(); }, m -> Ui.toast(this, m));
        });
        d.show();
    }

    // ---------------- TAB 2: Thuê máy ----------------
    void renderRent() {
        LinearLayout body = Ui.col(this, 14);
        body.addView(Ui.h1(this, Ui.L("Thuê điện thoại đám mây", "Rent a cloud phone")), Ui.fill());
        body.addView(Ui.muted(this, Ui.L("Chọn gợi ý bên dưới là xong — hoặc tự chỉnh RAM, CPU, số ngày. Máy mới tinh, có Play Store.",
                "Pick a preset below — or set RAM, CPU and days yourself. Fresh device with Play Store.")), Ui.fill());

        int minR = price.optInt("min_ram", 1), maxR = price.optInt("max_ram", 16), minC = price.optInt("min_cpu", 1), maxC = price.optInt("max_cpu", 8);
        final int mpu = maxPerUser(), left = mpu > 0 ? mpu - myLive() : 20;
        if (mpu > 0 && left <= 0) {            // đã đủ số máy cho phép → không cho thuê thêm
            LinearLayout full = Ui.card(this);
            full.addView(Ui.text(this, Ui.L("Bạn đã có đủ " + mpu + " máy", "You already have " + mpu + " phone(s)"), 17, Ui.FG, true));
            full.addView(Ui.muted(this, Ui.L("Mỗi tài khoản chỉ được thuê tối đa " + mpu + " máy. Gia hạn máy đang có, hoặc xoá máy cũ để thuê máy khác.",
                    "Each account can rent up to " + mpu + " phone(s). Renew your phone, or delete it to rent another.")));
            Button go = Ui.button(this, Ui.L("Xem máy của tôi", "My phones"), Ui.PRIMARY);
            go.setOnClickListener(v -> show(0));
            full.addView(go, Ui.fill());
            body.addView(full, Ui.fill());
            page(body);
            return;
        }
        if (left <= 1) sQty = 1;
        java.util.List<String> avs = andList();
        if (!avs.contains(sAnd)) sAnd = avs.get(0);
        if (!freeLive()) sFree = false;
        int minD = price.optInt("min_days", 1), maxD = price.optInt("max_days", 90), minF = price.optInt("min_fps", 10), maxF = price.optInt("max_fps", 120);
        sRam = clamp(sRam, minR, maxR); sCpu = clamp(sCpu, minC, maxC); sDays = clamp(sDays, minD, maxD); sFps = clamp(sFps, minF, maxF);

        TextView total = Ui.text(this, "", 22, Ui.SKY, true);
        TextView calc = Ui.text(this, "", 11.5f, Ui.MUTED, false);
        calc.setMaxLines(2);
        TextView err = Ui.text(this, "", 12.5f, Ui.BAD, false);
        Button buy = Ui.button(this, "", Ui.PRIMARY);

        Runnable upd = () -> {
            double t = sFree ? 0 : ctv(perDay(sRam, sCpu) * sDays) * sQty, bal = prof.optDouble("balance", 0);
            total.setText(Ui.vnd(t));
            calc.setText("(" + sRam + "GB × " + Ui.vnd(price.optDouble("price_ram_gb_day", 2500)) + " + " + sCpu + Ui.L(" nhân × ", " cores × ")
                    + Ui.vnd(price.optDouble("price_cpu_day", 2000)) + ") × " + sDays + Ui.L(" ngày", " days")
                    + (isCtv() ? " − " + prof.optInt("ctv_discount") + "% CTV" : "") + (sQty > 1 ? " × " + sQty + Ui.L(" máy", " phones") : "")
                    + "\n" + Ui.L("Số dư: ", "Balance: ") + Ui.vnd(bal));
            String bad = check();
            err.setText(bad.isEmpty() && bal < t ? Ui.L("Số dư không đủ — cần nạp thêm ", "Not enough balance — top up ") + Ui.vnd(t - bal) : bad);
            if (sFree) calc.setText(Ui.L("🎁 Miễn phí " + price.optInt("free_days", 1) + " ngày", "🎁 Free for " + price.optInt("free_days", 1) + " day(s)") + "\n" + Ui.L("Số dư: ", "Balance: ") + Ui.vnd(bal));
            buy.setText(sFree ? Ui.L("🎁 Nhận máy miễn phí", "🎁 Get free phone") : bad.isEmpty() && bal < t ? Ui.L("Nạp tiền", "Top up") : Ui.L("Thuê ngay", "Rent now") + (sQty > 1 ? " " + sQty + Ui.L(" máy", " phones") : ""));
            buy.setEnabled(bad.isEmpty());
            buy.setAlpha(bad.isEmpty() ? 1f : .5f);
            err.setVisibility(err.getText().length() == 0 ? View.GONE : View.VISIBLE);
        };

        // 🎁 Miễn phí ưu đãi (khi admin bật)
        if (freeLive()) {
            LinearLayout fc = Ui.card(this);
            TextView fch = Ui.chip(this, Ui.L("🎁 Nhận máy MIỄN PHÍ " + price.optInt("free_days", 1) + " ngày", "🎁 Get a FREE phone for " + price.optInt("free_days", 1) + " day(s)"), sFree);
            fc.addView(fch, Ui.fill());
            fc.addView(Ui.muted(this, Ui.L("Tối đa " + price.optInt("free_max_ram", 4) + "GB RAM · " + price.optInt("free_max_cpu", 4) + " nhân · mỗi tài khoản dùng thử 1 máy. Bấm lần nữa để tắt nếu muốn thuê trả phí.",
                    "Up to " + price.optInt("free_max_ram", 4) + "GB RAM · " + price.optInt("free_max_cpu", 4) + " cores · one free trial per account. Tap again to rent a paid phone instead.")));
            fch.setOnClickListener(v -> { sFree = !sFree; if (sFree) { sQty = 1; sRam = Math.min(sRam, price.optInt("free_max_ram", 4)); sCpu = Math.min(sCpu, price.optInt("free_max_cpu", 4)); } renderRent(); });
            body.addView(fc, Ui.fill());
        }
        // Gợi ý cấu hình — bấm 1 lần là điền RAM + CPU
        if (!sFree) {
            LinearLayout pc = Ui.card(this);
            pc.addView(Ui.text(this, Ui.L("💡 Gợi ý cấu hình — bấm để chọn nhanh", "💡 Presets — tap to fill"), 14, Ui.MUTED, true));
            Ui.Wrap pw = new Ui.Wrap(this, 8);
            String[][] PRE = {{"💬", Ui.L("Treo app", "Idle apps"), "2", "2"}, {"🎮", Ui.L("Game nhẹ", "Light games"), "4", "2"},
                    {"🟥", "Roblox", "4", "4"}, {"🔥", Ui.L("Game nặng", "Heavy games"), "8", "8"}};
            for (String[] pr : PRE) {
                final int pr0 = clamp(Integer.parseInt(pr[2]), minR, maxR), pc0 = clamp(Integer.parseInt(pr[3]), minC, maxC);
                TextView ch = Ui.chip(this, pr[0] + " " + pr[1] + "\n" + pr0 + "GB · " + pc0 + Ui.L(" nhân", " cores"), sRam == pr0 && sCpu == pc0);
                ch.setTextSize(13);
                ch.setOnClickListener(v -> { sRam = pr0; sCpu = pc0; renderRent(); });
                pw.addView(ch);
            }
            pc.addView(pw, Ui.fill());
            body.addView(pc, Ui.fill());
        }
        // Phiên bản Android
        LinearLayout ac = Ui.col(this, 8);
        ac.addView(Ui.text(this, Ui.L("Phiên bản Android", "Android version"), 14, Ui.MUTED, true));
        Ui.Wrap aw = new Ui.Wrap(this, 8);
        for (String v : avs) {
            TextView ch = Ui.chip(this, "Android " + v + ("9".equals(v) ? Ui.L("  🎮 64-bit · chạy Roblox", "  🎮 64-bit · runs Roblox") : "12".equals(v) ? Ui.L("  ⭐ Khuyên dùng", "  ⭐ Best")
                    : "10".equals(v) ? Ui.L("  ⚡ GPU · app x86", "  ⚡ GPU · x86 apps") : ""), v.equals(sAnd));
            ch.setTag(v);
            ch.setOnClickListener(x -> {
                sAnd = (String) x.getTag();
                for (int i = 0; i < aw.getChildCount(); i++) Ui.setChip((TextView) aw.getChildAt(i), aw.getChildAt(i).getTag().equals(sAnd));
                upd.run();
            });
            aw.addView(ch);
        }
        ac.addView(aw, Ui.fill());
        body.addView(boxed(ac), Ui.fill());

        body.addView(boxed(group(Ui.L("RAM", "RAM"), RAM, minR, maxR, "GB", v -> { sRam = v; upd.run(); }, () -> sRam)), Ui.fill());
        body.addView(boxed(group(Ui.L("CPU (nhân)", "CPU cores"), CPU, minC, maxC, "", v -> { sCpu = v; upd.run(); }, () -> sCpu)), Ui.fill());
        if (!sFree) body.addView(boxed(group(Ui.L("Số ngày", "Days"), DAYS, minD, maxD, Ui.L(" ngày", "d"), v -> { sDays = v; upd.run(); }, () -> sDays)), Ui.fill());
        if (!sFree && left > 1) body.addView(boxed(group(Ui.L("Số lượng máy", "Quantity"), QTY, 1, Math.min(20, left), Ui.L(" máy", ""), v -> { sQty = v; upd.run(); }, () -> sQty)), Ui.fill());
        body.addView(boxed(group("FPS", FPS, minF, maxF, "", v -> { sFps = v; upd.run(); }, () -> sFps)), Ui.fill());

        // Độ phân giải
        LinearLayout rc = Ui.col(this, 8);
        rc.addView(Ui.text(this, Ui.L("Độ phân giải", "Resolution"), 14, Ui.MUTED, true));
        Ui.Wrap rw = new Ui.Wrap(this, 8);
        for (String[] r : RES) {
            TextView ch = Ui.chip(this, Ui.L(r[1], r[0]) + "\n" + r[0], r[0].equals(sRes));
            ch.setTextSize(13);
            ch.setTag(r[0]);
            ch.setOnClickListener(v -> {
                sRes = (String) v.getTag();
                for (int i = 0; i < rw.getChildCount(); i++) Ui.setChip((TextView) rw.getChildAt(i), rw.getChildAt(i).getTag().equals(sRes));
                upd.run();
            });
            rw.addView(ch);
        }
        rc.addView(rw, Ui.fill());
        body.addView(boxed(rc), Ui.fill());

        // Thanh thanh toán ghim dưới màn hình: luôn thấy tổng tiền + nút Thuê ngay
        LinearLayout sum = Ui.col(this, 6);
        sum.setPadding(Ui.dp(14), Ui.dp(10), Ui.dp(10), Ui.dp(10));
        sum.setBackground(Ui.ringed(Ui.PANEL, 20));
        sum.setElevation(Ui.dp(8));
        sum.addView(err, Ui.fill());
        LinearLayout sr = Ui.row(this, 10);
        LinearLayout st = Ui.col(this, 0);
        st.addView(Ui.text(this, Ui.L("Tổng thanh toán", "Total"), 12, Ui.MUTED, false));
        st.addView(total);
        st.addView(calc);
        sr.addView(st, Ui.weight(1));
        buy.setMinWidth(Ui.dp(132));
        sr.addView(buy, Ui.wrap());
        sum.addView(sr, Ui.fill());

        buy.setOnClickListener(v -> {
            double t = sFree ? 0 : ctv(perDay(sRam, sCpu) * sDays) * sQty;
            if (prof.optDouble("balance", 0) < t) { openTopup(); return; }
            Ui.confirm(this, Ui.L("Xác nhận thuê máy", "Confirm"),
                    "Android " + sAnd + " · " + sQty + Ui.L(" máy · ", " phone(s) · ") + sRam + "GB · " + sCpu + Ui.L(" nhân · ", " cores · ") + sDays + Ui.L(" ngày · ", " days · ") + sRes + " · " + sFps + " FPS\n"
                            + Ui.L("Trừ ", "Charge ") + Ui.vnd(t) + Ui.L(" từ số dư.", " from balance."),
                    Ui.L("Thanh toán", "Pay"), () -> order(buy));
        });
        upd.run();
        body.setTag("rent");
        page(body);
        body.setPadding(body.getPaddingLeft(), body.getPaddingTop(), body.getPaddingRight(), Ui.dp(150));
        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        blp.setMargins(Ui.dp(10), 0, Ui.dp(10), Ui.dp(8));
        content.addView(sum, blp);
    }

    /** Bọc 1 nhóm lựa chọn vào thẻ viền neon cho gọn mắt. */
    private View boxed(View v) {
        LinearLayout c = Ui.card(this);
        c.setPadding(Ui.dp(14), Ui.dp(12), Ui.dp(14), Ui.dp(14));
        c.addView(v, Ui.fill());
        return c;
    }

    private void order(Button buy) {
        buy.setEnabled(false);
        Api.bg(() -> Api.rpc("cloudphone_order_custom", new JSONObject().put("p_ram", sRam).put("p_cpu", sCpu).put("p_days", sDays)
                .put("p_res", sRes).put("p_name", JSONObject.NULL).put("p_fps", sFps).put("p_qty", sFree ? 1 : sQty)
                .put("p_android", sAnd.isEmpty() ? "12" : sAnd).put("p_free", sFree)), r -> {
            Ui.toast(this, Ui.L("Đã đặt máy! Máy đang khởi tạo, khoảng 2–3 phút.", "Ordered! Your phone is being created (2–3 min)."));
            show(0);
            refresh();
        }, m -> { buy.setEnabled(true); Ui.toast(this, m); });
    }

    String check() {
        if (status.optBoolean("maint", false)) return Ui.L("Hệ thống đang bảo trì, vui lòng quay lại sau.", "Under maintenance, please come back later.");
        if (status.has("online") && !status.optBoolean("online", true)) return Ui.L("Máy chủ đang tạm ngưng.", "Server is offline.");
        if (sFree && !isAdmin() && (sRam > price.optInt("free_max_ram", 4) || sCpu > price.optInt("free_max_cpu", 4)))
            return Ui.L("Máy miễn phí tối đa " + price.optInt("free_max_ram", 4) + "GB RAM · " + price.optInt("free_max_cpu", 4) + " nhân — chọn nhỏ hơn.",
                    "Free phones are limited to " + price.optInt("free_max_ram", 4) + "GB RAM · " + price.optInt("free_max_cpu", 4) + " cores.");
        if (status.has("free_ram") && (sRam * sQty > status.optDouble("free_ram") || sCpu * sQty > status.optDouble("free_cpu")))
            return Ui.L("Máy chủ chỉ còn trống " + (int) status.optDouble("free_ram") + "GB RAM, " + (int) status.optDouble("free_cpu") + " nhân — chọn ít hơn.",
                    "Server only has " + (int) status.optDouble("free_ram") + "GB RAM, " + (int) status.optDouble("free_cpu") + " cores free.");
        return "";
    }

    static int clamp(int v, int lo, int hi) { return Math.max(lo, Math.min(hi, v)); }

    interface IntSet { void set(int v); }
    interface IntGet { int get(); }

    private View group(String title, int[] vals, int min, int max, String suf, IntSet set, IntGet get) {
        LinearLayout g = Ui.col(this, 8);
        LinearLayout hr = Ui.row(this, 8);
        hr.addView(Ui.text(this, title, 14, Ui.MUTED, true), Ui.weight(1));
        hr.addView(Ui.text(this, min + "–" + max, 12, Ui.MUTED, false), Ui.wrap());
        g.addView(hr, Ui.fill());
        Ui.Wrap w = new Ui.Wrap(this, 8);
        EditText x = Ui.input(this, Ui.L("Khác", "Other"), InputType.TYPE_CLASS_NUMBER);
        x.setMinWidth(Ui.dp(80));
        x.setPadding(Ui.dp(12), Ui.dp(8), Ui.dp(12), Ui.dp(8));
        for (int v : vals) {
            if (v < min || v > max) continue;
            TextView ch = Ui.chip(this, v + suf, v == get.get());
            ch.setTag(v);
            ch.setOnClickListener(c -> {
                set.set((int) c.getTag());
                x.setText("");
                for (int i = 0; i < w.getChildCount(); i++) if (w.getChildAt(i) instanceof TextView && w.getChildAt(i).getTag() != null)
                    Ui.setChip((TextView) w.getChildAt(i), (int) w.getChildAt(i).getTag() == get.get());
            });
            w.addView(ch);
        }
        w.addView(x);
        x.addTextChangedListener(new Simple(() -> {
            try {
                int v = Integer.parseInt(x.getText().toString());
                if (v >= min && v <= max) {
                    set.set(v);
                    for (int i = 0; i < w.getChildCount(); i++) if (!(w.getChildAt(i) instanceof EditText) && w.getChildAt(i).getTag() != null)
                        Ui.setChip((TextView) w.getChildAt(i), (int) w.getChildAt(i).getTag() == v);
                }
            } catch (Exception ignored) { }
        }));
        g.addView(w, Ui.fill());
        return g;
    }

    // ---------------- TAB 3: Tài khoản ----------------
    void renderAccount() {
        LinearLayout body = Ui.col(this, 14);
        LinearLayout c = Ui.card(this);
        String name = prof.optString("username", "");
        c.addView(Ui.h1(this, "👤 " + (name.isEmpty() || name.equals("null") ? Ui.L("Tài khoản", "Account") : name)));
        c.addView(Ui.muted(this, Api.email));
        c.addView(Ui.text(this, Ui.L("Số dư: ", "Balance: ") + Ui.vnd(prof.optDouble("balance", 0)), 18, Ui.SKY, true));
        if (isCtv()) c.addView(Ui.pill(this, "🤝 CTV −" + prof.optInt("ctv_discount") + "%", Ui.OK), Ui.wrap());
        Button top = Ui.button(this, "💳 " + Ui.L("Nạp tiền", "Top up"), Ui.PRIMARY);
        top.setOnClickListener(v -> openTopup());
        c.addView(top, Ui.fill());
        body.addView(c, Ui.fill());

        LinearLayout s = Ui.card(this);
        s.addView(Ui.h2(this, Ui.L("Cài đặt", "Settings")));
        TextView nt = Ui.text(this, "", 15, Ui.FG, false);
        Runnable ntDraw = () -> nt.setText((ExpiryAlarm.enabled(this) ? "🔔 " : "🔕 ") + Ui.L("Nhắc khi máy sắp hết hạn: ", "Expiry reminders: ")
                + (ExpiryAlarm.enabled(this) ? Ui.L("Bật", "On") : Ui.L("Tắt", "Off")));
        nt.setPadding(0, Ui.dp(8), 0, Ui.dp(8));
        nt.setOnClickListener(v -> { ExpiryAlarm.setEnabled(this, !ExpiryAlarm.enabled(this)); ExpiryAlarm.schedule(this, phones); ntDraw.run(); });
        ntDraw.run();
        s.addView(nt, Ui.fill());
        s.addView(Ui.muted(this, Ui.L("Nhắc trước 1 ngày và 1 giờ khi máy sắp hết hạn.", "Reminds you 1 day and 1 hour before a phone expires.")));
        body.addView(s, Ui.fill());

        LinearLayout l = Ui.card(this);
        l.addView(Ui.h2(this, Ui.L("Hỗ trợ", "Support")));
        Button web = Ui.button(this, "🌐 " + Ui.L("Mở website blueroblox.shop", "Open blueroblox.shop"), Ui.GHOST);
        web.setOnClickListener(v -> openWeb(Api.WEB));
        l.addView(web, Ui.fill());
        Button pol = Ui.button(this, "📄 " + Ui.L("Chính sách & điều khoản", "Policy & terms"), Ui.GHOST);
        pol.setOnClickListener(v -> openWeb(Api.WEB + "/#policy"));
        l.addView(pol, Ui.fill());
        body.addView(l, Ui.fill());

        Button out = Ui.button(this, Ui.L("Đăng xuất", "Log out"), Ui.DANGER);
        out.setOnClickListener(v -> Ui.confirm(this, Ui.L("Đăng xuất?", "Log out?"), Ui.L("Máy của bạn vẫn chạy bình thường.", "Your phones keep running."),
                Ui.L("Đăng xuất", "Log out"), () -> { ExpiryAlarm.cancelAll(this); Api.logout(); toLogin(); }));
        body.addView(out, Ui.fill());
        Button upd = Ui.button(this, "🔄 " + Ui.L("Kiểm tra cập nhật", "Check for updates"), Ui.GHOST);
        upd.setOnClickListener(v -> Updater.check(this, true));
        body.addView(upd, Ui.fill());
        TextView ver = Ui.muted(this, "BlueRoblox Cloud Phone v" + Api.version);
        ver.setGravity(Gravity.CENTER);
        body.addView(ver, Ui.fill());
        page(body);
    }

    void openTopup() { openWeb(Api.WEB + "/?topup=1"); }

    void openWeb(String u) {
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(u))); }
        catch (Exception e) { Ui.toast(this, u); }
    }

    /** TextWatcher gọn. */
    static class Simple implements android.text.TextWatcher {
        private final Runnable r;
        Simple(Runnable r) { this.r = r; }
        public void beforeTextChanged(CharSequence s, int a, int b, int c) { }
        public void onTextChanged(CharSequence s, int a, int b, int c) { }
        public void afterTextChanged(android.text.Editable e) { r.run(); }
    }
}
