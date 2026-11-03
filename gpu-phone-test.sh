#!/usr/bin/env bash
# =====================================================================
#  gpu-phone-test.sh — THỬ NGHIỆM "Máy GPU": Android Emulator (Google) dùng card NVIDIA (GTX 1080)
#  Chạy SONG SONG với hệ thống redroid hiện tại, KHÔNG đụng tới máy của khách.
#
#   bash gpu-phone-test.sh install   # cài SDK + Android 11 (có Google Play)  ~10 phút, ~6GB
#   bash gpu-phone-test.sh xsetup    # màn hình ảo trên GTX 1080 (install tự gọi)
#   bash gpu-phone-test.sh start     # bật máy thử (4 nhân, 4GB, 720x1280)
#   bash gpu-phone-test.sh check     # xem máy có ĐANG DÙNG GTX 1080 không + CPU/RAM
#   bash gpu-phone-test.sh report    # báo cáo đủ: GPU · game ARM · root · Play Store · hình · Chrome
#   bash gpu-phone-test.sh auto      # KIỂM TRA TẤT CẢ bản 9·10·11·12 một lệnh → bảng tổng kết (~40–60 phút)
#   bash gpu-phone-test.sh root      # root bằng Magisk (giữ Play Store) · unroot = bỏ root
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
  NW="-no-window"; [ -n "${WIN:-}" ] && NW=""
  DISPLAY=$XD setsid nohup "$EMU" -avd "$AVD" -port "$PORT" $NW -gpu "$EG" -feature -Vulkan -no-audio -no-boot-anim -no-snapshot ${WIPE:+-wipe-data} \
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

root)
  # ROOT máy thử bằng Magisk (bản chính thức) — dùng công cụ rootAVD: vá ramdisk của bản Android đang dùng.
  # Vá xong, MỌI máy dùng bản Android này đều có root. Giữ nguyên Play Store. Gỡ: bash $0 unroot
  export ANDROID_SERIAL=emulator-$PORT PATH="$SDK/platform-tools:$PATH"
  [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || { err "Bật máy thử trước:  bash $0 start"; exit 1; }
  SYS=$(grep "^image.sysdir.1=" "$ANDROID_AVD_HOME/$AVD.avd/config.ini" | cut -d= -f2 | sed 's|/*$||')
  RD="$SDK/$SYS/ramdisk.img"; [ -f "$RD" ] || { err "Không thấy $RD"; exit 1; }
  if [ ! -f "$RD.goc" ]; then cp "$RD" "$RD.goc" && ok "Đã lưu bản gốc: $RD.goc"; fi
  RA="$HOME/rootAVD"
  if [ ! -f "$RA/rootAVD.sh" ]; then
    echo "== Tải rootAVD =="
    rm -rf "$RA" /tmp/rootavd.tgz; mkdir -p "$RA"
    curl -fsSL -o /tmp/rootavd.tgz "https://gitlab.com/newbit/rootAVD/-/archive/master/rootAVD-master.tar.gz" \
      && tar -xzf /tmp/rootavd.tgz -C "$RA" --strip-components=1 && ok "rootAVD" || { err "Không tải được rootAVD"; exit 1; }
  fi
  echo "== Vá root (Magisk) cho $SYS — khoảng 1–3 phút, máy thử sẽ tự tắt khi xong =="
  ( cd "$RA" && ANDROID_HOME="$SDK" bash ./rootAVD.sh "$SYS/ramdisk.img" ) 2>&1 | tail -25
  sleep 5; pgrep -f -- "-avd $AVD" >/dev/null && bash "$0" stop >/dev/null 2>&1
  cmp -s "$RD" "$RD.goc" && warn "Ramdisk giống bản đã lưu (có thể đã root từ trước) — bật máy rồi chạy report để kiểm tra"
  ok "Đã vá root. Bật lại máy:  bash $0 start"
  echo "   Sau khi bật: mở màn hình máy → app Magisk → nếu hỏi 'Cần cài đặt bổ sung' bấm OK (máy tự khởi động lại 1 lần)." ;;

unroot)
  SYS=$(grep "^image.sysdir.1=" "$ANDROID_AVD_HOME/$AVD.avd/config.ini" | cut -d= -f2 | sed 's|/*$||'); RD="$SDK/$SYS/ramdisk.img"
  [ -f "$RD.goc" ] && { bash "$0" stop >/dev/null 2>&1; cp "$RD.goc" "$RD" && ok "Đã trả ramdisk gốc (bỏ root). Bật lại: bash $0 start"; } || err "Không có bản gốc để trả lại" ;;

