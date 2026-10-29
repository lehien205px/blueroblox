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
    String sRes = "720x1280";

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Ui.init(this);
        if (!Api.loggedIn()) { toLogin(); return; }
        Ui.edgeToEdge(this);

        LinearLayout root = Ui.col(this, 0);
        root.setBackgroundColor(Ui.BG);

        // Đầu trang
        LinearLayout top = Ui.row(this, 10);
        top.setPadding(Ui.dp(18), Ui.dp(12), Ui.dp(18), Ui.dp(10));
        TextView brand = Ui.text(this, "BlueRoblox Cloud", 19, Ui.FG, true);
        top.addView(brand, Ui.weight(1));
        balTop = Ui.pill(this, "…", Ui.SKY);
        balTop.setOnClickListener(v -> openTopup());
        top.addView(balTop, Ui.wrap());
        root.addView(top, Ui.fill());

        content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));

        // Thanh điều hướng dưới
        LinearLayout bar = Ui.row(this, 0);
        bar.setBackground(Ui.round(Ui.PANEL, 0, Ui.LINE));
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
            nav[i].setBackground(i == t ? Ui.round(Color.argb(34, 61, 123, 255), 14, 0) : null);
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

        LinearLayout acts = Ui.row(this, 8);
        Button ctl = Ui.button(this, "▶ " + Ui.L("Điều khiển", "Control"), Ui.PRIMARY);
        boolean canCtl = "active".equals(st) && !lk && !status.optBoolean("maint", false);
        ctl.setEnabled(canCtl);
        ctl.setAlpha(canCtl ? 1f : .45f);
        ctl.setOnClickListener(v -> startActivity(new Intent(this, ControlActivity.class).putExtra("id", p.optString("id"))));
        acts.addView(ctl, Ui.weight(1.4f));
        Button rn = Ui.button(this, Ui.L("Gia hạn", "Renew"), Ui.GHOST);
        rn.setOnClickListener(v -> renew(p));
        rn.setEnabled(!lk && ("active".equals(st) || "expired".equals(st)));
        acts.addView(rn, Ui.weight(1));
        Button more = Ui.button(this, "⋯", Ui.GHOST);
        more.setOnClickListener(v -> rename(p));
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
        body.addView(Ui.muted(this, Ui.L("Android 24/7 · cài game, treo acc · điều khiển ngay trong app. Máy mới tinh, có sẵn Play Store, Firefox, Brave.",
                "Android 24/7 · games & idle farming · control in the app. Fresh device with Play Store, Firefox, Brave.")), Ui.fill());

        int minR = price.optInt("min_ram", 1), maxR = price.optInt("max_ram", 16), minC = price.optInt("min_cpu", 1), maxC = price.optInt("max_cpu", 8);
        int minD = price.optInt("min_days", 1), maxD = price.optInt("max_days", 90), minF = price.optInt("min_fps", 10), maxF = price.optInt("max_fps", 120);
        sRam = clamp(sRam, minR, maxR); sCpu = clamp(sCpu, minC, maxC); sDays = clamp(sDays, minD, maxD); sFps = clamp(sFps, minF, maxF);

        TextView total = Ui.text(this, "", 26, Ui.FG, true);
        TextView calc = Ui.muted(this, "");
        TextView err = Ui.text(this, "", 13.5f, Ui.BAD, false);
        Button buy = Ui.button(this, "", Ui.PRIMARY);

        Runnable upd = () -> {
            double t = ctv(perDay(sRam, sCpu) * sDays) * sQty, bal = prof.optDouble("balance", 0);
            total.setText(Ui.vnd(t));
            calc.setText("(" + sRam + "GB × " + Ui.vnd(price.optDouble("price_ram_gb_day", 2500)) + " + " + sCpu + Ui.L(" nhân × ", " cores × ")
                    + Ui.vnd(price.optDouble("price_cpu_day", 2000)) + ") × " + sDays + Ui.L(" ngày", " days")
                    + (isCtv() ? " − " + prof.optInt("ctv_discount") + "% CTV" : "") + (sQty > 1 ? " × " + sQty + Ui.L(" máy", " phones") : "")
                    + "\n" + Ui.L("Số dư: ", "Balance: ") + Ui.vnd(bal));
            String bad = check();
            err.setText(bad.isEmpty() && bal < t ? Ui.L("Số dư không đủ — cần nạp thêm ", "Not enough balance — top up ") + Ui.vnd(t - bal) : bad);
            buy.setText(bad.isEmpty() && bal < t ? Ui.L("Nạp tiền", "Top up") : Ui.L("Thuê ngay", "Rent now") + (sQty > 1 ? " " + sQty + Ui.L(" máy", " phones") : ""));
            buy.setEnabled(bad.isEmpty());
            buy.setAlpha(bad.isEmpty() ? 1f : .5f);
        };

        body.addView(group(Ui.L("RAM", "RAM"), RAM, minR, maxR, "GB", v -> { sRam = v; upd.run(); }, () -> sRam), Ui.fill());
        body.addView(group(Ui.L("CPU (nhân)", "CPU cores"), CPU, minC, maxC, "", v -> { sCpu = v; upd.run(); }, () -> sCpu), Ui.fill());
        body.addView(group(Ui.L("Số ngày", "Days"), DAYS, minD, maxD, Ui.L(" ngày", "d"), v -> { sDays = v; upd.run(); }, () -> sDays), Ui.fill());
        body.addView(group(Ui.L("Số lượng máy", "Quantity"), QTY, 1, 20, Ui.L(" máy", ""), v -> { sQty = v; upd.run(); }, () -> sQty), Ui.fill());
        body.addView(group("FPS", FPS, minF, maxF, "", v -> { sFps = v; upd.run(); }, () -> sFps), Ui.fill());

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
        body.addView(rc, Ui.fill());

        LinearLayout sum = Ui.card(this);
        sum.addView(Ui.muted(this, Ui.L("Tổng thanh toán", "Total")));
        sum.addView(total);
        sum.addView(calc);
        sum.addView(err);
        sum.addView(buy, Ui.fill());
        body.addView(sum, Ui.fill());

        buy.setOnClickListener(v -> {
            double t = ctv(perDay(sRam, sCpu) * sDays) * sQty;
            if (prof.optDouble("balance", 0) < t) { openTopup(); return; }
            Ui.confirm(this, Ui.L("Xác nhận thuê máy", "Confirm"),
                    sQty + Ui.L(" máy · ", " phone(s) · ") + sRam + "GB · " + sCpu + Ui.L(" nhân · ", " cores · ") + sDays + Ui.L(" ngày · ", " days · ") + sRes + " · " + sFps + " FPS\n"
                            + Ui.L("Trừ ", "Charge ") + Ui.vnd(t) + Ui.L(" từ số dư.", " from balance."),
                    Ui.L("Thanh toán", "Pay"), () -> order(buy));
        });
        upd.run();
        page(body);
    }

    private void order(Button buy) {
        buy.setEnabled(false);
        Api.bg(() -> Api.rpc("cloudphone_order_custom", new JSONObject().put("p_ram", sRam).put("p_cpu", sCpu).put("p_days", sDays)
                .put("p_res", sRes).put("p_name", JSONObject.NULL).put("p_fps", sFps).put("p_qty", sQty)), r -> {
            Ui.toast(this, Ui.L("Đã đặt máy! Máy đang khởi tạo, khoảng 2–3 phút.", "Ordered! Your phone is being created (2–3 min)."));
            show(0);
            refresh();
        }, m -> { buy.setEnabled(true); Ui.toast(this, m); });
    }

    String check() {
        if (status.optBoolean("maint", false)) return Ui.L("Hệ thống đang bảo trì, vui lòng quay lại sau.", "Under maintenance, please come back later.");
        if (status.has("online") && !status.optBoolean("online", true)) return Ui.L("Máy chủ đang tạm ngưng.", "Server is offline.");
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
