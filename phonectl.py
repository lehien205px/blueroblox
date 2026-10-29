#!/usr/bin/env python3
# phonectl.py — Quản lý máy Android (redroid) trên server: tạo/xoá/gia hạn + chặn 85% tài nguyên.
# Nền tảng của bộ điều phối (orchestrator). Chạy: python3 phonectl.py <lệnh> ...
# Lệnh:
#   create <ten> --ram 2 --cpu 2 --days 7 --res 720x1280   : tạo máy (tự chọn cổng, chặn nếu quá 85%)
#   list                                                    : liệt kê máy + hạn dùng
#   delete <ten>                                            : xoá máy
#   renew <ten> --days 7                                    : cộng thêm ngày
#   reap                                                    : xoá hết máy đã hết hạn (dùng cho cron)
#   capacity                                                : xem tài nguyên đã dùng / ngưỡng 85%
import json, os, sys, subprocess, time, argparse, datetime, re

STATE = os.path.expanduser("~/redroid/phones.json")
DATA_ROOT = os.path.expanduser("~/redroid")
IMAGE = "redroid/redroid:11.0.0_gapps_ndk"
WSNAME = "wsscrcpy"                 # container ws-scrcpy (để đăng ký máy cho điều khiển web)
PORT_BASE = 5555
PORT_MAX = 5585                    # adb emulator chỉ quét 5555..5585
LIMIT_PCT = 85                    # ngưỡng chặn (theo % tài nguyên máy chủ)
ARM = ("ro.product.cpu.abilist=x86_64,arm64-v8a,x86,armeabi-v7a,armeabi "
       "ro.product.cpu.abilist64=x86_64,arm64-v8a "
       "ro.product.cpu.abilist32=x86,armeabi-v7a,armeabi "
       "ro.dalvik.vm.isa.arm=x86 ro.dalvik.vm.isa.arm64=x86_64 "
       "ro.enable.native.bridge.exec=1 ro.vendor.enable.native.bridge.exec=1 "
       "ro.vendor.enable.native.bridge.exec64=1 "
       "ro.dalvik.vm.native.bridge=libndk_translation.so ro.ndk_translation.version=0.2.3").split()

