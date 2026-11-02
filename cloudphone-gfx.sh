#!/usr/bin/env bash
# =====================================================================
#  cloudphone-gfx.sh — LẮP "CARD ĐỒ HOẠ GIẢ" ĐỜI MỚI (của Android 12) SANG Android 9 / 10 / 11  (thử nghiệm)
#    Lỗi: Android 9/10/11 dùng SwiftShader 4.1 (2019) → không tạo được OpenGL ES 3 → Chrome mới + game: màn trắng rồi văng.
#    Cách: chép bộ đồ hoạ phần mềm của Android 12 (chạy được ES 3) vào bản 9/10/11 → dựng bản mới tên *_gfx.
#    Máy khách KHÔNG bị đụng tới cho tới khi bạn chạy "on".
#
#   bash cloudphone-gfx.sh check          # xem bộ đồ hoạ của từng bản Android đang có
#   bash cloudphone-gfx.sh build 11       # dựng bản Android 11 đồ hoạ mới (11 / 10 / 9)
#   bash cloudphone-gfx.sh test 11        # bật 1 máy THỬ (cổng 5597) từ bản đó → tự mở Chrome vào google.com → báo chạy được hay không
#   bash cloudphone-gfx.sh stop           # tắt + xoá máy thử
#   bash cloudphone-gfx.sh on             # thử ổn → máy 9/10/11 MỚI dùng bản đồ hoạ mới + chuyển máy 9/10/11 ĐANG CÓ (giữ dữ liệu, ~1 phút/máy)
#   bash cloudphone-gfx.sh off            # trả mọi máy về bản cũ
# =====================================================================
set -u
G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; NC='\033[0m'
ok() { echo -e "${G}✓${NC} $*"; }; warn() { echo -e "${Y}!${NC} $*"; }; err() { echo -e "${R}✗${NC} $*"; }
[ "$(id -u)" = "0" ] && { err "Chạy bằng user thường (không sudo):  bash $0 ${1:-}"; exit 1; }
CONF="$HOME/cloudphone.conf"; W="$HOME/.cache/cloudphone-gfx"; T=gfxtest; TP=5597
conf_set() { touch "$CONF"; grep -v "^$1=" "$CONF" > "$CONF.tmp"; echo "$1=$2" >> "$CONF.tmp"; cat "$CONF.tmp" > "$CONF"; rm -f "$CONF.tmp"; chmod 600 "$CONF"; }
img_of() { python3 - "$1" <<'PY'
import sys, importlib.util, os
want = sys.argv[1] if len(sys.argv) > 1 else "11"
spec = importlib.util.spec_from_file_location("pc", os.path.expanduser("~/phonectl.py")); m = importlib.util.module_from_spec(spec)
sys.argv = ["phonectl.py"]
try: spec.loader.exec_module(m)
except SystemExit: pass
print(m.IMAGES[m.android_of(want)][0])
PY
}
sdk_of() { case "$1" in 9) echo 28;; 10) echo 29;; 11) echo 30;; 12) echo 31;; *) echo 0;; esac; }
gl_line() { docker exec "$1" sh -c 'dumpsys SurfaceFlinger 2>/dev/null | grep -m1 "^GLES"; getprop ro.hardware.egl; ls /vendor/lib64/egl 2>/dev/null | tr "\n" " "' 2>/dev/null; }

case "${1:-}" in
check)
  for c in $(docker ps --format '{{.Names}}' | grep -E '^cp_|^gfxtest'); do
    v=$(docker exec "$c" getprop ro.build.version.release 2>/dev/null | tr -d '\r'); case " ${seen:-} " in *" $v "*) continue;; esac; seen="${seen:-} $v"
    echo "== Android $v ($c · $(docker inspect -f '{{.Config.Image}}' "$c" | sed 's#redroid/redroid:##'))"; gl_line "$c" | sed 's/^/   /'
  done ;;

