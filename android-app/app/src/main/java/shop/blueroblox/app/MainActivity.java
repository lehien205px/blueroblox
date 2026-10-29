package shop.blueroblox.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.OutputStream;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * App BlueRoblox Cloud: mở web blueroblox.shop trong app (thuê, gia hạn, điều khiển điện thoại đám mây).
 * Mọi chức năng nằm trên web → web cập nhật là app tự có tính năng mới, không cần cài lại.
 */
public class MainActivity extends Activity {

    private static final String HOME = "https://blueroblox.shop/";
    private static final int REQ_FILE = 41;
    private static final Set<String> INSIDE = new HashSet<>(Arrays.asList(
            "blueroblox.shop", "www.blueroblox.shop", "phone.blueroblox.shop"));

    private FrameLayout root;
    private WebView web;
    private ProgressBar bar;
    private LinearLayout errBox;
    private TextView errText;
    private View customView;
    private WebChromeClient.CustomViewCallback customCb;
    private ValueCallback<Uri[]> fileCb;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(7, 12, 31));

        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(7, 12, 31));
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        bar.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.rgb(79, 140, 255)));
        root.addView(bar, new FrameLayout.LayoutParams(-1, dp(3), Gravity.TOP));

        buildErrorView();
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setAllowFileAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(false);            // link mở tab mới → mở ngay trong app
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        s.setUserAgentString(s.getUserAgentString() + " BlueRobloxApp/" + versionName());

        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(web, true);       // khung điều khiển phone.blueroblox.shop cần cookie

        web.addJavascriptInterface(new Bridge(), "BRApp");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        web.setDownloadListener(new Downloader());

        if (state != null && web.restoreState(state) != null) return;
        Uri data = getIntent() != null ? getIntent().getData() : null;
        web.loadUrl(data != null && isInside(data) ? data.toString() : HOME);
    }

    // ---------------- Điều hướng ----------------
    private boolean isInside(Uri u) {
        String h = u.getHost();
        return h != null && ("https".equals(u.getScheme()) && (INSIDE.contains(h) || h.endsWith(".supabase.co")));
    }

    private void openOutside(Uri u) {
        try {
            Intent i;
            if ("intent".equals(u.getScheme())) {
                i = Intent.parseUri(u.toString(), Intent.URI_INTENT_SCHEME);
                i.addCategory(Intent.CATEGORY_BROWSABLE);
                i.setComponent(null);
                i.setSelector(null);
            } else {
                i = new Intent(Intent.ACTION_VIEW, u);
            }
            startActivity(i);
        } catch (ActivityNotFoundException | java.net.URISyntaxException e) {
            Toast.makeText(this, "Không có ứng dụng để mở link này", Toast.LENGTH_SHORT).show();
        }
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (isInside(u)) return false;
            if ("about".equals(u.getScheme()) || "blob".equals(u.getScheme()) || "data".equals(u.getScheme())) return false;
            openOutside(u);                          // Zalo, Discord, Telegram, ngân hàng… → mở bằng app tương ứng
            return true;
        }

        @Override
        public void onPageFinished(WebView v, String url) {
            bar.setVisibility(View.GONE);
            keepAwake(url != null && url.contains("phone.html"));
            CookieManager.getInstance().flush();
        }

        @Override
        public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            if (r.isForMainFrame()) showError(online()
                    ? "Không tải được trang (" + e.getDescription() + ")."
                    : "Không có kết nối mạng. Kiểm tra Wi-Fi / 4G rồi thử lại.");
        }
    }

    private class Chrome extends WebChromeClient {
        @Override
        public void onProgressChanged(WebView v, int p) {
            bar.setVisibility(p < 100 ? View.VISIBLE : View.GONE);
            bar.setProgress(p);
        }

        // Ô chọn file trên web (tải APK / ảnh lên máy ảo)
        @Override
        public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
            if (fileCb != null) fileCb.onReceiveValue(null);
            fileCb = cb;
            Intent i = p.createIntent();
            i.setType("*/*");
            if (p.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
            try {
                startActivityForResult(Intent.createChooser(i, "Chọn file"), REQ_FILE);
            } catch (ActivityNotFoundException e) {
                fileCb = null;
                Toast.makeText(MainActivity.this, "Không mở được trình chọn file", Toast.LENGTH_SHORT).show();
                return false;
            }
            return true;
        }

        // Nút "Toàn màn" trên trang điều khiển
        @Override
        public void onShowCustomView(View view, CustomViewCallback cb) {
            if (customView != null) { cb.onCustomViewHidden(); return; }
            customView = view;
            customCb = cb;
            root.addView(view, new FrameLayout.LayoutParams(-1, -1));
            web.setVisibility(View.GONE);
            setImmersive(true);
        }

        @Override
        public void onHideCustomView() {
            if (customView == null) return;
            root.removeView(customView);
            customView = null;
            web.setVisibility(View.VISIBLE);
            setImmersive(false);
            if (customCb != null) customCb.onCustomViewHidden();
            customCb = null;
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_FILE && fileCb != null) {
            Uri[] out = null;
            if (res == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int n = data.getClipData().getItemCount();
                    out = new Uri[n];
                    for (int k = 0; k < n; k++) out[k] = data.getClipData().getItemAt(k).getUri();
                } else if (data.getData() != null) {
                    out = new Uri[]{data.getData()};
                }
            }
            fileCb.onReceiveValue(out);
            fileCb = null;
            return;
        }
        super.onActivityResult(req, res, data);
    }

    // ---------------- Tải file từ web về điện thoại ----------------
    private class Downloader implements DownloadListener {
        @Override
        public void onDownloadStart(String url, String ua, String cd, String mime, long len) {
            String name = URLUtil.guessFileName(url, cd, mime);
            if (url.startsWith("blob:")) {          // ảnh chụp màn hình… tạo bằng JS → lấy dữ liệu qua cầu nối
                web.evaluateJavascript("(function(){fetch('" + url + "').then(r=>r.blob()).then(b=>{var f=new FileReader();"
                        + "f.onload=function(){BRApp.save('" + name.replace("'", "") + "',b.type||'application/octet-stream',String(f.result).split(',')[1]);};"
                        + "f.readAsDataURL(b);});})();", null);
                return;
            }
            if (!url.startsWith("http")) return;
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.setMimeType(mime);
                String ck = CookieManager.getInstance().getCookie(url);
                if (ck != null) r.addRequestHeader("Cookie", ck);
                if (ua != null) r.addRequestHeader("User-Agent", ua);
                r.setTitle(name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(MainActivity.this, "Đang tải " + name, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                openOutside(Uri.parse(url));
            }
        }
    }

    private class Bridge {
        @JavascriptInterface
        public void save(String name, String mime, String b64) {
            runOnUiThread(() -> {
                if (Build.VERSION.SDK_INT < 29) {
                    Toast.makeText(MainActivity.this, "Máy Android 9 trở xuống: hãy lưu file bằng trình duyệt", Toast.LENGTH_LONG).show();
                    return;
                }
                try {
                    byte[] bytes = Base64.decode(b64, Base64.DEFAULT);
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                    cv.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                    cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                    Uri u = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (u == null) throw new Exception("no uri");
                    try (OutputStream o = getContentResolver().openOutputStream(u)) { o.write(bytes); }
                    Toast.makeText(MainActivity.this, "Đã lưu vào Download: " + name, Toast.LENGTH_SHORT).show();
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "Không lưu được file", Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public String version() { return versionName(); }
    }

    // ---------------- Màn báo lỗi mạng ----------------
    private void buildErrorView() {
        errBox = new LinearLayout(this);
        errBox.setOrientation(LinearLayout.VERTICAL);
        errBox.setGravity(Gravity.CENTER);
        errBox.setPadding(dp(32), dp(32), dp(32), dp(32));
        errBox.setBackgroundColor(Color.rgb(7, 12, 31));
        errBox.setVisibility(View.GONE);

        TextView t = new TextView(this);
        t.setText("Mất kết nối");
        t.setTextColor(Color.WHITE);
        t.setTextSize(22);
        t.setGravity(Gravity.CENTER);
        errBox.addView(t);

        errText = new TextView(this);
        errText.setTextColor(Color.rgb(160, 176, 210));
        errText.setTextSize(15);
        errText.setGravity(Gravity.CENTER);
        errText.setPadding(0, dp(10), 0, dp(20));
        errBox.addView(errText);

        Button b = new Button(this);
        b.setText("Thử lại");
        b.setOnClickListener(v -> { errBox.setVisibility(View.GONE); web.reload(); });
        errBox.addView(b, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        root.addView(errBox, new FrameLayout.LayoutParams(-1, -1));
    }

    private void showError(String msg) {
        errText.setText(msg);
        errBox.setVisibility(View.VISIBLE);
    }

    // ---------------- Tiện ích ----------------
    private boolean online() {
        ConnectivityManager c = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        NetworkInfo n = c != null ? c.getActiveNetworkInfo() : null;
        return n != null && n.isConnected();
    }

    private void keepAwake(boolean on) {    // đang điều khiển máy ảo → màn hình điện thoại không tự tắt
        if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    @SuppressWarnings("deprecation")
    private void setImmersive(boolean on) {
        getWindow().getDecorView().setSystemUiVisibility(on
                ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                  | View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                : View.SYSTEM_UI_FLAG_VISIBLE);
    }

    private String versionName() {
        try { return getPackageManager().getPackageInfo(getPackageName(), 0).versionName; }
        catch (Exception e) { return "1"; }
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        Uri u = intent.getData();
        if (u != null && isInside(u)) web.loadUrl(u.toString());
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (customView != null) { new Chrome().onHideCustomView(); return; }
        if (errBox.getVisibility() == View.VISIBLE) { errBox.setVisibility(View.GONE); }
        if (web.canGoBack()) { web.goBack(); return; }
        super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onPause() { super.onPause(); CookieManager.getInstance().flush(); }

    @Override
    protected void onDestroy() {
        if (web != null) { root.removeView(web); web.destroy(); }
        super.onDestroy();
    }
}
