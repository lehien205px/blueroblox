#!/usr/bin/env bash
# =====================================================================
#  gpu-phone-test.sh — THỬ NGHIỆM "Máy GPU": Android Emulator (Google) dùng card NVIDIA (GTX 1080)
#  Chạy SONG SONG với hệ thống redroid hiện tại, KHÔNG đụng tới máy của khách.
#
#   bash gpu-phone-test.sh install   # cài SDK + Android 13 (có Google Play)  ~10 phút, ~6GB
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
IMG="system-images;android-33;google_apis_playstore;x86_64"
TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
LOG="$HOME/gpu-phone-test.log"
export ANDROID_SDK_ROOT="$SDK" ANDROID_HOME="$SDK" ANDROID_AVD_HOME="$HOME/.android/avd"
# adb riêng ở cổng 5038 — KHÔNG dùng chung adb (5037) của ws-scrcpy, tránh làm rớt hình máy khách
export ANDROID_ADB_SERVER_PORT=5038 QT_QPA_PLATFORM=offscreen
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
  yes | "$SM" --licenses >/dev/null 2>&1
  echo "== Tải emulator + Android 13 Google Play (vài phút) =="
  "$SM" "emulator" "platform-tools" "$IMG" >/dev/null && ok "emulator, platform-tools, Android 13 (Google Play)" || { err "Tải SDK lỗi"; exit 1; }
  echo no | "$AM" create avd -n "$AVD" -k "$IMG" -d pixel_4 --force >/dev/null && ok "Tạo máy thử '$AVD'"
  CFG="$ANDROID_AVD_HOME/$AVD.avd/config.ini"
  set_cfg() { grep -q "^$1=" "$CFG" && sed -i "s|^$1=.*|$1=$2|" "$CFG" || echo "$1=$2" >> "$CFG"; }
  set_cfg hw.ramSize 4096; set_cfg hw.cpu.ncore 4; set_cfg hw.gpu.enabled yes; set_cfg hw.gpu.mode host
  set_cfg hw.lcd.width 720; set_cfg hw.lcd.height 1280; set_cfg hw.lcd.density 320
  set_cfg disk.dataPartition.size 16G; set_cfg hw.keyboard yes; set_cfg fastboot.forceColdBoot yes
  set_cfg hw.audioInput no; set_cfg hw.audioOutput no; set_cfg showDeviceFrame no
  ok "Cấu hình: 4 nhân · 4GB · 720x1280 · GPU host (GTX 1080)"
  echo; warn "THOÁT SSH RỒI VÀO LẠI (để nhận quyền KVM), sau đó chạy:  bash $0 start" ;;

start)
  [ -x "$EMU" ] || { err "Chưa cài. Chạy:  bash $0 install"; exit 1; }
  [ -w /dev/kvm ] || { err "Chưa có quyền KVM — thoát SSH rồi vào lại"; exit 1; }
  pgrep -f "qemu.*-avd $AVD|emulator.*-avd $AVD" >/dev/null && { ok "Máy thử đang chạy rồi"; exit 0; }
  echo "== Bật máy thử bằng GPU NVIDIA =="
  setsid nohup "$EMU" -avd "$AVD" -port "$PORT" -no-window -gpu host -no-audio -no-boot-anim -no-snapshot \
      -camera-back none -camera-front none -netdelay none -netspeed full > "$LOG" 2>&1 < /dev/null &
  "$ADB" start-server >/dev/null 2>&1
  for i in $(seq 1 90); do
    sleep 3
    if ! pgrep -f "emulator.*-avd $AVD|qemu.*$AVD" >/dev/null; then err "Máy thử bị tắt khi khởi động. 20 dòng log cuối:"; tail -20 "$LOG"; exit 1; fi
    [ "$("$ADB" -s emulator-$PORT shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
    printf "."
  done; echo
  [ "$("$ADB" -s emulator-$PORT shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || { err "Chưa khởi động xong sau 4 phút — xem: tail -50 $LOG"; exit 1; }
  ok "Android đã khởi động"
  "$ADB" -s emulator-$PORT shell 'settings put global window_animation_scale 0; settings put global transition_animation_scale 0; settings put global animator_duration_scale 0; settings put global stay_on_while_plugged_in 7; svc power stayon true' >/dev/null 2>&1
  docker exec wsscrcpy adb connect 127.0.0.1:$((PORT + 1)) >/dev/null 2>&1 && ok "Đã nối vào trình điều khiển: thiết bị 127.0.0.1:$((PORT + 1))"
  echo "   Xem màn hình: đăng nhập admin trên web → https://phone.blueroblox.shop/ → chọn 127.0.0.1:$((PORT + 1))"
  bash "$0" check ;;

check)
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
  pkill -f "emulator.*-avd $AVD" 2>/dev/null; pkill -f "qemu-system.*$AVD" 2>/dev/null
  docker exec wsscrcpy adb disconnect 127.0.0.1:$((PORT + 1)) >/dev/null 2>&1
  ok "Đã tắt máy thử" ;;

remove)
  bash "$0" stop >/dev/null 2>&1
  rm -rf "$ANDROID_AVD_HOME/$AVD.avd" "$ANDROID_AVD_HOME/$AVD.ini" "$SDK" "$LOG" && ok "Đã xoá máy thử + Android SDK" ;;

*) sed -n '2,12p' "$0" ;;
esac
