# =====================================================================
#  thu-webrtc.ps1 — BẢN THỬ phát hình kiểu mới (KHÔNG đụng hệ thống đang chạy)
#   Quay thẳng CỬA SỔ LDPlayer trên Windows → nén bằng card NVIDIA (NVENC, siêu ít trễ) → phát WebRTC
#   Xem trên điện thoại/máy tính CÙNG MẠNG NHÀ:  http://<IP máy chủ>:8889/thu
#  Chạy (PowerShell Admin):  Set-ExecutionPolicy -Scope Process Bypass -Force; & C:\blue\thu-webrtc.ps1 [-May TÊN_MÁY] [-Cpu]
#   -May  : tên máy LDPlayer muốn quay (mặc định: cửa sổ LDPlayer đầu tiên đang mở)
#   -Cpu  : nén bằng CPU (x264) thay cho card — để so sánh
#  Dừng: bấm Ctrl+C (tự tắt máy chủ WebRTC)
# =====================================================================
param([string]$May = "", [switch]$Cpu)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$R = "C:\blue\rtc"; New-Item -ItemType Directory -Force $R | Out-Null
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }

# 1) FFmpeg (bản chính thức BtbN trên GitHub) + MediaMTX (máy chủ WebRTC mã nguồn mở, GitHub bluenviron)
if (-not (Test-Path "$R\ffmpeg.exe")) {
  Write-Host "Tai FFmpeg (~100MB)..."
  curl.exe -fsSL -o "$R\ff.zip" "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
  Expand-Archive -Force "$R\ff.zip" "$R\fftmp"
  Copy-Item -Force (Get-ChildItem "$R\fftmp" -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName "$R\ffmpeg.exe"
  Remove-Item -Recurse -Force "$R\ff.zip", "$R\fftmp" }
Ok "ffmpeg"
if (-not (Test-Path "$R\mediamtx.exe")) {
  Write-Host "Tai MediaMTX..."
  $rel = Invoke-RestMethod -UseBasicParsing -UserAgent "BlueRoblox" "https://api.github.com/repos/bluenviron/mediamtx/releases/latest"
  $u = ($rel.assets | Where-Object { $_.name -like "*windows_amd64.zip" } | Select-Object -First 1).browser_download_url
  curl.exe -fsSL -o "$R\mtx.zip" $u
  Expand-Archive -Force "$R\mtx.zip" $R; Remove-Item "$R\mtx.zip" }
Ok "mediamtx"

# 2) Mở cổng trong mạng nhà (8889 trang xem, 8189 UDP dữ liệu hình)
netsh advfirewall firewall delete rule name="BlueRoblox thu WebRTC" | Out-Null
netsh advfirewall firewall add rule name="BlueRoblox thu WebRTC" dir=in action=allow protocol=TCP localport=8889 remoteip=localsubnet | Out-Null
netsh advfirewall firewall add rule name="BlueRoblox thu WebRTC" dir=in action=allow protocol=UDP localport=8189 remoteip=localsubnet | Out-Null
Ok "mo cong 8889/8189 trong mang nha"

# 3) Tìm cửa sổ LDPlayer + vị trí thật trên màn hình (điểm ảnh thật, không bị phóng to Windows làm lệch)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  public struct RECT { public int L, T, R, B; } }
"@
[W]::SetProcessDPIAware() | Out-Null
$ps = Get-Process dnplayer -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
if ($May) { $ps = $ps | Where-Object { $_.MainWindowTitle -like "*$May*" } }
$p = $ps | Select-Object -First 1
if (-not $p) { throw "Khong thay cua so LDPlayer dang mo. Bat may LDPlayer (cua so hien tren man hinh) roi chay lai." }
$h = $p.MainWindowHandle
if ([W]::IsIconic($h)) { [W]::ShowWindow($h, 9) | Out-Null; Start-Sleep 1 }
[W]::SetWindowPos($h, [IntPtr]::Zero, 0, 0, 0, 0, 0x0001 -bor 0x0004) | Out-Null; Start-Sleep 1   # đưa về góc trên-trái màn hình (giữ cỡ)
$r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
$x = [Math]::Max(0, $r.L); $y = [Math]::Max(0, $r.T); $w = ($r.R - $x); $hh = ($r.B - $y); $w -= $w % 2; $hh -= $hh % 2
Ok "cua so '$($p.MainWindowTitle)' tai ($x,$y) co ${w}x${hh}  — DUNG thu nho / che cua so nay khi dang thu"

# 4) Bật máy chủ WebRTC + bắt đầu quay & nén
Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
$mtx = Start-Process -PassThru -WindowStyle Hidden -FilePath "$R\mediamtx.exe" -WorkingDirectory $R
Start-Sleep 2
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } | Select-Object -First 1).IPAddress
Write-Host "`n===== MO TREN DIEN THOAI / MAY TINH CUNG MANG NHA:  http://${ip}:8889/thu  =====" -ForegroundColor Yellow
Write-Host "(Dieu khien van bam tren cua so LDPlayer / trang web cu — ban thu nay chi so DO TRE HINH)`n" -ForegroundColor Yellow
$src = "ddagrab=output_idx=0:framerate=60:offset_x=${x}:offset_y=${y}:video_size=${w}x${hh}:draw_mouse=0"
if ($Cpu) { $enc = @("-vf", "hwdownload,format=bgra,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline") }
else      { $enc = @("-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-zerolatency", "1", "-rc", "cbr", "-profile:v", "baseline") }
try {
  & "$R\ffmpeg.exe" -hide_banner -loglevel warning -f lavfi -i $src @enc -bf 0 -g 120 -b:v 4M -maxrate 4M -bufsize 1M -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/thu"
} finally { Stop-Process -Id $mtx.Id -Force -ErrorAction SilentlyContinue; Write-Host "Da tat ban thu." }
