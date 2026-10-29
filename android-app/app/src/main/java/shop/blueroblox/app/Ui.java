package shop.blueroblox.app;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.os.Build;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.NumberFormat;
import java.util.Locale;

/** Màu, chữ, nút, thẻ… dùng chung cho mọi màn hình (giao diện tối, xanh dương như web). */
public final class Ui {
    public static final int BG = Color.rgb(6, 10, 28);
    public static final int PANEL = Color.rgb(14, 22, 56);
    public static final int PANEL2 = Color.rgb(22, 34, 79);
    public static final int LINE = Color.argb(40, 143, 208, 255);
    public static final int LINE2 = Color.argb(80, 143, 208, 255);
    public static final int FG = Color.rgb(238, 243, 255);
    public static final int MUTED = Color.rgb(151, 165, 207);
    public static final int BLUE = Color.rgb(61, 123, 255);
    public static final int SKY = Color.rgb(143, 208, 255);
    public static final int OK = Color.rgb(60, 224, 176);
    public static final int WAIT = Color.rgb(255, 200, 97);
    public static final int BAD = Color.rgb(255, 107, 129);

    public static boolean vi() { return "vi".equals(Locale.getDefault().getLanguage()); }
    public static String L(String vi, String en) { return vi() ? vi : en; }

    private static float density = 3f;
    public static void init(Context c) { density = c.getResources().getDisplayMetrics().density; Api.init(c); }
    public static int dp(float v) { return Math.round(v * density); }

    public static String vnd(double v) {
        return NumberFormat.getInstance(new Locale("vi", "VN")).format(Math.round(v)) + "đ";
    }

    // ---------------- Hình nền ----------------
    public static GradientDrawable round(int fill, int radius, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(radius));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    public static void ripple(View v, int fill, int radius, int stroke) {
        v.setBackground(new RippleDrawable(ColorStateList.valueOf(Color.argb(60, 143, 208, 255)), round(fill, radius, stroke), null));
    }

