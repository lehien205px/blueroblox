# BlueRoblox v2 — Hướng dẫn lên bản mới

Bản v2 chia hệ thống làm 4 phần:

| Phần | Làm gì |
|---|---|
| **Web** cloudblue.vn (trước đây blueroblox.shop) | Chỉ còn: đăng ký, nạp tiền, **mua máy (chọn RAM · CPU · Android 9/12/7)**, danh sách máy, gia hạn, nâng cấp, chia sẻ. Không còn màn điều khiển → web nhẹ, load nhanh. |
| **App PC cho khách** (BlueRobloxPC.exe) | Đăng nhập → thấy máy → bấm **Điều khiển** → cửa sổ máy mượt (mở nhiều máy cùng lúc được). |
| **App Android** (cập nhật) | Màn điều khiển dùng trình phát hình gốc của điện thoại (không còn trang web bên trong). |
| **Máy chủ** (bảng BlueRoblox) | Thêm dịch vụ **phát hình app** (bluestream, cổng 8189). Mỗi máy chủ chọn 1 giả lập: **LDPlayer 9 = Android 9**, **MuMu = Android 12 + 7** (hoặc MEmu = Android 7). Đơn khách tự chạy về đúng máy chủ. |

Đường hình mới: cửa sổ giả lập → card NVIDIA nén → **đi thẳng** tới app khách qua cổng 8189 (không qua Cloudflare, không qua trình duyệt).
Mạng khách yếu thì máy chủ tự hạ chất lượng thay vì để hình trễ dần.

---

## Làm theo thứ tự

### Bước 1 — Supabase: chạy SQL v20
Supabase → SQL Editor → dán toàn bộ `cloud-phones-v20.sql` → **Run**. Chạy lại cũng không sao.

### Bước 2 — Web
Tải lên https://github.com/lehien205px/blueroblox/upload/main/blueroblox-site : `index.html`, `vercel.json`
(Vercel tự cập nhật sau ~1 phút.)

### Bước 3 — Tải code lên GitHub
| Link tải lên | File |
|---|---|
| https://github.com/lehien205px/blueroblox/upload/main | `cloudphoned.py` |
| https://github.com/lehien205px/blueroblox/upload/main/windows | `bluestream.py`, `phonectl.py`, `panel.py`, `setup.ps1`, `blueserver.py` |
| https://github.com/lehien205px/blueroblox/upload/main/pc-client | `bluepc.py`, `brstream.py`, `build.bat`, `requirements.txt`, `icon.png` |
| https://github.com/lehien205px/blueroblox/upload/main/.github/workflows | `pc-client.yml`, `server-exe.yml` |
| https://github.com/lehien205px/blueroblox/upload/main/android-app/app/src/main/java/shop/blueroblox/app | **đủ 11 file** `.java`: `Api`, `Brand`, `CloneLaunch`, `ControlActivity`, `ExpiryAlarm`, `LoginActivity`, `MainActivity`, `StreamClient`, `StreamView`, `Ui`, `Updater` (file `MainActivity.java` đang có trên GitHub bị thiếu nửa → phải tải đè) |
| https://github.com/lehien205px/blueroblox/upload/main/android-app/app/src/main | `AndroidManifest.xml` |
| https://github.com/lehien205px/blueroblox/upload/main/docs | `STREAM-PROTOCOL.md`, `HUONG-DAN-V2.md` |

Tải **từng thư mục một**, bị lỗi thì F5 rồi tải lại.
Sau khi tải xong, GitHub tự build: app Android (~5 phút, khách tự được báo cập nhật) và app PC (~10 phút, ở Releases → `pc-latest`).
Vào tab **Actions**: *Build Android app* và *Build PC client* phải có dấu ✅ xanh. Dấu ❌ đỏ → bấm vào, chụp phần chữ đỏ gửi hỗ trợ (bản lỗi **không** được đưa cho khách).

