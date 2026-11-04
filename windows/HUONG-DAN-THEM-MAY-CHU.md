# Hướng dẫn thêm MÁY CHỦ mới (máy 2, 3, 4…) cho BlueRoblox Cloud Phone

Mỗi máy Windows là **một máy chủ độc lập**: tự chạy máy ảo MEmu của khách, tự nối internet qua đường hầm Cloudflare riêng.
Các máy **không nối với nhau** — cùng đọc đơn hàng trên Supabase. Có đơn mới: **máy nào còn trống nhiều nhất nhận**;
máy đầy thì nhường máy khác; tất cả đầy thì web báo hết chỗ. Máy khách đã tạo nằm yên ở máy đã tạo ra nó.

| Máy | NODE | Tên miền | Đường hầm Cloudflare |
|---|---|---|---|
| Máy 1 (máy chính) | `win1` | `phone.blueroblox.shop` | `phone` |
| Máy 2 | `win2` | `phone2.blueroblox.shop` | `phone2` |
| Máy 3 | `win3` | `phone3.blueroblox.shop` | `phone3` |
| Máy 4 | `win4` | `phone4.blueroblox.shop` | `phone4` |

> ⚠️ Không gửi khoá Supabase / mã Cloudflare (`eyJ…`) vào chat hay lên GitHub — chỉ dán vào bảng BlueRoblox trên máy.

---

## Bước 0 — Chuẩn bị máy mới
1. Bật **ảo hoá (VT-x / Intel Virtualization)** trong BIOS.
2. Cài **MEmu Play** bản chính thức từ memuplay.com (nên cùng bản với máy 1 — hiện 9.5.8).
3. **Cắm dây mạng (LAN)**, không dùng wifi. **Không cần** vào router 192.168.1.1, không cần mở cổng.

## Bước 1 — Tạo đường hầm Cloudflare (trên trình duyệt)
1. Vào https://one.dash.cloudflare.com/ → **Networks → Tunnels** → **Create a tunnel** → **Cloudflared**.
2. Đặt tên theo bảng trên (máy 2: `phone2`) → **Save tunnel**.
3. Chọn **Windows** → bấm nút **copy** ở dòng `cloudflared.exe service install eyJ…` → giữ lại (dán ở Bước 3).
   Không cần tải file .msi hay mở Command Prompt như trang hướng dẫn — tool tự cài.
4. **Next** → **Public Hostname**: Subdomain `phone2` · Domain `blueroblox.shop` · Type **HTTP** · URL `localhost:8010` → **Save**.
   (URL luôn là `localhost:8010`, KHÔNG ghi IP.)
5. Đường hầm cũ `phone` của máy 1: **không sửa**.

## Bước 2 — Cài tool trên máy mới
Bấm chuột phải nút **Start** → **Windows PowerShell (Admin)** (dấu nhắc phải có chữ **PS** — nếu là `C:\Windows\system32>` thì gõ `powershell` rồi Enter).
Dán lệnh (máy 2 — máy 3/4 đổi `win2`/`phone2` thành `win3`/`phone3`, `win4`/`phone4`):

```
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; New-Item -ItemType Directory -Force C:\blue | Out-Null; Invoke-WebRequest -UseBasicParsing -UserAgent "BlueRoblox" -Headers @{Accept = "application/vnd.github.raw"} -Uri "https://api.github.com/repos/lehien205px/blueroblox/contents/windows/setup.ps1" -OutFile C:\blue\setup.ps1; Set-ExecutionPolicy -Scope Process Bypass -Force; & C:\blue\setup.ps1 -Node win2 -PubHost phone2.blueroblox.shop
```

Mất 10–15 phút. Tự cài: curl (nếu Windows cũ chưa có), Python 3.12, Node.js, adb, cloudflared, trình phát hình ws-scrcpy, toàn bộ tool BlueRoblox,
file cài đặt `C:\blue\cloudphone.conf` đã tối ưu sẵn. Dòng cuối phải hiện `NODE_ID=win2  PUBLIC_BASE=https://phone2.blueroblox.shop`.

## Bước 3 — Mở bảng BlueRoblox (biểu tượng trên Desktop máy mới) và bấm lần lượt
1. **②** Khoá Supabase — lấy tại https://supabase.com/dashboard/project/nprftgodswlpzomcttal/settings/api-keys
   → khoá **service_role / secret** (`eyJ…` hoặc `sb_secret_…`) → Reveal → Copy → dán. (Cùng khoá với máy 1.)
2. **③** Dán lệnh Cloudflare `eyJ…` đã copy ở Bước 1 → trang Cloudflare đổi thành **Connected**.
3. **🔑** Tự đăng nhập + tự chạy (nhập mật khẩu Windows vào hộp thoại — chỉ lưu trong máy).
4. **🚀** Tự động A→Z → chờ đủ đèn xanh.

