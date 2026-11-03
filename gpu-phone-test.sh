#!/usr/bin/env bash
# =====================================================================
#  gpu-phone-test.sh — THỬ NGHIỆM "Máy GPU": Android Emulator (Google) dùng card NVIDIA (GTX 1080)
#  Chạy SONG SONG với hệ thống redroid hiện tại, KHÔNG đụng tới máy của khách.
#
#   bash gpu-phone-test.sh install   # cài SDK + Android 11 (có Google Play)  ~10 phút, ~6GB
#   bash gpu-phone-test.sh xsetup    # màn hình ảo trên GTX 1080 (install tự gọi)
#   bash gpu-phone-test.sh start     # bật máy thử (4 nhân, 4GB, 720x1280)
#   bash gpu-phone-test.sh check     # xem máy có ĐANG DÙNG GTX 1080 không + CPU/RAM
#   bash gpu-phone-test.sh stop      # tắt máy thử
#   bash gpu-phone-test.sh remove    # xoá sạch máy thử + SDK
#  Xem màn hình: đăng nhập admin → https://phone.blueroblox.shop/ → thiết bị 127.0.0.1:5601
# =====================================================================
set -u
SDK="$HOME/android-sdk"
AVD=gputest
PORT=5600                     # cổng console; adb = PORT+1 = 5601 (chỉ trong server)
# Android 11 (API 30) Google Play: có sẵn bộ dịch ARM của Google (Roblox chạy được) + trình điều khiển web xem được
# (Android 13 bị lỗi hình trên ws-scrcpy). Muốn đổi: GPU_IMG="system-images;android-33;google_apis_playstore;x86_64" bash ...
IMG="${GPU_IMG:-system-images;android-30;google_apis_playstore;x86_64}"
TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
LOG="$HOME/gpu-phone-test.log"
export ANDROID_SDK_ROOT="$SDK" ANDROID_HOME="$SDK" ANDROID_AVD_HOME="$HOME/.android/avd"
# adb riêng ở cổng 5038 — KHÔNG dùng chung adb (5037) của ws-scrcpy, tránh làm rớt hình máy khách
export ANDROID_ADB_SERVER_PORT=5038 QT_QPA_PLATFORM=offscreen
WS=wsscrcpy
XD=":9"                      # màn hình ảo chạy trên GTX 1080 (không cần màn hình thật) — emulator cần nó để dùng GPU
SM="$SDK/cmdline-tools/latest/bin/sdkmanager"; AM="$SDK/cmdline-tools/latest/bin/avdmanager"
EMU="$SDK/emulator/emulator"; ADB="$SDK/platform-tools/adb"
G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; N='\033[0m'
ok() { echo -e "${G}✓${N} $*"; }; warn() { echo -e "${Y}!${N} $*"; }; err() { echo -e "${R}✗${N} $*"; }
[ "$(id -u)" = "0" ] && { err "Chạy bằng user thường (không sudo):  bash $0 ${1:-}"; exit 1; }

case "${1:-}" in
install)
  echo "== Gói hệ thống (cần mật khẩu sudo) =="
  sudo apt-get update -qq
  AS=libasound2; apt-cache show libasound2t64 >/dev/null 2>&1 && AS=libasound2t64
  sudo apt-get install -y -qq openjdk-17-jre-headless unzip libpulse0 libnss3 libxcomposite1 libxcursor1 libxi6 libxtst6 \
       libxdamage1 libxkbfile1 libxrandr2 libegl1 libgl1 libgbm1 libdrm2 $AS >/dev/null && ok "Gói hệ thống"
  sudo usermod -aG kvm "$USER" && ok "User $USER dùng được KVM (máy ảo)"
  if [ ! -x "$SM" ]; then
    mkdir -p "$SDK/cmdline-tools" && curl -fsSL -o /tmp/cmdtools.zip "$TOOLS_URL" && unzip -qo /tmp/cmdtools.zip -d "$SDK/cmdline-tools" \
      && rm -rf "$SDK/cmdline-tools/latest" && mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest" && ok "Android cmdline-tools" \
      || { err "Không tải được cmdline-tools"; exit 1; }
  fi
  bash "$0" image || exit 1
  bash "$0" xsetup
  echo; warn "THOÁT SSH RỒI VÀO LẠI (để nhận quyền KVM), sau đó chạy:  bash $0 start" ;;