rootgrant)
  # Cấp root cho Shell + app KHÔNG cần nhìn màn hình: tự đọc giao diện máy (uiautomator) và tự bấm
  A() { "$ADB" -s emulator-$PORT "$@"; }
  ui() { A shell 'uiautomator dump /data/local/tmp/ui.xml >/dev/null 2>&1; cat /data/local/tmp/ui.xml' > /tmp/gpu-ui.xml 2>/dev/null; }
  # find <regex>  → in toạ độ giữa phần tử có chữ khớp ;  toggle <regex> → toạ độ công tắc cùng hàng (nếu đang TẮT)
  pick() { python3 - "$1" "$2" <<'PY'
import re,sys
mode,pat=sys.argv[1],sys.argv[2]; x=open("/tmp/gpu-ui.xml",encoding="utf-8",errors="ignore").read()
nodes=[(dict(re.findall(r'([\w-]+)="([^"]*)"',n))) for n in re.findall(r'<node [^>]*>',x)]
def box(d): a=list(map(int,re.findall(r'\d+',d.get("bounds","")))); return a if len(a)==4 else None
hit=[d for d in nodes if re.fullmatch(pat,d.get("text",""),re.I) and box(d)]
if not hit: sys.exit(0)
b=box(hit[0])
if mode=="find": print((b[0]+b[2])//2,(b[1]+b[3])//2); sys.exit(0)
cy=(b[1]+b[3])//2
sw=[d for d in nodes if d.get("checkable")=="true" and box(d) and box(d)[1]-60<=cy<=box(d)[3]+60]
if sw:
    s=box(sw[0]); print((s[0]+s[2])//2,(s[1]+s[3])//2, sw[0].get("checked"))
PY
  }
  okroot() { A shell 'su -c id' 2>/dev/null | grep -q "uid=0"; }
  if ! okroot; then
    # 1) có hộp hỏi quyền → bấm Cho phép
    ( A shell 'su -c id' >/dev/null 2>&1 & ); sleep 3; ui; T=$(pick find 'Grant|Allow|Cho phép|Cấp quyền')
    [ -n "$T" ] && { A shell input tap $T; ok "Đã bấm Cho phép"; sleep 3; }
  fi
  if ! okroot; then
    # 2) Shell từng bị từ chối → vào app Magisk › Superuser › bật công tắc Shell
    A shell am force-stop com.topjohnwu.magisk; A shell monkey -p com.topjohnwu.magisk -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; sleep 4
    ui; T=$(pick find 'OK|Đồng ý'); [ -n "$T" ] && { A shell input tap $T; sleep 2; ui; }
    T=$(pick find 'Superuser|Siêu người dùng'); [ -n "$T" ] && { A shell input tap $T; sleep 3; ui; }
    R=$(pick toggle 'Shell')
    if [ -n "$R" ]; then set -- $R; [ "$3" = "true" ] || { A shell input tap $1 $2; ok "Đã bật root cho Shell trong Magisk"; }; sleep 2
    else warn "Không thấy dòng Shell trong Magisk › Superuser. Chữ đang có trên màn hình:"; grep -o 'text="[^"]*"' /tmp/gpu-ui.xml | grep -v 'text=""' | head -15; fi
  fi
  if okroot; then
    cat > /tmp/gpu-grant.sh <<'GS'
M=$(command -v magisk || ls /sbin/magisk /debug_ramdisk/magisk 2>/dev/null | head -1)
$M --sqlite "REPLACE INTO settings (key,value) VALUES('root_access',3)"
for u in 2000 $(cmd package list packages -3 -U | sed -n 's/.*uid:\([0-9]*\).*/\1/p' | sort -u); do
  $M --sqlite "REPLACE INTO policies (uid,policy,until,logging,notification) VALUES($u,2,0,1,1)"
done
GS
    A push /tmp/gpu-grant.sh /data/local/tmp/grant.sh >/dev/null 2>&1; A shell su -c "sh /data/local/tmp/grant.sh" >/dev/null 2>&1
    A shell input keyevent 3
    ok "Root OK: Shell + mọi app đã cài được cấp quyền root sẵn"
  else err "Chưa có root cho Shell"; fi ;;
rootboot)
  # ROOT TỰ ĐỘNG KHÔNG CẦN BẤM: gắn vào ramdisk (đã có Magisk) 1 dịch vụ chạy quyền root lúc khởi động,
  # cứ 15 giây cấp sẵn quyền root cho Shell + MỌI app khách cài (tool/game không phải bấm "Cho phép").
  # Áp dụng cho mọi máy dùng bản Android này. Dùng magiskboot (trong Magisk chính thức) chạy ngay trong máy thử.
  A() { "$ADB" -s emulator-$PORT "$@"; }
  [ "$(A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || { err "Bật máy thử trước:  bash $0 start"; exit 1; }
  A shell 'ls -la /system/bin/su' 2>/dev/null | grep -q magisk || { err "Bản này chưa có Magisk trong file khởi động — chạy:  bash $0 root"; exit 1; }

  SYS=$(grep "^image.sysdir.1=" "$ANDROID_AVD_HOME/$AVD.avd/config.ini" | cut -d= -f2 | sed 's|/*$||'); RD="$SDK/$SYS/ramdisk.img"
  [ -f "$RD.magisk" ] || cp "$RD" "$RD.magisk"            # bản đã có Magisk (chưa gắn dịch vụ) — luôn làm lại từ bản này
  AB=$(A shell getprop ro.product.cpu.abi | tr -d '\r'); case "$AB" in x86_64) L=x86_64;; *) L=x86;; esac
  MZ="$HOME/rootAVD/Magisk.zip"; [ -f "$MZ" ] || { err "Không thấy $MZ (chạy: bash $0 root)"; exit 1; }
  unzip -p "$MZ" "lib/$L/libmagiskboot.so" > /tmp/cp-mb 2>/dev/null; [ -s /tmp/cp-mb ] || { err "Không lấy được magiskboot từ Magisk.zip"; exit 1; }
  cat > /tmp/cpgrant.sh <<'GS'