    // ---------------- Chữ ----------------
    public static TextView text(Context c, String s, float sp, int color, boolean bold) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        t.setLineSpacing(0, 1.15f);
        return t;
    }

    public static TextView h1(Context c, String s) { return text(c, s, 24, FG, true); }
    public static TextView h2(Context c, String s) { return text(c, s, 17, FG, true); }
    public static TextView muted(Context c, String s) { return text(c, s, 13.5f, MUTED, false); }

    public static TextView pill(Context c, String s, int color) {
        TextView t = text(c, s, 12, color, true);
        t.setPadding(dp(10), dp(3), dp(10), dp(4));
        t.setBackground(round(Color.argb(30, Color.red(color), Color.green(color), Color.blue(color)), 99,
                Color.argb(110, Color.red(color), Color.green(color), Color.blue(color))));
        return t;
    }

    // ---------------- Nút ----------------
    public static final int PRIMARY = 0, GHOST = 1, DANGER = 2;

    public static Button button(Context c, String s, int kind) {
        Button b = new Button(c);
        b.setText(s);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        b.setStateListAnimator(null);
        b.setMinHeight(dp(46));
        b.setMinimumHeight(dp(46));
        b.setPadding(dp(16), 0, dp(16), 0);
        if (kind == PRIMARY) { b.setTextColor(Color.WHITE); ripple(b, BLUE, 12, 0); }
        else if (kind == DANGER) { b.setTextColor(BAD); ripple(b, Color.argb(20, 255, 107, 129), 12, Color.argb(110, 255, 107, 129)); }
        else { b.setTextColor(FG); ripple(b, PANEL2, 12, LINE2); }
        return b;
    }

    /** Nút nhỏ có biểu tượng chữ (emoji) + nhãn, dùng cho thanh công cụ. */
    public static LinearLayout tool(Context c, String icon, String label, View.OnClickListener on) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setGravity(Gravity.CENTER);
        l.setPadding(dp(4), dp(8), dp(4), dp(8));
        TextView i = text(c, icon, 20, FG, false);
        i.setGravity(Gravity.CENTER);
        TextView t = text(c, label, 11.5f, MUTED, false);
        t.setGravity(Gravity.CENTER);
        t.setMaxLines(2);
        l.addView(i);
        l.addView(t);
        l.setClickable(true);
        l.setFocusable(true);
        l.setContentDescription(label);
        ripple(l, Color.TRANSPARENT, 12, 0);
        l.setOnClickListener(on);
        return l;
    }

    // ---------------- Ô nhập ----------------
    public static EditText input(Context c, String hint, int type) {
        EditText e = new EditText(c);
        e.setHint(hint);
        e.setHintTextColor(Color.argb(150, 151, 165, 207));
        e.setTextColor(FG);
        e.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        e.setInputType(type);
        e.setSingleLine(type != (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE));
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        e.setBackground(round(Color.rgb(9, 15, 40), 12, LINE2));
        e.setMinHeight(dp(48));
        return e;
    }

    // ---------------- Bố cục ----------------
    public static LinearLayout col(Context c, int gapDp) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        if (gapDp > 0) {
            l.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE);
            GradientDrawable d = new GradientDrawable();
            d.setSize(1, dp(gapDp));
            d.setColor(Color.TRANSPARENT);
            l.setDividerDrawable(d);
        }
        return l;
    }

    public static LinearLayout row(Context c, int gapDp) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        if (gapDp > 0) {
            l.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE);
            GradientDrawable d = new GradientDrawable();
            d.setSize(dp(gapDp), 1);
            d.setColor(Color.TRANSPARENT);
            l.setDividerDrawable(d);
        }
        return l;
    }

    public static LinearLayout card(Context c) {
        LinearLayout l = col(c, 10);
        l.setPadding(dp(16), dp(16), dp(16), dp(16));
        l.setBackground(round(PANEL, 18, LINE));
        return l;
    }

    public static LinearLayout.LayoutParams wrap() { return new LinearLayout.LayoutParams(-2, -2); }
    public static LinearLayout.LayoutParams fill() { return new LinearLayout.LayoutParams(-1, -2); }
    public static LinearLayout.LayoutParams weight(float w) { return new LinearLayout.LayoutParams(0, -2, w); }

    public static ScrollView scroll(Context c, View child) {
        ScrollView s = new ScrollView(c);
        s.setFillViewport(true);
        s.setClipToPadding(false);
        s.addView(child, new FrameLayout.LayoutParams(-1, -2));
        return s;
    }

    /** Chừa chỗ cho thanh trạng thái / điều hướng / bàn phím (Android 15+ vẽ tràn viền). */
    public static void insets(View v, boolean top, boolean bottom) {
        final int l = v.getPaddingLeft(), t = v.getPaddingTop(), r = v.getPaddingRight(), b = v.getPaddingBottom();
        v.setOnApplyWindowInsetsListener((view, in) -> {
            int it, ib, il, ir;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets s = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                android.graphics.Insets k = in.getInsets(WindowInsets.Type.ime());
                it = s.top; ib = Math.max(s.bottom, k.bottom); il = s.left; ir = s.right;
            } else {
                it = in.getSystemWindowInsetTop(); ib = in.getSystemWindowInsetBottom();
                il = in.getSystemWindowInsetLeft(); ir = in.getSystemWindowInsetRight();
            }
            view.setPadding(l + il, t + (top ? it : 0), r + ir, b + (bottom ? ib : 0));
            return in;
        });
        v.requestApplyInsets();
    }

    public static void edgeToEdge(Activity a) {
        Window w = a.getWindow();
        if (Build.VERSION.SDK_INT >= 30) w.setDecorFitsSystemWindows(false);
        else w.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        if (Build.VERSION.SDK_INT < 35) { w.setStatusBarColor(Color.TRANSPARENT); w.setNavigationBarColor(BG); }
        w.getDecorView().setBackgroundColor(BG);
    }

    // ---------------- Thông báo nhanh / hộp thoại ----------------
    public static void toast(Context c, String s) { Toast.makeText(c, s, s.length() > 60 ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show(); }

    /** Hộp thoại kiểu "tấm trượt từ dưới lên" với nội dung tuỳ ý. */
    public static Dialog sheet(Activity a, String title, View content) {
        Dialog d = new Dialog(a);
        d.requestWindowFeature(Window.FEATURE_NO_TITLE);
        LinearLayout box = col(a, 14);
        box.setPadding(dp(20), dp(10), dp(20), dp(20));
        box.setBackground(round(PANEL, 22, LINE2));
        View grip = new View(a);
        grip.setBackground(round(LINE2, 99, 0));
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(dp(40), dp(4));
        gp.gravity = Gravity.CENTER_HORIZONTAL;
        box.addView(grip, gp);
        if (title != null) box.addView(h2(a, title));
        box.addView(content, fill());
        ScrollView sv = scroll(a, box);
        d.setContentView(sv);
        Window w = d.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            w.setGravity(Gravity.BOTTOM);
            w.setDimAmount(0.6f);
            w.getAttributes().windowAnimations = android.R.style.Animation_InputMethod;
        }
        return d;
    }

    public interface Yes { void run(); }

    public static void confirm(Activity a, String title, String msg, String yes, Yes onYes) {
        LinearLayout c = col(a, 16);
        c.addView(muted(a, msg));
        LinearLayout r = row(a, 10);
        Button no = button(a, L("Huỷ", "Cancel"), GHOST);
        Button ok = button(a, yes, PRIMARY);
        r.addView(no, weight(1));
        r.addView(ok, weight(1));
        c.addView(r, fill());
        Dialog d = sheet(a, title, c);
        no.setOnClickListener(v -> d.dismiss());
        ok.setOnClickListener(v -> { d.dismiss(); onYes.run(); });
        d.show();
    }

    // ---------------- Bố cục xếp chip tự xuống dòng ----------------
    public static class Wrap extends ViewGroup {
        private final int gap;
        public Wrap(Context c, int gapDp) { super(c); gap = dp(gapDp); }

        @Override protected void onMeasure(int ws, int hs) {
            int max = MeasureSpec.getSize(ws), x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View ch = getChildAt(i);
                if (ch.getVisibility() == GONE) continue;
                measureChild(ch, MeasureSpec.makeMeasureSpec(max, MeasureSpec.AT_MOST), hs);
                int w = ch.getMeasuredWidth(), h = ch.getMeasuredHeight();
                if (x > 0 && x + w > max) { x = 0; y += lineH + gap; lineH = 0; }
                x += w + gap;
                lineH = Math.max(lineH, h);
            }
            setMeasuredDimension(max, y + lineH);
        }

        @Override protected void onLayout(boolean changed, int l, int t, int r, int b) {
            int max = r - l, x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View ch = getChildAt(i);
                if (ch.getVisibility() == GONE) continue;
                int w = ch.getMeasuredWidth(), h = ch.getMeasuredHeight();
                if (x > 0 && x + w > max) { x = 0; y += lineH + gap; lineH = 0; }
                ch.layout(x, y, x + w, y + h);
                x += w + gap;
                lineH = Math.max(lineH, h);
            }
        }
    }

    /** Chip chọn 1 trong nhiều (VD: 2GB / 4GB / 8GB). */
    public static TextView chip(Context c, String s, boolean on) {
        TextView t = text(c, s, 14, on ? Color.WHITE : FG, on);
        t.setGravity(Gravity.CENTER);
        t.setMinWidth(dp(64));
        t.setPadding(dp(14), dp(10), dp(14), dp(10));
        t.setClickable(true);
        t.setFocusable(true);
        setChip(t, on);
        return t;
    }

    public static void setChip(TextView t, boolean on) {
        t.setSelected(on);
        t.setTextColor(on ? Color.WHITE : FG);
        ripple(t, on ? BLUE : PANEL2, 12, on ? BLUE : LINE2);
    }
}