image)
  # tải bản Android ($GPU_IMG) + tạo lại máy thử — KHÔNG cần sudo (trang admin gọi được)
  [ -x "$SM" ] || { err "Chưa cài. Chạy:  bash $0 install"; exit 1; }
  yes | "$SM" --licenses >/dev/null 2>&1
  echo "== Tải emulator + $IMG (vài phút) =="
  "$SM" "emulator" "platform-tools" "$IMG" >/dev/null && ok "emulator, platform-tools, $IMG" || { err "Tải SDK lỗi"; exit 1; }
  echo no | "$AM" create avd -n "$AVD" -k "$IMG" -d pixel_4 --force >/dev/null && ok "Tạo máy thử '$AVD'"
  CFG="$ANDROID_AVD_HOME/$AVD.avd/config.ini"
  set_cfg() { grep -q "^$1=" "$CFG" && sed -i "s|^$1=.*|$1=$2|" "$CFG" || echo "$1=$2" >> "$CFG"; }
  set_cfg hw.ramSize 4096; set_cfg hw.cpu.ncore 4; set_cfg hw.gpu.enabled yes; set_cfg hw.gpu.mode host
  set_cfg hw.lcd.width 720; set_cfg hw.lcd.height 1280; set_cfg hw.lcd.density 320
  set_cfg disk.dataPartition.size 16G; set_cfg hw.keyboard yes; set_cfg fastboot.forceColdBoot yes
  set_cfg hw.audioInput no; set_cfg hw.audioOutput no; set_cfg showDeviceFrame no
  ok "Cấu hình: 4 nhân · 4GB · 720x1280 · GPU host (GTX 1080)" ;;

xsetup)
  echo "== Màn hình ảo $XD trên card NVIDIA (cần sudo) =="
  BUS=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | head -1)          # 00000000:02:00.0
  IFS=':.' read -r _ b d f <<< "$BUS"; B="PCI:$((16#$b)):$((16#$d)):$((16#$f))"      # -> PCI:2:0:0
  [ -n "$BUS" ] || { err "Không đọc được card NVIDIA (nvidia-smi)"; exit 1; }
  V=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | cut -d. -f1)
  sudo apt-get install -y -qq xserver-xorg-core >/dev/null && ok "Xorg"
  # thư viện OpenGL/EGL/Vulkan của NVIDIA (máy chủ cài driver kiểu "headless" thường thiếu) — đúng phiên bản driver đang chạy
  if ! ls /usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0 >/dev/null 2>&1; then
    PV=$(dpkg-query -W -f='${Version}' "nvidia-utils-$V" 2>/dev/null)
    sudo apt-get install -y -qq "libnvidia-gl-$V${PV:+=$PV}" >/dev/null && ok "Thư viện OpenGL NVIDIA ($V)" || warn "Không cài được libnvidia-gl-$V"
  fi
  if ! ls /usr/lib/xorg/modules/drivers/nvidia_drv.so /usr/lib/x86_64-linux-gnu/nvidia/xorg/nvidia_drv.so >/dev/null 2>&1; then
    sudo apt-get install -y -qq "xserver-xorg-video-nvidia-$V" >/dev/null 2>&1 && ok "Driver Xorg NVIDIA $V" || warn "Không cài được xserver-xorg-video-nvidia-$V"
  fi
  printf '%s\n' 'Section "ServerLayout"' '    Identifier "cloudgpu"' '    Screen 0 "Screen0"' 'EndSection' \
    'Section "Files"' '    ModulePath "/usr/lib/x86_64-linux-gnu/nvidia/xorg"' '    ModulePath "/usr/lib/xorg/modules"' 'EndSection' \
    'Section "Device"' '    Identifier "Device0"' '    Driver "nvidia"' "    BusID \"$B\"" \
    '    Option "AllowEmptyInitialConfiguration" "True"' '    Option "UseDisplayDevice" "None"' 'EndSection' \
    'Section "Screen"' '    Identifier "Screen0"' '    Device "Device0"' '    DefaultDepth 24' '    Option "AllowEmptyInitialConfiguration" "True"' \
    '    SubSection "Display"' '        Depth 24' '        Virtual 1920 1080' '    EndSubSection' 'EndSection' | sudo tee /etc/X11/xorg-cloudgpu.conf >/dev/null
  printf '%s\n' '[Unit]' 'Description=Man hinh ao tren GPU NVIDIA cho May GPU' 'After=systemd-user-sessions.service' '[Service]' \
    "ExecStart=/usr/lib/xorg/Xorg $XD -config /etc/X11/xorg-cloudgpu.conf -noreset -nolisten tcp -novtswitch -sharevts vt9" \
    'Restart=always' 'RestartSec=5' '[Install]' 'WantedBy=multi-user.target' | sudo tee /etc/systemd/system/cloudgpu-x.service >/dev/null
  sudo systemctl daemon-reload; sudo systemctl enable cloudgpu-x >/dev/null 2>&1; sudo systemctl restart cloudgpu-x; sleep 5
  if [ -S "/tmp/.X11-unix/X${XD#:}" ] && systemctl is-active -q cloudgpu-x; then ok "Màn hình ảo $XD đang chạy trên GTX 1080 ($B)"
  else err "Màn hình ảo chưa chạy. Gửi kết quả lệnh:  sudo journalctl -u cloudgpu-x -n 30 --no-pager; grep -E 'EE|WW' /var/log/Xorg.${XD#:}.log | tail -20"; exit 1; fi ;;