#!/system/bin/sh
# chạy quyền root ngay lúc khởi động (do init gọi), KHÔNG phụ thuộc app Magisk
L=/data/local/tmp/cpgrant.log; exec >$L 2>&1; chmod 644 $L
id; M="${0%/*}/magisk"; [ -x "$M" ] || M=/system/bin/magisk; [ -x "$M" ] || M=$(command -v magisk)
ls -la "${0%/*}" | head -20
for i in 1 2 3 4 5 6 7 8 9 10; do $M -v >/dev/null 2>&1 && break; sleep 3; done
# Magisk chưa "cài đặt bổ sung" (máy mới) → tự làm: chép bộ Magisk vào /data/adb/magisk rồi bật daemon
if ! $M -v >/dev/null 2>&1; then
  mkdir -p /data/adb/magisk /data/adb/modules /data/adb/post-fs-data.d /data/adb/service.d
  for f in magisk magisk32 magisk64 magiskinit magiskpolicy busybox; do [ -f "${0%/*}/$f" ] && cp -af "${0%/*}/$f" /data/adb/magisk/; done
  chmod -R 755 /data/adb/magisk; $M --daemon; sleep 3
fi
echo "magisk: $($M -v 2>&1) / $($M -V 2>&1)"; cp -f /cache/magisk.log /data/local/tmp/magisk.log 2>/dev/null; chmod 644 /data/local/tmp/magisk.log 2>/dev/null
while true; do
  $M --sqlite "REPLACE INTO settings (key,value) VALUES('root_access',3)" >/dev/null 2>&1
  for u in 2000 $(cmd package list packages -3 -U 2>/dev/null | sed -n 's/.*uid:\([0-9]*\).*/\1/p' | sort -u); do
    $M --sqlite "REPLACE INTO policies (uid,policy,until,logging,notification) VALUES($u,2,0,0,0)" >/dev/null 2>&1
  done
  sleep 15