def sh(cmd, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r

def load():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {"phones": []}

def save(st):
    os.makedirs(DATA_ROOT, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE)

def host_total():
    # RAM (GB) và số nhân CPU của máy chủ
    mem_kb = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal"): mem_kb = int(line.split()[1]); break
    return mem_kb / 1024 / 1024, os.cpu_count() or 1

def used(st):
    ram = sum(float(p.get("ram", 0)) for p in st["phones"])
    cpu = sum(float(p.get("cpu", 0)) for p in st["phones"])
    return ram, cpu

def cap():
    tr, tc = host_total()
    return tr * LIMIT_PCT / 100.0, tc * LIMIT_PCT / 100.0, tr, tc

def docker_busy_ports():
    # Cổng host đang bị bất kỳ container nào chiếm (kể cả phone1 tạo tay)
    busy = set()
    r = sh(["sudo", "docker", "ps", "--format", "{{.Ports}}"])
    for m in re.findall(r"0\.0\.0\.0:(\d+)->", r.stdout or ""):
        busy.add(int(m))
    return busy

def free_port(st):
    busy = {int(p["port"]) for p in st["phones"]} | docker_busy_ports()
    for port in range(PORT_BASE, PORT_MAX + 1):
        if port not in busy: return port
    return None

def now(): return datetime.datetime.now()
def fmt(dt): return dt.strftime("%d/%m/%Y %H:%M")

def cmd_capacity(a):
    st = load(); ur, uc = used(st); lr, lc, tr, tc = cap()
    print(f"Máy chủ: RAM {tr:.1f} GB, CPU {tc} nhân")
    print(f"Ngưỡng {LIMIT_PCT}%: RAM {lr:.1f} GB, CPU {lc:.1f} nhân")
    print(f"Đã cấp:  RAM {ur:.1f} GB, CPU {uc:.1f} nhân  ({len(st['phones'])} máy)")
    print(f"Còn:     RAM {lr-ur:.1f} GB, CPU {lc-uc:.1f} nhân")

def cmd_create(a):
    st = load()
    if any(p["name"] == a.name for p in st["phones"]):
        print("LỖI: đã có máy tên này."); sys.exit(1)
    ur, uc = used(st); lr, lc, tr, tc = cap()
    if ur + a.ram > lr or uc + a.cpu > lc:
        print(f"HẾT HÀNG: thêm máy này (RAM {a.ram}GB, CPU {a.cpu}) sẽ vượt {LIMIT_PCT}%.")
        print(f"  Còn: RAM {lr-ur:.1f}GB, CPU {lc-uc:.1f} nhân."); sys.exit(2)
    port = free_port(st)
    if not port: print("LỖI: hết cổng (tối đa 30 máy/node)."); sys.exit(3)
    if not re.match(r"^\d+x\d+$", a.res): print("LỖI: --res phải dạng 720x1280."); sys.exit(1)
    w, h = a.res.split("x")
    data = os.path.join(DATA_ROOT, a.name); os.makedirs(data, exist_ok=True)
    run = ["sudo", "docker", "run", "-itd", "--privileged", "--name", a.name,
           f"--memory={a.ram}g", f"--cpus={a.cpu}",
           "-v", f"{data}:/data", "-p", f"{port}:5555", IMAGE,
           f"androidboot.redroid_width={w}", f"androidboot.redroid_height={h}",
           f"androidboot.redroid_dpi={a.dpi}"] + ARM
    r = sh(run)
    if r.returncode != 0:
        print("LỖI tạo máy:", (r.stderr or r.stdout).strip()); sys.exit(4)
    # đăng ký vào ws-scrcpy để điều khiển qua web
    sh(["sudo", "docker", "exec", WSNAME, "adb", "connect", f"127.0.0.1:{port}"])
    exp = now() + datetime.timedelta(days=a.days)
    st["phones"].append({"name": a.name, "port": port, "ram": a.ram, "cpu": a.cpu,
                          "res": a.res, "created": fmt(now()), "expires": exp.isoformat()})
    save(st)
    print(f"✅ Đã tạo máy '{a.name}'  | cổng {port} | RAM {a.ram}GB CPU {a.cpu} | hết hạn {fmt(exp)}")
    print(f"   Chờ ~90s cho máy boot, rồi vào ws-scrcpy (cổng 8000) sẽ thấy máy này.")

def rm_container(name):
    sh(["sudo", "docker", "rm", "-f", name])

def cmd_delete(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    rm_container(a.name)
    sh(["sudo", "docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
    st["phones"] = [x for x in st["phones"] if x["name"] != a.name]; save(st)
    print(f"🗑️  Đã xoá máy '{a.name}'.")

def cmd_renew(a):
    st = load(); p = next((x for x in st["phones"] if x["name"] == a.name), None)
    if not p: print("Không có máy tên này."); sys.exit(1)
    base = datetime.datetime.fromisoformat(p["expires"])
    if base < now(): base = now()
    base += datetime.timedelta(days=a.days)
    p["expires"] = base.isoformat(); save(st)
    print(f"🔄 '{a.name}' gia hạn +{a.days} ngày → hết hạn {fmt(base)}")

def cmd_list(a):
    st = load()
    if not st["phones"]: print("(chưa có máy nào)"); return
    print(f"{'TÊN':<16}{'CỔNG':<7}{'RAM':<6}{'CPU':<5}{'HẾT HẠN':<18}{'CÒN'}")
    for p in st["phones"]:
        exp = datetime.datetime.fromisoformat(p["expires"])
        left = exp - now(); d = left.days; hrs = int(left.total_seconds()//3600)
        status = f"{d}n" if d >= 0 else "HẾT HẠN"
        print(f"{p['name']:<16}{p['port']:<7}{str(p['ram'])+'G':<6}{str(p['cpu']):<5}{fmt(exp):<18}{status}")

def cmd_reap(a):
    st = load(); n = 0
    for p in list(st["phones"]):
        if datetime.datetime.fromisoformat(p["expires"]) < now():
            rm_container(p["name"])
            sh(["sudo", "docker", "exec", WSNAME, "adb", "disconnect", f"127.0.0.1:{p['port']}"])
            st["phones"] = [x for x in st["phones"] if x["name"] != p["name"]]; n += 1
            print(f"🗑️  Hết hạn → xoá '{p['name']}'")
    save(st)
    print(f"Xong. Đã xoá {n} máy hết hạn.")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create"); c.add_argument("name"); c.add_argument("--ram", type=float, default=2); c.add_argument("--cpu", type=float, default=2); c.add_argument("--days", type=int, default=7); c.add_argument("--res", default="720x1280"); c.add_argument("--dpi", type=int, default=320); c.set_defaults(f=cmd_create)
    sub.add_parser("list").set_defaults(f=cmd_list)
    d = sub.add_parser("delete"); d.add_argument("name"); d.set_defaults(f=cmd_delete)
    r = sub.add_parser("renew"); r.add_argument("name"); r.add_argument("--days", type=int, default=7); r.set_defaults(f=cmd_renew)
    sub.add_parser("reap").set_defaults(f=cmd_reap)
    sub.add_parser("capacity").set_defaults(f=cmd_capacity)
    a = ap.parse_args()
    if not getattr(a, "cmd", None): ap.print_help(); sys.exit(0)
    a.f(a)

if __name__ == "__main__":
    main()