build)
  V="${2:-11}"; SRC=$(img_of 12); DST=$(img_of "$V")
  [ -n "$SRC" ] && docker image inspect "$SRC" >/dev/null 2>&1 || { err "Chưa có bản Android 12 ($SRC)"; exit 1; }
  docker image inspect "$DST" >/dev/null 2>&1 || { err "Chưa có bản Android $V ($DST)"; exit 1; }
  echo "== Lấy bộ đồ hoạ từ $SRC → lắp vào $DST =="
  rm -rf "$W/$V"; mkdir -p "$W/$V/f"
  cid=$(docker create "$SRC" 2>/dev/null) || { err "Không mở được $SRC"; exit 1; }
  for d in /vendor/lib64/egl /vendor/lib/egl; do docker cp "$cid:$d" "$W/$V/f/$(echo "$d" | tr / _)" >/dev/null 2>&1; done
  mkdir -p "$W/$V/f/hw64" "$W/$V/f/hw32"
  for f in $(docker export "$cid" 2>/dev/null | tar -t 2>/dev/null | grep -E '^vendor/lib(64)?/hw/vulkan\.[^/]+\.so$'); do
    case "$f" in vendor/lib64/*) docker cp "$cid:/$f" "$W/$V/f/hw64/" >/dev/null 2>&1;; *) docker cp "$cid:/$f" "$W/$V/f/hw32/" >/dev/null 2>&1;; esac
  done
  docker rm "$cid" >/dev/null
  [ -d "$W/$V/f/_vendor_lib64_egl" ] || { err "Bản 12 không có /vendor/lib64/egl"; exit 1; }
  ok "Bộ đồ hoạ Android 12: $(ls "$W/$V/f/_vendor_lib64_egl" | tr '\n' ' ')"
  { echo "FROM $DST"
    echo "COPY f/_vendor_lib64_egl/ /vendor/lib64/egl/"
    [ -d "$W/$V/f/_vendor_lib_egl" ] && echo "COPY f/_vendor_lib_egl/ /vendor/lib/egl/"
    ls "$W/$V/f/hw64"/*.so >/dev/null 2>&1 && echo "COPY f/hw64/ /vendor/lib64/hw/"
    ls "$W/$V/f/hw32"/*.so >/dev/null 2>&1 && echo "COPY f/hw32/ /vendor/lib/hw/"
  } > "$W/$V/Dockerfile"
  docker build -q -t "${DST}_gfx" "$W/$V" >/dev/null && ok "Đã dựng ${DST}_gfx" || { err "Dựng lỗi"; exit 1; }
  echo "   Tiếp theo — thử:  bash $0 test $V" ;;

test)
  V="${2:-11}"; DST=$(img_of "$V"); S=$(sdk_of "$V")
  docker image inspect "${DST}_gfx" >/dev/null 2>&1 || { err "Chưa dựng — chạy:  bash $0 build $V"; exit 1; }
  REF=""; for c in $(docker ps --format '{{.Names}}' | grep '^cp_'); do [ "$(docker exec "$c" getprop ro.build.version.sdk 2>/dev/null | tr -dc 0-9)" = "$S" ] && { REF=$c; break; }; done
  [ -n "$REF" ] || { err "Cần ít nhất 1 máy Android $V đang chạy để lấy cấu hình mẫu"; exit 1; }
  ARGS=$(docker inspect -f '{{join .Args " "}}' "$REF")
  docker rm -f "$T" >/dev/null 2>&1
  echo "== Bật máy THỬ $T (cổng $TP) từ ${DST}_gfx, cấu hình giống $REF =="
  # shellcheck disable=SC2086
  docker run -itd --privileged --name "$T" --memory 6g --cpus 4 -p 127.0.0.1:$TP:5555 "${DST}_gfx" $ARGS >/dev/null || { err "Không bật được máy thử"; exit 1; }
  for i in $(seq 1 60); do [ "$(docker exec "$T" getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 3; done
  [ "$(docker exec "$T" getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] || { err "Máy thử không khởi động được (bộ đồ hoạ 12 không hợp Android $V) — chạy: bash $0 stop"; docker exec "$T" logcat -d -b crash 2>/dev/null | grep -m5 -E "Process:|Abort|CANNOT LINK|not found"; exit 1; }
  sleep 15; ok "Máy thử đã khởi động"
  echo "   Đồ hoạ: $(gl_line "$T" | head -1)"
  docker exec "$T" sh -c 'settings put global device_provisioned 1; settings put secure user_setup_complete 1; pm disable com.google.android.setupwizard >/dev/null 2>&1; true'
  if ! docker exec "$T" sh -c 'pm path com.android.chrome' >/dev/null 2>&1; then
    APK=$(ls "$HOME"/apk-cai-san/Chrome-1*.apks "$HOME"/apk-cai-san/Chrome-1*.apk 2>/dev/null | head -1)
    if [ -n "$APK" ]; then echo "   … cài Chrome vào máy thử"; docker exec wsscrcpy adb connect 127.0.0.1:$TP >/dev/null 2>&1; sleep 2
      python3 -c "import sys,os; sys.path.insert(0,os.path.expanduser('~')); import importlib.util as u; s=u.spec_from_file_location('pc',os.path.expanduser('~/phonectl.py')); m=u.module_from_spec(s); sys.argv=['x']; s.loader.exec_module(m); r=m.install_file($TP, '$APK'); print((r.stdout or '')[-80:])" 2>/dev/null; fi
  fi
  docker exec "$T" sh -c 'logcat -c; logcat -b crash -c; am start -a android.intent.action.VIEW -d https://www.google.com -p com.android.chrome >/dev/null 2>&1; sleep 15
    if pidof com.android.chrome >/dev/null; then echo "RESULT_OK"; else echo "RESULT_BAD"; fi
    logcat -d | grep -m4 -E "eglCreateContext|GPU process isn.t usable|CANNOT LINK|dlopen failed.*egl"' > "$W/test.txt" 2>&1
  if grep -q RESULT_OK "$W/test.txt"; then ok "✅ CHROME CHẠY ĐƯỢC trên Android $V đồ hoạ mới"; echo "   Xem tận mắt: admin → https://phone.blueroblox.shop/ → thiết bị 127.0.0.1:$TP (thử thêm game)"
    echo "   Ổn rồi thì:  bash $0 on   (rồi  bash $0 stop  để xoá máy thử)"
  else err "❌ Vẫn văng trên Android $V"; grep -v RESULT "$W/test.txt" | sed 's/^/   /'; echo "   Gửi kết quả này cho người hỗ trợ. Xoá máy thử: bash $0 stop"; fi ;;

stop) docker rm -f "$T" >/dev/null 2>&1; ok "Đã xoá máy thử" ;;

on)
  n=0; for V in 9 10 11; do docker image inspect "$(img_of $V)_gfx" >/dev/null 2>&1 && n=$((n+1)); done
  [ $n -gt 0 ] || { err "Chưa dựng bản nào — chạy: bash $0 build 11"; exit 1; }
  conf_set GFX 1; sudo systemctl restart cloudphoned 2>/dev/null
  echo "== Chuyển máy 9/10/11 đang có sang đồ hoạ mới (giữ dữ liệu, ~1 phút/máy) =="
  for c in $(docker ps -a --format '{{.Names}}' | grep '^cp_'); do
    img=$(docker inspect -f '{{.Config.Image}}' "$c"); case "$img" in *_gfx) echo "✓ $c đã dùng bản mới"; continue;; esac
    docker image inspect "${img}_gfx" >/dev/null 2>&1 || continue
    echo "… $c"; python3 "$HOME/phonectl.py" recreate "$c" | tail -1
  done; ok "XONG — máy 9/10/11 mới tạo cũng tự dùng đồ hoạ mới" ;;

off)
  conf_set GFX 0; sudo systemctl restart cloudphoned 2>/dev/null
  for c in $(docker ps -a --format '{{.Names}}' | grep '^cp_'); do
    case "$(docker inspect -f '{{.Config.Image}}' "$c")" in *_gfx) echo "… $c"; python3 "$HOME/phonectl.py" recreate "$c" | tail -1;; esac
  done; ok "Đã trả về bản cũ" ;;

*) sed -n '2,15p' "$0" ;;
esac