done
GS
  cat > /tmp/cpgrant.rc <<'RC'
service cpgrant /system/bin/sh ${MAGISKTMP}/cpgrant.sh
    user root
    group root
    seclabel u:r:magisk:s0
    oneshot
    disabled

on property:sys.boot_completed=1
    start cpgrant
RC
  T=/data/local/tmp
  A push /tmp/cp-mb $T/mb >/dev/null && A push "$RD.magisk" $T/rd.img >/dev/null && A push /tmp/cpgrant.sh $T/cpgrant.sh >/dev/null && A push /tmp/cpgrant.rc $T/cpgrant.rc >/dev/null || { err "Không chép được file vào máy"; exit 1; }
  OUT=$(A shell "cd $T && chmod 755 mb && rm -f rd.cpio rd.new && ./mb decompress rd.img rd.cpio 2>&1" | tr -d '\r'); echo "   $OUT" | head -3
  FMT=$(echo "$OUT" | sed -n 's/.*\[\(.*\)\].*/\1/p' | head -1); [ -n "$FMT" ] || FMT=lz4_legacy
  A shell "cd $T && (./mb cpio rd.cpio 'exists overlay.d/sbin' || ./mb cpio rd.cpio 'mkdir 0750 overlay.d' 'mkdir 0750 overlay.d/sbin') && ./mb cpio rd.cpio 'add 0644 overlay.d/cpgrant.rc cpgrant.rc' 'add 0755 overlay.d/sbin/cpgrant.sh cpgrant.sh' && ./mb compress=$FMT rd.cpio rd.new" 2>&1 | tr -d '\r' | tail -3
  A pull $T/rd.new /tmp/cp-rd.new >/dev/null 2>&1 && [ -s /tmp/cp-rd.new ] || { err "Đóng gói ramdisk lỗi"; exit 1; }
  cp /tmp/cp-rd.new "$RD" && ok "Đã gắn dịch vụ root tự động ($FMT) vào $SYS"
  A shell "rm -f $T/mb $T/rd.img $T/rd.cpio $T/rd.new $T/cpgrant.sh $T/cpgrant.rc"
  bash "$0" stop >/dev/null 2>&1; bash "$0" start >/dev/null 2>&1; sleep 20
  # App Magisk phải là bản CHÍNH THỨC đúng phiên bản (Magisk kiểm tra chữ ký app; sai chữ ký → từ chối mọi quyền root)
  MV=$(A shell 'magisk -v' 2>/dev/null | tr -d '\r' | cut -d: -f1)
  [ -n "$MV" ] || MV=$(A shell 'cat /data/local/tmp/cpgrant.log' 2>/dev/null | tr -d '\r' | sed -n 's/^magisk: \([0-9.]*\):.*/\1/p' | head -1)
  [ -n "$MV" ] || MV=25.2
  IV=$(A shell dumpsys package com.topjohnwu.magisk 2>/dev/null | tr -d '\r' | sed -n 's/.*versionName=//p' | head -1)
  if [ "$IV" != "$MV" ] || [ ! -f "$HOME/.cache/.magisk-official-$MV" ]; then
    APK="$HOME/.cache/Magisk-v$MV.apk"; mkdir -p "$HOME/.cache"
    [ -s "$APK" ] || curl -fsSL -o "$APK" "https://github.com/topjohnwu/Magisk/releases/download/v$MV/Magisk-v$MV.apk" || { err "Không tải được Magisk v$MV từ GitHub"; exit 1; }
    A uninstall com.topjohnwu.magisk >/dev/null 2>&1; A install -r "$APK" >/dev/null 2>&1 && { ok "Đã cài app Magisk chính thức v$MV"; touch "$HOME/.cache/.magisk-official-$MV"; }
    bash "$0" stop >/dev/null 2>&1; bash "$0" start >/dev/null 2>&1; sleep 25
  fi
  if A shell 'su -c id' 2>/dev/null | grep -q "uid=0"; then ok "ROOT TỰ ĐỘNG: CHẠY ✓ (Shell + mọi app có root, không cần bấm)"
  else err "Root tự động chưa chạy. Thông tin dò lỗi:"
    echo "   dịch vụ cpgrant: $(A shell getprop init.svc.cpgrant | tr -d '\r')"
    A shell 'cat /data/local/tmp/cpgrant.log 2>/dev/null | head -8; echo ---; tail -15 /data/local/tmp/magisk.log 2>/dev/null' | tr -d '\r' | sed 's/^/   /'
  fi ;;