## Bước 4 — Kiểm tra
- Web → **Bảng quản trị → Máy chủ**: thấy máy mới (`win2`…) đang chạy.
- Trên máy mới: `& "C:\Program Files\Python312\python.exe" C:\blue\kiemtra.py` → không có MÁY THỪA / MẤT MÁY THẬT.

---

## Cài đặt sẵn trên mỗi máy (trong `C:\blue\cloudphone.conf`)
| Dòng | Ý nghĩa |
|---|---|
| `MEMU_RENDER=1` | **DirectX — MƯỢT.** (OpenGL `0` thấy cảnh 3D trên web nhưng máy vẽ chậm gấp nhiều lần — ĐỪNG đổi) |
| `MEMU_MAX_RAM=16` `MEMU_MAX_CPU=8` | Giới hạn MEmu mỗi máy ảo (gói lớn hơn vẫn nhận, chạy 16GB/8 nhân) |
| `WIN_VCPU_OVER=1` | Tổng nhân ảo ≤ số luồng thật (lag thì đổi `0.9` = chừa 10%) |
| `WIN_RAM_RESERVE=3` `RAM_BUSY_PCT=98` | Nhận máy tới khi RAM thật gần đầy, chừa 3GB cho Windows |
| `POOL_SIZE=1` | Giữ sẵn 1 máy dựng trước (đang tắt) → khách mua có máy ~1 phút |
| `NODE_ID` `PUBLIC_BASE` `PRIMARY` | Tên máy chủ / tên miền / máy chính (chỉ máy 1 là `PRIMARY=1`) |

Sửa conf xong → khởi động lại phần nhận đơn:
```
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run.py cloudphoned.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## Lệnh hay dùng (chạy trên máy chủ cần làm, PowerShell Admin, trong `C:\blue`)
| Việc | Lệnh |
|---|---|
| Xem máy (MEmu ↔ danh sách ↔ web, tổng nhân) | `& "C:\Program Files\Python312\python.exe" kiemtra.py` |
| Tối ưu lại mọi máy đang chạy | `& "C:\Program Files\Python312\python.exe" phonectl.py tuneall` |
| Xoá máy thừa (không thuộc khách) | `& "C:\Program Files\Python312\python.exe" phonectl.py cleanup` |
| Làm mới máy khách bị mất máy thật | `& "C:\Program Files\Python312\python.exe" run.py remake.py list` rồi `… remake.py all` |
| Dọn đơn kẹt trên web (chỉ xem / làm thật) | `… donweb.py` / `… donweb.py --yes` |
| Đổi chế độ hình 1 máy | `… phonectl.py render TÊN_MÁY directx` |

Mỗi máy chủ chỉ làm mới / dọn **máy của chính nó** — không đụng máy chủ khác.

## Cập nhật tool (sau khi tải file mới lên GitHub)
Chạy trên **từng** máy chủ:
```
foreach ($f in "phonectl.py","panel.py","remake.py","donweb.py","kiemtra.py","setup.ps1","suachua.ps1") { curl.exe -fsSL -H "Accept: application/vnd.github.raw" -o "C:\blue\$f" "https://api.github.com/repos/lehien205px/blueroblox/contents/windows/$f" }
foreach ($f in "cloudphoned.py","cpapi.py") { curl.exe -fsSL -H "Accept: application/vnd.github.raw" -o "C:\blue\$f" "https://api.github.com/repos/lehien205px/blueroblox/contents/$f" }
Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run.py cloudphoned.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
Không cập nhật khi đang có máy "Đang tạo…".

## Lưu ý
- **Không tắt máy chủ đang có khách** — khách ở máy đó mất máy tới khi bật lại (bật lại tự đăng nhập → tự bật máy khách, 10–20 phút).
- Các máy cùng 1 đường mạng **chia nhau upload** (hiện 30 Mbps ≈ 15–20 khách xem cùng lúc). Đông khách → nâng gói mạng / đặt máy ở mạng khác.
- App điện thoại hiện lấy ảnh màn hình nhỏ từ máy 1 → máy ảo ở máy 2+ không có ảnh nhỏ trong app (vẫn điều khiển được). Cần build lại app để sửa.

## Lỗi thường gặp
| Báo | Cách xử lý |
|---|---|
| `'New-Item' is not recognized` | Đang ở cmd → gõ `powershell` rồi dán lại |
| `curl.exe is not recognized` | Windows cũ → dùng đúng lệnh `Invoke-WebRequest` ở Bước 2 (setup tự cài curl) |
| `Khong thay MEmu (memuc.exe)` | Cài MEmu trước rồi chạy lại lệnh Bước 2 |
| MEmu `connect console failed` / máy không tạo được | Khởi động lại Windows |
| Lag cả máy | `kiemtra.py` xem tổng nhân (> số luồng = quá tải) · máy thừa → `cleanup` · chế độ hình phải DirectX |