start)
  [ -x "$EMU" ] || { err "Chưa cài. Chạy:  bash $0 install"; exit 1; }
  [ -w /dev/kvm ] || { err "Chưa có quyền KVM — thoát SSH rồi vào lại"; exit 1; }
  EG="${EMU_GPU:-host}"     # host = card NVIDIA · swiftshader_indirect = vẽ bằng CPU (OpenGL ES 3, không cần màn hình ảo)
  [ "$EG" != host ] || [ -S "/tmp/.X11-unix/X${XD#:}" ] || bash "$0" xsetup || exit 1
  if pgrep -f -- "-avd $AVD" >/dev/null; then
    if [ "$("$ADB" -s emulator-$PORT shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then ok "Máy thử đang chạy rồi"; bash "$0" check; exit 0; fi
    warn "Có máy thử cũ bị kẹt → tắt hẳn rồi bật lại"; bash "$0" stop >/dev/null 2>&1
  fi
  # Máy Android Google bật "xác nhận adb": máy chỉ tin khoá adb của máy chủ (~/.android/adbkey.pub) lúc khởi động.
  # → dùng CHUNG khoá với trình điều khiển ws-scrcpy, để trang web xem/điều khiển được mà không cần bấm "Cho phép".
  if docker exec $WS sh -c 'test -f ~/.android/adbkey && test -f ~/.android/adbkey.pub' 2>/dev/null; then
    mkdir -p "$HOME/.android"
    docker exec $WS sh -c 'cat ~/.android/adbkey' > "$HOME/.android/adbkey.ws" 2>/dev/null
    docker exec $WS sh -c 'cat ~/.android/adbkey.pub' > "$HOME/.android/adbkey.pub.ws" 2>/dev/null
    if [ -s "$HOME/.android/adbkey.ws" ] && ! cmp -s "$HOME/.android/adbkey.ws" "$HOME/.android/adbkey"; then
      mv -f "$HOME/.android/adbkey.ws" "$HOME/.android/adbkey"; mv -f "$HOME/.android/adbkey.pub.ws" "$HOME/.android/adbkey.pub"
      chmod 600 "$HOME/.android/adbkey"; "$ADB" kill-server >/dev/null 2>&1; ok "Dùng chung khoá adb với trình điều khiển"
    else rm -f "$HOME/.android/adbkey.ws" "$HOME/.android/adbkey.pub.ws"; fi
  fi
  echo "== Bật máy thử (đồ hoạ: $EG) =="
  DISPLAY=$XD setsid nohup "$EMU" -avd "$AVD" -port "$PORT" -no-window -gpu "$EG" -feature -Vulkan -no-audio -no-boot-anim -no-snapshot ${WIPE:+-wipe-data} \
      -camera-back none -camera-front none -netdelay none -netspeed full > "$LOG" 2>&1 < /dev/null &
  "$ADB" start-server >/dev/null 2>&1
  for i in $(seq 1 90); do
    sleep 3
    if ! pgrep -f -- "-avd $AVD" >/dev/null; then err "Máy thử bị tắt khi khởi động. 20 dòng log cuối:"; tail -20 "$LOG"; exit 1; fi
    [ "$("$ADB" -s emulator-$PORT shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
    printf "."
  done; echo
  [ "$("$ADB" -s emulator-$PORT shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || { err "Chưa khởi động xong sau 4 phút — xem: tail -50 $LOG"; exit 1; }
  ok "Android đã khởi động"
  "$ADB" -s emulator-$PORT shell 'settings put global window_animation_scale 0; settings put global transition_animation_scale 0; settings put global animator_duration_scale 0; settings put global stay_on_while_plugged_in 7; svc power stayon true' >/dev/null 2>&1
  docker exec $WS adb connect 127.0.0.1:$((PORT + 1)) >/dev/null 2>&1 && ok "Đã nối vào trình điều khiển: thiết bị 127.0.0.1:$((PORT + 1))"
  echo "   Xem màn hình: đăng nhập admin trên web → https://phone.blueroblox.shop/ → chọn 127.0.0.1:$((PORT + 1))"
  bash "$0" check ;;

chrome)
  # mở Chrome vào google.com trong máy thử → báo chạy được hay văng (đúng lỗi máy 9/10/11 đang gặp)
  "$ADB" -s emulator-$PORT shell 'logcat -c; logcat -b crash -c; am start -W -a android.intent.action.VIEW -d https://www.google.com com.android.chrome >/dev/null 2>&1; sleep 15; pidof com.android.chrome >/dev/null && echo "✅ CHROME CHẠY ĐƯỢC" || echo "❌ CHROME VĂNG"; logcat -d -b crash | grep -m2 -E "Abort|FATAL"' | tr -d '\r'
  echo "   Đồ hoạ: $("$ADB" -s emulator-$PORT shell dumpsys SurfaceFlinger 2>/dev/null | grep -m1 'GLES:' | tr -d '\r')" ;;

check)
  echo "== Android: $("$ADB" -s emulator-$PORT shell getprop ro.build.version.release 2>/dev/null | tr -d '\r') · chip hỗ trợ: $("$ADB" -s emulator-$PORT shell getprop ro.product.cpu.abilist 2>/dev/null | tr -d '\r')"
  echo "== Bộ vẽ hình của máy thử =="
  R1=$("$ADB" -s emulator-$PORT shell dumpsys SurfaceFlinger 2>/dev/null | grep -iE "^GLES:|GLES:" | head -1 | tr -d '\r')
  echo "   $R1"
  if echo "$R1" | grep -qi "nvidia\|geforce"; then ok "ĐANG DÙNG GTX 1080 — thành công!"
  elif echo "$R1" | grep -qi "swiftshader\|llvmpipe"; then err "Đang vẽ bằng CPU (SwiftShader) — chế độ GPU chưa chạy. Gửi kết quả này + 'tail -40 $LOG' cho người hỗ trợ"
  else warn "Chưa đọc được (máy chưa chạy?)"; fi
  echo "== Card đồ hoạ =="
  nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader | sed 's/^/   GPU dùng: /'
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | sed 's/^/   /'
  echo "== CPU / RAM của máy thử =="
  P=$(pgrep -f "qemu-system.*$AVD" | head -1)
  [ -n "$P" ] && ps -o %cpu=,rss= -p "$P" | awk '{printf "   CPU %s%%  (400%% = 4 nhân chạy kín) · RAM %.1f GB\n", $1, $2/1048576}' || warn "Máy thử chưa chạy"
  echo "   So với máy thường (redroid):"
  docker stats --no-stream --format '   {{.Name}}  CPU {{.CPUPerc}}  RAM {{.MemUsage}}' $(docker ps --format '{{.Names}}' | grep -E '^cp_' | head -3) 2>/dev/null ;;

stop)
  "$ADB" -s emulator-$PORT emu kill >/dev/null 2>&1; sleep 3
  pkill -f -- "-avd $AVD" 2>/dev/null; sleep 2; pkill -9 -f -- "-avd $AVD" 2>/dev/null
  docker exec wsscrcpy adb disconnect 127.0.0.1:$((PORT + 1)) >/dev/null 2>&1
  ok "Đã tắt máy thử" ;;

remove)
  bash "$0" stop >/dev/null 2>&1
  rm -rf "$ANDROID_AVD_HOME/$AVD.avd" "$ANDROID_AVD_HOME/$AVD.ini" "$SDK" "$LOG" && ok "Đã xoá máy thử + Android SDK" ;;

*) sed -n '2,12p' "$0" ;;
esac