auto)
  # KIỂM TRA TẤT CẢ BẢN 1 LỆNH: với từng bản Android → tải, bật, root (Magisk), cấp root, báo cáo → bảng tổng kết
  VERS="${VERS:-11 12 10 9}"; SUM="$HOME/gpu-auto-ketqua.txt"; : > "$SUM"
  exec > >(tee "$HOME/gpu-auto-log.txt") 2>&1          # ghi toàn bộ quá trình để dò lỗi
  declare -A IM=([9]="system-images;android-28;google_apis_playstore;x86" [10]="system-images;android-29;google_apis_playstore;x86" \
                 [11]="system-images;android-30;google_apis_playstore;x86_64" [12]="system-images;android-31;google_apis_playstore;x86_64")
  for v in $VERS; do
    echo; echo "############ ANDROID $v ############"
    bash "$0" stop >/dev/null 2>&1
    GPU_IMG="${IM[$v]}" bash "$0" image || GPU_IMG="${IM[$v]/;x86/;x86_64}" bash "$0" image || { echo "Android $v: TẢI LỖI" >> "$SUM"; continue; }
    WIPE=1 bash "$0" start >/dev/null 2>&1 || { echo "Android $v: KHÔNG BẬT ĐƯỢC" >> "$SUM"; continue; }
    bash "$0" root </dev/null 2>&1 | tail -12
    bash "$0" start >/dev/null 2>&1; sleep 20
    bash "$0" rootboot 2>&1 | tail -3
    R=$(bash "$0" report 2>&1); echo "$R"
    { echo "== Android $v =="; echo "$R" | grep -E "GPU:|Game ARM:|Root|Play Store:|Ảnh màn hình:|Nén video|Kết nối phát hình|Chrome:" | sed 's/\x1b\[[0-9;]*m//g'; } >> "$SUM"
  done
  echo; echo "================ TỔNG KẾT (lưu ở $SUM) ================"; cat "$SUM" ;;

