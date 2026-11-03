# =====================================================================
#  setup.ps1 — Cài BlueRoblox Cloud Phone lên MÁY WINDOWS (MEmu) — thay hẳn server Ubuntu
#  Chạy trong PowerShell (Run as administrator):
#     Set-ExecutionPolicy -Scope Process Bypass -Force; & C:\blue\setup.ps1
#  Làm: Node.js · adb (Google) · cloudflared · ws-scrcpy (trình phát hình) · tải phonectl/cloudphoned/cpapi · tự chạy khi bật máy
# =====================================================================
$ErrorActionPreference = "Stop"
$B = "C:\blue"
$RAW = "https://raw.githubusercontent.com/lehien205px/blueroblox/main"   # thêm ?t=… để luôn lấy bản mới nhất (không bị giới hạn lượt như api.github.com)
function Ok($m)  { Write-Host "  [OK] $m" -ForegroundColor Green }
function Step($m){ Write-Host "`n== $m ==" -ForegroundColor Cyan }
function Get($url, $out) {
  # File của BlueRoblox: lấy qua api.github.com TRƯỚC (luôn là bản mới nhất — raw.githubusercontent hay trả bản cũ vài phút),
  # api hết lượt (60 lần/giờ) thì dùng raw.githubusercontent
  if ($url -like "https://raw.githubusercontent.com/lehien205px/blueroblox/main/*") {
    $api = $url -replace "https://raw.githubusercontent.com/lehien205px/blueroblox/main", "https://api.github.com/repos/lehien205px/blueroblox/contents"
    & curl.exe -fsSL -H "Accept: application/vnd.github.raw" -H "User-Agent: BlueRoblox-setup" -o $out $api
    if ($LASTEXITCODE -eq 0) { return }
    $url = $url + "?t=" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
  & curl.exe -fsSL -H "Cache-Control: no-cache" -o $out $url
  if ($LASTEXITCODE -ne 0) { throw "Tải lỗi: $url" } }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host "Hay mo PowerShell bang Run as administrator." -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path $B, "$B\redroid", "$B\wstmp", "$B\apk-cai-san", "$B\logs" | Out-Null
Set-Location $B
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

Step "1/7 Python"
$PY = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $PY)) { winget install -e --id Python.Python.3.12 --scope machine --accept-source-agreements --accept-package-agreements | Out-Null }
if (-not (Test-Path $PY)) { throw "Chua co Python 3.12" }; Ok $PY

Step "2/7 Node.js (de chay trinh phat hinh ws-scrcpy)"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  winget install -e --id OpenJS.NodeJS.LTS --scope machine --accept-source-agreements --accept-package-agreements | Out-Null
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
$NODE = (Get-Command node).Source; Ok "node $(node --version)"

Step "3/7 adb (Google platform-tools)"
if (-not (Test-Path "$B\platform-tools\adb.exe")) {
  Get "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" "$B\pt.zip"
  Expand-Archive -Force "$B\pt.zip" $B; Remove-Item "$B\pt.zip" }
Ok "$B\platform-tools\adb.exe"
# MEmu kèm adb.exe cũ → 2 bản adb tranh nhau làm rớt máy. Thay bằng bản của Google (giữ bản cũ .bak)
$MEMU = @("C:\Program Files\Microvirt\MEmu", "D:\Program Files\Microvirt\MEmu", "C:\Microvirt\MEmu", "D:\Microvirt\MEmu") | Where-Object { Test-Path "$_\memuc.exe" } | Select-Object -First 1
if (-not $MEMU) { throw "Khong thay MEmu (memuc.exe). Cai MEmu Play truoc." }
Get-Process adb -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
foreach ($f in "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll") {
  if ((Test-Path "$MEMU\$f") -and -not (Test-Path "$MEMU\$f.bak")) { Copy-Item "$MEMU\$f" "$MEMU\$f.bak" }
  Copy-Item -Force "$B\platform-tools\$f" "$MEMU\$f" }
Ok "MEmu: $MEMU (da dung chung adb cua Google)"

Step "4/7 cloudflared (duong ham phone.blueroblox.shop)"
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue) -and -not (Test-Path "C:\Program Files (x86)\cloudflared\cloudflared.exe")) {
  winget install -e --id Cloudflare.cloudflared --scope machine --accept-source-agreements --accept-package-agreements | Out-Null }
Ok "cloudflared da cai"

