# =====================================================================
#  setup.ps1 — Cài BlueRoblox Cloud Phone lên MÁY WINDOWS (MEmu) — thay hẳn server Ubuntu
#  Chạy trong PowerShell (Run as administrator):
#     Set-ExecutionPolicy -Scope Process Bypass -Force; & C:\blue\setup.ps1
#  Làm: Node.js · adb (Google) · cloudflared · ws-scrcpy (trình phát hình) · tải phonectl/cloudphoned/cpapi · tự chạy khi bật máy
#  MÁY CHỦ THỨ 2, 3…:  & C:\blue\setup.ps1 -Node win2 -PubHost phone2.blueroblox.shop
# =====================================================================
param([string]$Node = "win1", [string]$PubHost = "phone.blueroblox.shop")
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12   # Windows cũ mặc định TLS 1.0 → GitHub/Google từ chối
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
function Dl($url, $out) { Invoke-WebRequest -UseBasicParsing -UserAgent "BlueRoblox-setup" -Uri $url -OutFile $out }
function RefreshPath { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
# DNS Cloudflare + Google (IPv4 + IPv6): DNS của modem/nhà mạng hay nhớ "không có tên" → tên miền phoneN.blueroblox.shop mới tạo không vào được
try { Get-NetAdapter | Where-Object Status -eq Up | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ("1.1.1.1","8.8.8.8","2606:4700:4700::1111","2001:4860:4860::8888") }; ipconfig /flushdns | Out-Null } catch {}
$WG = [bool](Get-Command winget -ErrorAction SilentlyContinue)   # Windows 10 bản cũ / Server không có winget → tải thẳng bộ cài chính thức

# Windows 10 bản cũ (trước 1803) không có sẵn curl.exe → tải curl chính thức (curl.se) vào C:\blue\bin + thêm vào PATH
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
  Step "0/7 curl (Windows nay chua co)"
  New-Item -ItemType Directory -Force "$B\bin" | Out-Null
  Dl "https://curl.se/windows/latest.cgi?p=win64-mingw.zip" "$B\curl.zip"
  if (Test-Path "$B\curltmp") { Remove-Item -Recurse -Force "$B\curltmp" }
  Expand-Archive -Force "$B\curl.zip" "$B\curltmp"
  $cb = (Get-ChildItem "$B\curltmp" -Recurse -Filter curl.exe | Select-Object -First 1).Directory.FullName
  Copy-Item -Force "$cb\*" "$B\bin\"
  Remove-Item -Recurse -Force "$B\curltmp", "$B\curl.zip"
  $mp = [Environment]::GetEnvironmentVariable("Path", "Machine")
  if ($mp -notlike "*$B\bin*") { [Environment]::SetEnvironmentVariable("Path", "$mp;$B\bin", "Machine") }
  RefreshPath; Ok "curl da cai vao C:\blue\bin" }

Step "1/7 Python"
$PY = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $PY)) {
  if ($WG) { winget install -e --id Python.Python.3.12 --scope machine --accept-source-agreements --accept-package-agreements | Out-Null }
  else { Get "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" "$B\py-setup.exe"
         Start-Process -Wait "$B\py-setup.exe" -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 TargetDir="C:\Program Files\Python312"'
         Remove-Item "$B\py-setup.exe" } }
if (-not (Test-Path $PY)) { throw "Chua co Python 3.12" }; Ok $PY