report)
  # BÁO CÁO 1 LẦN: GPU · game ARM · root · ảnh màn hình có bị đen · Chrome
  A() { "$ADB" -s emulator-$PORT "$@"; }
  [ "$(A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] || { err "Máy thử chưa bật xong (chạy: bash $0 start)"; exit 1; }
  V=$(A shell getprop ro.build.version.release | tr -d '\r'); ABI=$(A shell getprop ro.product.cpu.abilist | tr -d '\r')
  GL=$(A shell dumpsys SurfaceFlinger 2>/dev/null | grep -m1 "GLES:" | tr -d '\r')
  echo "================ BÁO CÁO MÁY THỬ ================"
  echo "Android:        $V   ($(A shell getprop ro.build.type | tr -d '\r') · $(A shell getprop ro.product.name | tr -d '\r'))"
  echo "$GL" | grep -qi "nvidia\|geforce" && ok "GPU:            GTX 1080" || err "GPU:            KHÔNG dùng GTX 1080 ($GL)"
  echo "$ABI" | grep -q arm64 && ok "Game ARM:       chạy được ($ABI)" || err "Game ARM:       KHÔNG ($ABI)"
  if A shell 'su -c id' 2>/dev/null | grep -q "uid=0"; then ok "Root (su):      CÓ"
  elif A shell 'magisk -v' 2>/dev/null | grep -qi magisk; then warn "Root:           Magisk có trong máy nhưng chưa cấp quyền — chạy:  bash $0 rootboot"
  elif A root 2>&1 | grep -qi "cannot run as root\|production"; then err "Root:           KHÔNG (bản có Play Store bị khoá root)"
  else sleep 2; A wait-for-device; A shell id 2>/dev/null | grep -q "uid=0" && warn "Root:           chỉ có 'adb root' (chưa có su cho app)" || err "Root:           KHÔNG"; A unroot >/dev/null 2>&1; sleep 2; A wait-for-device; fi
  A shell pm list packages 2>/dev/null | grep -q com.android.vending && ok "Play Store:     CÓ" || err "Play Store:     KHÔNG"
  BR=$(A exec-out screencap 2>/dev/null | python3 -c "import sys;b=sys.stdin.buffer.read()[16:];px=[b[i]+b[i+1]+b[i+2] for i in range(0,len(b)-4,4*997)];print(int(sum(px)/3/max(1,len(px))))" 2>/dev/null)
  [ "${BR:-0}" -gt 8 ] && ok "Ảnh màn hình:   có hình (độ sáng $BR)" || err "Ảnh màn hình:   ĐEN (độ sáng ${BR:-0}) → đây là lý do web bị đen"
  A shell 'screenrecord --time-limit 2 /data/local/tmp/t.mp4 >/dev/null 2>&1; stat -c %s /data/local/tmp/t.mp4 2>/dev/null; rm -f /data/local/tmp/t.mp4' | tr -d '\r' | { read SZ; [ "${SZ:-0}" -gt 2000 ] && ok "Nén video H264: được (${SZ} byte/2 giây)" || err "Nén video H264: LỖI (${SZ:-0} byte)"; }
  A shell 'am start -W -a android.intent.action.VIEW -d https://www.google.com com.android.chrome >/dev/null 2>&1; sleep 12; pidof com.android.chrome' | tr -d '\r' | grep -q . && ok "Chrome:         chạy được" || err "Chrome:         không chạy"
  SB=$(python3 - "$((PORT + 1))" <<'PY'
import socket,base64,os,time,sys
p=sys.argv[1]
try:
  s=socket.create_connection(("127.0.0.1",8000),5); k=base64.b64encode(os.urandom(16)).decode()
  q=f"/?action=proxy-adb&remote=tcp%3A8886&udid=127.0.0.1%3A{p}"
  s.send(f"GET {q} HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {k}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
  s.settimeout(1); n=0; t=time.time(); h=b""
  while time.time()-t<6:
    try: d=s.recv(65536)
    except socket.timeout: continue
    if not d: break
    n+=len(d); h+=d[:200] if len(h)<200 else b""
  print(n if b" 101 " in h.split(b"\r\n")[0] else -1)
except Exception as e: print(-2)
PY
)
  if [ "${SB:--2}" -gt 300 ]; then ok "Kết nối phát hình: được (${SB} byte/6 giây) → nếu web vẫn đen là do kiểu phát (player)"
  elif [ "${SB:--2}" -ge 0 ]; then err "Kết nối phát hình: nối được nhưng không có dữ liệu (${SB} byte)"
  else err "Kết nối phát hình: KHÔNG nối được (${SB})"; fi
  echo "Cửa sổ:         $(pgrep -af -- '-avd gputest' | grep -q -- '-no-window' && echo 'ẩn (-no-window)' || echo 'hiện trên màn hình ảo :9')"
  echo "=================================================" ;;

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