Step "5/7 ws-scrcpy (trinh phat hinh may len web) — 3-8 phut"
if (-not (Test-Path "$B\ws-scrcpy\dist\index.js")) {
  Get "https://codeload.github.com/NetrisTV/ws-scrcpy/zip/refs/heads/master" "$B\ws.zip"
  if (Test-Path "$B\ws-src") { Remove-Item -Recurse -Force "$B\ws-src" }
  Expand-Archive -Force "$B\ws.zip" "$B\ws-src"; Remove-Item "$B\ws.zip"
  $src = (Get-ChildItem "$B\ws-src" -Directory | Select-Object -First 1).FullName
  Push-Location $src
  # bỏ các phần cần biên dịch C++ (shell, iOS) — không dùng tới
  & node -e "const f='package.json',d=JSON.parse(require('fs').readFileSync(f));delete d.optionalDependencies;delete d.scripts.postinstall;['node-pty','ios-device-lib','node-mjpeg-proxy'].forEach(k=>delete d.dependencies[k]);require('fs').writeFileSync(f,JSON.stringify(d,null,2))"
  # chỉ dịch TypeScript, bỏ kiểm tra kiểu (các file iOS/shell không dùng tới làm báo lỗi)
  (Get-Content webpack\ws-scrcpy.common.ts -Raw) -replace "\{ loader: 'ts-loader' \}", "{ loader: 'ts-loader', options: { transpileOnly: true } }" | Set-Content -Encoding utf8 webpack\ws-scrcpy.common.ts
  Set-Content -Encoding ascii build.config.override.json '{"INCLUDE_ADB_SHELL": false, "INCLUDE_APPL": false, "USE_QVH_SERVER": false, "USE_WDA_MJPEG_SERVER": false}'
  if (Test-Path package-lock.json) { Remove-Item package-lock.json }
  & npm.cmd install --ignore-scripts --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw "npm install loi" }
  & npm.cmd run dist; if ($LASTEXITCODE -ne 0) { throw "build ws-scrcpy loi" }
  Pop-Location
  if (Test-Path "$B\ws-scrcpy") { Remove-Item -Recurse -Force "$B\ws-scrcpy" }
  New-Item -ItemType Directory -Force "$B\ws-scrcpy" | Out-Null
  Copy-Item -Recurse "$src\dist" "$B\ws-scrcpy\dist"
  Push-Location "$B\ws-scrcpy\dist"; & npm.cmd install --omit=dev --ignore-scripts --no-audit --no-fund; Pop-Location
  Remove-Item -Recurse -Force "$B\ws-src" }
Ok "$B\ws-scrcpy\dist"
# chặn máy khác trong mạng vào thẳng cổng 8000 (chỉ đi qua cpapi 8010 + Cloudflare)
netsh advfirewall firewall delete rule name="BlueRoblox chan 8000" | Out-Null
netsh advfirewall firewall add rule name="BlueRoblox chan 8000" dir=in action=block protocol=TCP localport=8000,8010 remoteip=localsubnet | Out-Null

Step "6/7 Tai chuong trinh BlueRoblox"
foreach ($f in "cloudphoned.py", "cpapi.py") { Get "$RAW/$f" "$B\$f" }
foreach ($f in "phonectl.py", "run.py", "remake.py", "keep.bat", "start-blue.bat", "panel.py", "wallpaper.png") { Get "$RAW/windows/$f" "$B\$f" }
if (-not (Test-Path "$B\cloudphone.conf")) {
@"
SUPABASE_URL=https://nprftgodswlpzomcttal.supabase.co
SUPABASE_SERVICE_KEY=DAN_SERVICE_ROLE_KEY_VAO_DAY
PUBLIC_BASE=https://phone.blueroblox.shop
NODE_ID=win1
PRIMARY=1
LIMIT_PCT=90
RAM_OVER=1
CPU_OVER=2
WIN_RAM_RESERVE=4
"@ | Set-Content -Encoding utf8 "$B\cloudphone.conf" }
Ok "$B\cloudphone.conf"

Step "7/7 Tu chay khi bat may"
$lnk = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\BlueRoblox.lnk"
$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($lnk)
$PYW = "C:\Program Files\Python312\pythonw.exe"
if (Get-ScheduledTask -TaskName "BlueRoblox" -ErrorAction SilentlyContinue) { Remove-Item $lnk -ErrorAction SilentlyContinue }   # đã có tự chạy quyền Admin (nút 🔑)
else { $s.TargetPath = $PYW; $s.Arguments = "`"$B\panel.py`" --auto"; $s.WorkingDirectory = $B; $s.Save() }
$d = $ws.CreateShortcut("$env:Public\Desktop\BlueRoblox.lnk"); $d.TargetPath = $PYW; $d.Arguments = "`"$B\panel.py`""; $d.WorkingDirectory = $B; $d.Save()
Ok "Bat may Windows -> tu chay BlueRoblox"
Set-Location $B
Write-Host "`nXONG phan cai dat. Mo bieu tuong BlueRoblox tren man hinh Desktop de dieu khien." -ForegroundColor Yellow