Step "2/7 Node.js (de chay trinh phat hinh ws-scrcpy)"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  if ($WG) { winget install -e --id OpenJS.NodeJS.LTS --scope machine --accept-source-agreements --accept-package-agreements | Out-Null }
  else { Get "https://nodejs.org/dist/v20.18.0/node-v20.18.0-x64.msi" "$B\node.msi"; Start-Process -Wait msiexec.exe -ArgumentList "/i `"$B\node.msi`" /qn /norestart"; Remove-Item "$B\node.msi" }
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
$NODEEXE = (Get-Command node).Source; Ok "node $(node --version)"   # KHÔNG đặt tên $NODE — PowerShell không phân biệt hoa/thường, sẽ ghi đè tham số -Node

Step "3/7 adb (Google platform-tools)"
if (-not (Test-Path "$B\platform-tools\adb.exe")) {
  Get "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" "$B\pt.zip"
  Expand-Archive -Force "$B\pt.zip" $B; Remove-Item "$B\pt.zip" }
Ok "$B\platform-tools\adb.exe"
# Giả lập: LDPlayer 9 (ưu tiên) hoặc MEmu. Chúng kèm adb.exe cũ → 2 bản adb tranh nhau làm rớt máy → thay bằng bản của Google (giữ bản cũ .bak)
$LDP = @("C:\LDPlayer\LDPlayer9", "D:\LDPlayer\LDPlayer9", "C:\Program Files\LDPlayer\LDPlayer9", "C:\leidian\LDPlayer9") | Where-Object { Test-Path "$_\ldconsole.exe" } | Select-Object -First 1
$MEMU = @("C:\Program Files\Microvirt\MEmu", "D:\Program Files\Microvirt\MEmu", "C:\Microvirt\MEmu", "D:\Microvirt\MEmu") | Where-Object { Test-Path "$_\memuc.exe" } | Select-Object -First 1
if ($LDP) { $MEMU = $LDP }
if (-not $MEMU) { throw "Khong thay gia lap. Cai LDPlayer 9 (ldplayer.net) truoc." }
[Environment]::SetEnvironmentVariable("ADB_LOCAL_TRANSPORT_MAX_PORT", "5553", "Machine"); $env:ADB_LOCAL_TRANSPORT_MAX_PORT = "5553"   # adb không tự dò emulator-55xx (LDPlayer hiện mỗi máy 2 lần)
Get-Process adb -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
foreach ($f in "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll") {
  if ((Test-Path "$MEMU\$f") -and -not (Test-Path "$MEMU\$f.bak")) { Copy-Item "$MEMU\$f" "$MEMU\$f.bak" }
  Copy-Item -Force "$B\platform-tools\$f" "$MEMU\$f" }
Ok "Gia lap: $MEMU (da dung chung adb cua Google)"

Step "4/7 cloudflared (duong ham phone.blueroblox.shop)"
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue) -and -not (Test-Path "C:\Program Files (x86)\cloudflared\cloudflared.exe")) {
  if ($WG) { winget install -e --id Cloudflare.cloudflared --scope machine --accept-source-agreements --accept-package-agreements | Out-Null }
  else { Get "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.msi" "$B\cf.msi"; Start-Process -Wait msiexec.exe -ArgumentList "/i `"$B\cf.msi`" /qn /norestart"; Remove-Item "$B\cf.msi" } }
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
# Trình phát hình trên trình duyệt mặc định GIỮ tới 100ms khung hình chờ vẽ (maxFps/10 khung) → hình chạy theo sau tay.
# Sửa: chỉ giữ khung MỚI NHẤT (vẽ ngay khung mới, bỏ khung cũ) → bớt tới ~100ms trễ.
Get-ChildItem "$B\ws-scrcpy\dist" -Recurse -Filter *.js -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\node_modules\\' } | ForEach-Object {
  $t = [IO.File]::ReadAllText($_.FullName)
  if ($t -match 'videoSettings\.maxFps\s*/\s*10') { [IO.File]::WriteAllText($_.FullName, ($t -replace 'videoSettings\.maxFps\s*/\s*10', 'videoSettings.maxFps*0')); Ok "it tre: $($_.Name)" } }   # giữ "this." phía trước → vẫn đúng cú pháp
# chặn máy khác trong mạng vào thẳng cổng 8000 (chỉ đi qua cpapi 8010 + Cloudflare)
netsh advfirewall firewall delete rule name="BlueRoblox chan 8000" | Out-Null
netsh advfirewall firewall add rule name="BlueRoblox chan 8000" dir=in action=block protocol=TCP localport=8000,8010 remoteip=localsubnet | Out-Null

Step "6/7 Tai chuong trinh BlueRoblox"
& $PY -m pip install --quiet --disable-pip-version-check pillow 2>$null | Out-Null   # ảnh màn hình nhỏ (màn hình trực tiếp trong danh sách máy)
foreach ($f in "cloudphoned.py", "cpapi.py") { Get "$RAW/$f" "$B\$f" }
foreach ($f in "phonectl.py", "run.py", "remake.py", "keep.bat", "start-blue.bat", "panel.py", "wallpaper.png", "donweb.py", "kiemtra.py", "suachua.ps1") { Get "$RAW/windows/$f" "$B\$f" }
if (-not (Test-Path "$B\cloudphone.conf")) {
$PRI = if ($Node -eq "win1") { "1" } else { "0" }
$MAXR = if ($LDP) { "8" } else { "16" }
@"
SUPABASE_URL=https://nprftgodswlpzomcttal.supabase.co
SUPABASE_SERVICE_KEY=DAN_SERVICE_ROLE_KEY_VAO_DAY
PUBLIC_BASE=https://$PubHost
NODE_ID=$Node
PRIMARY=$PRI
LIMIT_PCT=90
RAM_OVER=1
CPU_OVER=1
WIN_VCPU_OVER=1
WIN_RAM_RESERVE=3
RAM_BUSY_PCT=98
MEMU_MAX_RAM=$MAXR
MEMU_MAX_CPU=8
MEMU_RENDER=1
MEMU_GMEM_OPT=1
POOL_SIZE=1
"@ | Set-Content -Encoding utf8 "$B\cloudphone.conf" }
$nl = (Get-Content "$B\cloudphone.conf" | Where-Object { $_ -match '^(NODE_ID|PUBLIC_BASE)=' }) -join "  "
Ok "$B\cloudphone.conf  ($nl)"

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