### Bước 4 — Máy chủ 1 (LDPlayer): tải bản mới
PowerShell (Admin):
```powershell
$G = "https://api.github.com/repos/lehien205px/blueroblox/contents"
foreach ($f in "bluestream.py", "phonectl.py", "panel.py", "setup.ps1", "blueserver.py") { curl.exe -fsSL -H "Accept: application/vnd.github.raw" -o "C:\blue\$f" "$G/windows/$f" }
curl.exe -fsSL -H "Accept: application/vnd.github.raw" -o C:\blue\cloudphoned.py "$G/cloudphoned.py"
python -m pip install --quiet windows-capture numpy cryptography
Select-String -Path C:\blue\bluestream.py, C:\blue\panel.py, C:\blue\phonectl.py, C:\blue\cloudphoned.py -Pattern "BRS2|choose_emu|def create_vm|def android_ok" -List
```
Dòng cuối phải in **4 dòng** (mỗi file 1 dòng). Thiếu dòng nào = file đó chưa lên GitHub.

Rồi tắt hẳn bảng cũ + các dịch vụ cũ và mở lại:
```powershell
Get-Process python, pythonw, mediamtx, ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process "C:\Program Files\Python312\pythonw.exe" '"C:\blue\panel.py"' -WorkingDirectory C:\blue
```
- Bảng hỏi **"Máy chủ này chạy giả lập nào?"** → chọn **LDPlayer 9**.
- Bấm **▶ Bật tất cả**. Đèn mới **Phát hình app :8189** phải xanh.
- Tab **phat-hinh** phải có dòng `đường nối: trực tiếp 192.168.1.6:8189, <IP nhà>:8189`.

Modem đã mở cổng **TCP 8189** về 192.168.1.6 (làm hôm trước) — giữ nguyên. Dòng **UDP 8189** (của bản WebRTC cũ) không cần nữa, xoá đi cho gọn.

### Bước 5 — Thử app PC (trước khi đưa khách)
Trên máy tính bất kỳ (hoặc ngay máy chủ):
```powershell
mkdir C:\bluepc -Force | Out-Null
$G = "https://api.github.com/repos/lehien205px/blueroblox/contents"
foreach ($f in "bluepc.py", "brstream.py") { curl.exe -fsSL -H "Accept: application/vnd.github.raw" -o "C:\bluepc\$f" "$G/pc-client/$f" }
python -m pip install --quiet av pygame-ce numpy
python C:\bluepc\bluepc.py
```
Đăng nhập tài khoản khách → **Điều khiển**. Phím **F3** hiện thông số (khung/giây, Mbps, trễ mạng, "trực tiếp" hay "Cloudflare").
Bản .exe cho khách: GitHub → **Actions** → *Build PC client* xong → web tự có link tải (`/download/BlueRobloxPC.zip`).

### Bước 6 (nên làm) — Đường dự phòng qua Cloudflare
Cho khách ở mạng chặn cổng lạ:
1. Cloudflare → Zero Trust → Networks → Tunnels → đường hầm của máy 1 → **Public Hostname** → **Add**:
   subdomain `stream`, domain `cloudblue.vn`, Type `HTTP`, URL `localhost:8020` → Save.
2. `python C:\blue\doi-ten-mien.py cloudblue.vn` tự ghi `STREAM_WS=wss://stream.cloudblue.vn/v2/stream` vào `cloudphone.conf`.
3. Tắt/bật dịch vụ **phat-hinh** trong bảng.

---

## Thêm máy chủ MuMu (Android 12 + Android 7)

1. Cài **MuMu Player** bản chính thức từ trang MuMu (mumuplayer.com). Không dùng bản sửa / bản tải lại.
2. Cài BlueRoblox như máy chủ 2 cũ (`setup.ps1 -Node win3 -PubHost phone3.cloudblue.vn`), dán khoá, nối Cloudflare.
3. Bảng hỏi giả lập → chọn **MuMu** (bán Android 12 và 7).
4. **Máy mẫu Android 7**: trong MuMu Multi-instance tạo 1 máy **Android 7**, đặt tên đúng `tpl_a7`.
   Nếu MuMu mặc định tạo Android 12 thì làm thêm 1 máy Android 12 tên `tpl_a12` (để chắc chắn). Máy mẫu không bao giờ bị xoá.
