#!/usr/bin/env bash
# =====================================================================
#  cloudphone-root.sh — ROOT cho máy Cloud Phone (Magisk, kiểu "bootless" dành cho redroid)
#    • Dựng thêm bản ROOT cho từng bản Android đang bán (giữ nguyên Google Play / bộ dịch ARM như cũ)
#    • Máy MỚI tạo sau đó tự có root. Máy ĐANG CÓ: chạy "apply" để chuyển sang bản root (giữ nguyên app + tài khoản)
#    • Trong máy có app Magisk: tool xin quyền root → hiện bảng "Cho phép" (Grant) → bấm là xong
#
#   bash cloudphone-root.sh build        # dựng bản root (tải Magisk ~15MB, vài phút)
#   bash cloudphone-root.sh apply        # chuyển TẤT CẢ máy đang có sang bản root (mỗi máy khởi động lại ~1 phút)
#   bash cloudphone-root.sh apply TÊN    # chỉ 1 máy (VD: cp_761fa7e548)
#   bash cloudphone-root.sh check TÊN    # kiểm tra máy đã có root chưa
#   bash cloudphone-root.sh off          # máy MỚI không root nữa (máy đã root giữ nguyên)
# =====================================================================
set -u
G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; N='\033[0m'
ok() { echo -e "${G}✓${N} $*"; }; warn() { echo -e "${Y}!${N} $*"; }; err() { echo -e "${R}✗${N} $*"; }
[ "$(id -u)" = "0" ] && { err "Chạy bằng user thường (không sudo):  bash $0 ${1:-}"; exit 1; }
RS="$HOME/redroid-script"; CONF="$HOME/cloudphone.conf"; PC="python3 $HOME/phonectl.py"
conf_set() { touch "$CONF"; grep -v "^$1=" "$CONF" > "$CONF.tmp"; echo "$1=$2" >> "$CONF.tmp"; cat "$CONF.tmp" > "$CONF"; rm -f "$CONF.tmp"; chmod 600 "$CONF"; }
# các bản Android đang bán (giống phonectl.py)
BASES="redroid/redroid:11.0.0_gapps_ndk redroid/redroid:12.0.0_mindthegapps_ndk redroid/redroid:13.0.0_mindthegapps_houdini redroid/redroid:14.0.0_mindthegapps_houdini"

case "${1:-}" in
build)
  echo "== 1) Chuẩn bị công cụ (cần mật khẩu sudo) =="
  sudo apt-get install -y -qq git python3-venv python3-pip lzip unzip >/dev/null && ok "git, python"
  if [ -d "$RS/.git" ]; then git -C "$RS" pull -q; else git clone -q https://github.com/ayasa520/redroid-script "$RS"; fi && ok "redroid-script"
  [ -x "$RS/.venv/bin/python" ] || python3 -m venv "$RS/.venv"
  "$RS/.venv/bin/pip" install -q -r "$RS/requirements.txt" && ok "Thư viện python"

  echo "== 2) Tải + giải nén Magisk =="
  W="$HOME/.cache/cloudphone-root"; sudo rm -rf "$W"; mkdir -p "$W"
  (cd "$W" && sudo env "PYTHONPATH=$RS" "SUDO_USER=$USER" "$RS/.venv/bin/python" -c "import os,sys; sys.path.insert(0,'$RS'); from stuff.magisk import Magisk; Magisk().install()") > "$W.log" 2>&1
  [ -f "$W/magisk/system/etc/init/bootanim.rc" ] && ok "Magisk đã sẵn sàng" || { err "Chuẩn bị Magisk lỗi — xem: tail -30 $W.log"; exit 1; }
  sudo chown -R "$USER": "$W"
  printf 'COPY magisk /\n' > "$W/Dockerfile.part"

  echo "== 3) Dựng bản ROOT cho từng bản Android đang có =="
  n=0
  for B in $BASES; do
    docker image inspect "$B" >/dev/null 2>&1 || continue
    { echo "FROM $B"; cat "$W/Dockerfile.part"; } > "$W/Dockerfile"
    if docker build -q -t "${B}_root" "$W" >/dev/null 2>>"$W.log"; then ok "${B}_root"; n=$((n+1)); else err "Dựng ${B}_root lỗi — xem: tail -30 $W.log"; fi
  done
  [ $n -gt 0 ] || { err "Không dựng được bản nào"; exit 1; }
  conf_set ROOT 1
  sudo systemctl restart cloudphoned 2>/dev/null
  echo; ok "XONG: máy MỚI tạo từ giờ tự có root (Magisk)."
  echo "   Chuyển máy ĐANG CÓ sang root (giữ nguyên app/tài khoản, mỗi máy khởi động lại ~1 phút):"
  echo "       bash $0 apply" ;;

apply)
  echo "== Chuyển máy sang bản ROOT ${2:+(chỉ $2)} — mỗi máy khởi động lại ~1 phút, app + tài khoản giữ nguyên =="
  $PC rootall ${2:-} ;;

check)
  [ -n "${2:-}" ] || { err "Ghi tên máy:  bash $0 check cp_xxxxxxxx   (xem tên: python3 ~/phonectl.py list)"; exit 1; }
  img=$(docker inspect -f '{{.Config.Image}}' "$2" 2>/dev/null)
  echo "Image: ${img:-không thấy máy}"
  docker exec "$2" /system/bin/sh -c 'ls /sbin/magisk /system/etc/init/magisk/magisk 2>/dev/null; pm list packages 2>/dev/null | grep -i magisk; echo "su: $(which su 2>/dev/null)"' 2>/dev/null
  [[ "$img" == *_root ]] && ok "Máy đang chạy bản ROOT" || warn "Máy CHƯA root — chạy:  bash $0 apply $2" ;;

off)
  conf_set ROOT 0; sudo systemctl restart cloudphoned 2>/dev/null
  ok "Máy mới sẽ KHÔNG root nữa (máy đã root vẫn giữ nguyên)." ;;

*) sed -n '2,14p' "$0" ;;
esac
