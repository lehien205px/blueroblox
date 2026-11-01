#!/usr/bin/env bash
# =====================================================================
#  cloudphone-apps.sh — KHO ỨNG DỤNG có sẵn cho máy khách (thư mục ~/apk-cai-san)
#    • App trong kho được TỰ CÀI vào mọi máy (máy mới + máy cũ, ~5 phút/lần) và hiện trong "🛍️ Kho ứng dụng" trên trang điều khiển
#    • Firefox: tải bản CHÍNH THỨC từ máy chủ Mozilla (bản x86_64 chạy nhanh nhất trên máy ảo)
#    • Chrome: Google không phát file cài riêng → khách bấm "Mở CH Play" trong Kho ứng dụng để cài (1 chạm)
#    • App khác: admin tự thêm ở Quản trị → ĐT đám mây → Cài APK (tick "giữ lại cho máy mới"), hoặc chép file .apk vào ~/apk-cai-san
#
#   bash cloudphone-apps.sh firefox     # tải / cập nhật Firefox chính thức vào kho
#   bash cloudphone-apps.sh list        # xem kho
#   bash cloudphone-apps.sh push        # cài ngay kho vào mọi máy đang chạy (không cần đợi 5 phút)
# =====================================================================
set -u
G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; N='\033[0m'
ok() { echo -e "${G}✓${N} $*"; }; warn() { echo -e "${Y}!${N} $*"; }; err() { echo -e "${R}✗${N} $*"; }
D="$HOME/apk-cai-san"; mkdir -p "$D"

case "${1:-}" in
firefox)
  V=$(curl -fsSL -m 30 https://product-details.mozilla.org/1.0/mobile_versions.json | python3 -c "import json,sys; print(json.load(sys.stdin).get('version',''))" 2>/dev/null)
  if [ -z "$V" ]; then   # dự phòng: đọc danh sách bản phát hành trên máy chủ Mozilla
    V=$(curl -fsSL -m 30 https://ftp.mozilla.org/pub/fenix/releases/ | grep -oE 'releases/[0-9]+\.[0-9]+(\.[0-9]+)?/' | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | sort -V | tail -1)
  fi
  [ -n "$V" ] || { err "Không đọc được phiên bản Firefox mới nhất (mất mạng?)"; exit 1; }
  F="$D/Firefox-$V.apk"
  [ -s "$F" ] && { ok "Kho đã có Firefox $V"; exit 0; }
  echo "== Tải Firefox $V (chính thức Mozilla, x86_64) =="
  U="https://ftp.mozilla.org/pub/fenix/releases/$V/android/fenix-$V-android-x86_64/fenix-$V.multi.android-x86_64.apk"
  curl -fL -m 900 -o "$F.part" "$U" || { rm -f "$F.part"; err "Tải lỗi: $U"; exit 1; }
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).testzip()" "$F.part" 2>/dev/null || { rm -f "$F.part"; err "File tải về bị hỏng"; exit 1; }
  rm -f "$D"/Firefox-*.apk; mv "$F.part" "$F"
  ok "Đã thêm Firefox $V vào kho — server tự cài vào mọi máy trong vài phút (hoặc chạy: bash $0 push)" ;;

list)
  echo "Kho: $D"; ls -lh "$D" | awk 'NR>1{print "   " $5 "\t" $9}' ;;

push)
  python3 "$HOME/phonectl.py" preinstall --all && ok "Đã cài kho vào các máy đang chạy" ;;

*) sed -n '2,12p' "$0" ;;
esac
