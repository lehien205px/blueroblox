@echo off
rem ======================================================================
rem  Đóng gói app PC cho khách: dist\BlueRobloxPC\BlueRobloxPC.exe + dist\BlueRobloxPC.zip
rem  Cần Python 3.12 (python.org). Chạy: bấm đúp build.bat
rem ======================================================================
cd /d %~dp0
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install --upgrade pyinstaller av pygame-ce numpy pillow
py -3.12 -m PyInstaller --noconfirm --clean --windowed --name BlueRobloxPC --collect-submodules av --icon icon.png --add-data "icon.png;." bluepc.py
if errorlevel 1 goto :err
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\BlueRobloxPC -DestinationPath dist\BlueRobloxPC.zip"
echo.
echo XONG: dist\BlueRobloxPC\BlueRobloxPC.exe  va  dist\BlueRobloxPC.zip
pause
exit /b 0
:err
echo LOI khi dong goi - chup man hinh gui ho tro
pause
exit /b 1