5. Modem mở cổng **TCP `8188 + số máy`** (win3 → 8191) về máy này. `setup.ps1` đã ghi sẵn `STREAM_PORT` đúng số đó.
6. Chạy `python C:\blue\phonectl.py probe` → **chụp kết quả gửi hỗ trợ** (để chỉnh khoá cài đặt RAM/CPU của MuMu nếu bản MuMu khác).
7. Bấm 🧪 **Tạo máy thử** → mở bằng app PC thử.

MEmu cũng được (chọn **MEmu** trong bảng): Android 7 tạo sẵn; Android 12 cần máy mẫu `tpl_a12`.

---

## Tên miền cloudblue.vn (05/11/2026)
`blueroblox.shop` bị nơi bán khoá (`clientHold`) chiều 04/11 → chuyển web sang **cloudblue.vn** (Tenten → nameserver Cloudflare → Vercel).
Những chỗ phụ thuộc tên miền và đã đổi:

| Chỗ | Tên miền mới |
|---|---|
| Vercel → Domains | `cloudblue.vn` (chính), `www.cloudblue.vn` chuyển về đó. Bản ghi A `@` + CNAME `www` ở Cloudflare để **mây xám** |
| Đường hầm Cloudflare (máy 1) | `phone.cloudblue.vn` → `http://localhost:8010`, `stream.cloudblue.vn` → `http://localhost:8020` (máy 2: `phone2` / `stream2`) |
| `cloudphone.conf` | `python C:\blue\doi-ten-mien.py cloudblue.vn` (máy 2: thêm `phone2`) — tự sửa PUBLIC_BASE, STREAM_WS, CPAPI_ORIGINS, NOTIFY_URL + link máy khách cũ |
| Supabase | Auth → URL Configuration (Site URL + Redirect URLs) · SQL Editor chạy `doi-ten-mien-cloudblue.sql` (settings.data.web + đổi địa chỉ Database Webhooks) |
| SePay webhook | `https://cloudblue.vn/api/sepay-webhook` |
| Bot Telegram | `setWebhook?url=https://cloudblue.vn/api/telegram&secret_token=…` |
| Vercel env | `SITE_URL=https://cloudblue.vn` (nếu có biến này) |

- App PC + app Android (từ v1.37) đọc tên miền web ở Supabase `settings.data.web` → lần sau đổi tên miền chỉ cần sửa dòng đó, không phải cập nhật app.
  App Android tự cập nhật: web hỏng thì lấy `app.json` + APK thẳng trên GitHub.
- Đường điều khiển chính (IP:8189) không phụ thuộc tên miền; tên miền chỉ cho web, nút công cụ dự phòng, báo Telegram.
- `vercel.json`: khi `blueroblox.shop` mở lại sẽ tự chuyển sang `cloudblue.vn` (trừ `/api/…` để webhook cũ vẫn chạy).

## App máy chủ dạng .exe (không cần cài Python) — tuỳ chọn
GitHub → Actions → **Build server exe** → Run workflow → tải `BlueRobloxServer.zip` ở Artifacts (chỉ admin).
Giải nén vào `C:\blue\BlueRobloxServer\` → chạy `BlueRobloxServer.exe` (các file .py vẫn ở `C:\blue`, sửa .py không cần build lại).

## Lỗi hay gặp
| Hiện tượng | Cách sửa |
|---|---|
| App báo "Máy chủ chưa mở đường phát hình" | Chưa chạy SQL v20, hoặc dịch vụ phat-hinh chưa chạy (đèn Phát hình app đỏ) |
| App chỉ nối được "Cloudflare", không "trực tiếp" | Modem chưa mở cổng TCP 8189 về máy chủ |
| Đen màn, tab phat-hinh báo "Không lấy được hình từ cửa sổ máy" | Cửa sổ giả lập bị thu nhỏ — bấm 🙈 Ẩn cửa sổ giả lập (ẩn đúng cách, không thu nhỏ) |
| Chạm không ăn, tab phat-hinh báo "scrcpy lỗi → dùng lệnh input" | Vẫn dùng được (chậm hơn); gửi dòng lỗi cho hỗ trợ |
| Đơn Android 12/7 hoàn tiền "không máy chủ nào nhận" | Chưa có máy chủ MuMu đang chạy |
