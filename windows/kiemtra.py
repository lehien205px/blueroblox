# -*- coding: utf-8 -*-
# kiemtra.py — CHỈ XEM (không đổi gì): đối chiếu máy trong MEmu ↔ danh sách BlueRoblox ↔ trạng thái trên web
#   python C:\blue\kiemtra.py
import json, os, subprocess, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
B = r"C:\blue"
C = {}
for l in open(os.path.join(B, "cloudphone.conf"), encoding="utf-8-sig"):
    l = l.strip()
    if "=" in l and not l.startswith("#"): k, v = l.split("=", 1); C[k.strip()] = v.strip()
MC = next(os.path.join(d, "memuc.exe") for d in (C.get("MEMU_DIR", ""), r"C:\Program Files\Microvirt\MEmu", r"D:\Program Files\Microvirt\MEmu")
          if d and os.path.isfile(os.path.join(d, "memuc.exe")))
mem = {}
for ln in (subprocess.run([MC, "listvms"], capture_output=True, text=True, timeout=90).stdout or "").splitlines():
    p = ln.split(",")
    if len(p) >= 4 and p[0].strip().isdigit(): mem[p[1].strip()] = (int(p[0]), p[3].strip() == "1")
def cfg(i, k):
    o = subprocess.run([MC, "getconfigex", "-i", str(i), k], capture_output=True, text=True, timeout=60).stdout or ""
    return o.split(":")[-1].strip() if ":" in o else "?"
ph = {p["name"]: p for p in json.load(open(os.path.join(B, "redroid", "phones.json"), encoding="utf-8")).get("phones", [])}
web = {}
try:
    K = C["SUPABASE_SERVICE_KEY"]
    q = urllib.request.Request(C["SUPABASE_URL"].rstrip("/") + "/rest/v1/cloud_phones?container=like.cp_*&status=neq.cancelled&select=container,status,port,node&limit=500",
                               headers={"apikey": K, "Authorization": "Bearer " + K})
    NODE = C.get("NODE_ID", "win1"); PRI = C.get("PRIMARY", "1") == "1"
    for r in json.loads(urllib.request.urlopen(q, timeout=30).read()):
        if r.get("node") == NODE or (not r.get("node") and PRI): web[r["container"]] = r   # chỉ máy của máy chủ này
except Exception as e: print("(không đọc được web:", e, ")")
names = sorted(set(mem) | set(ph))
print(f"{'MÁY':16} {'MEmu':10} {'NHÂN THẬT':9} {'DS BLUE':8} {'WEB':10} {'CỔNG':6}  GHI CHÚ")
ports = {}
for n in names:
    if n in ph and ph[n].get("port"): ports.setdefault(ph[n]["port"], []).append(n)
tot = 0
for n in names:
    m = mem.get(n); p = ph.get(n); w = web.get(n, {})
    cpu = cfg(m[0], "cpus") if m else "-"
    if m and m[1] and cpu.isdigit(): tot += int(cpu)
    note = []
    if m and not p and not n.startswith("spare_"): note.append("MÁY THỪA (không có trong danh sách)")
    if p and not m: note.append("MẤT MÁY THẬT")
    if p and len(ports.get(p.get("port"), [])) > 1: note.append("TRÙNG CỔNG với " + ",".join(x for x in ports[p["port"]] if x != n))
    print(f"{n:16} {('#%d ' % m[0] + ('chạy' if m[1] else 'tắt')) if m else '—':10} {cpu:9} {'có' if p else '—':8} {w.get('status', '—'):10} {str(p.get('port') if p else '-'):6}  {'; '.join(note)}")
print(f"\nTổng nhân ảo đang CHẠY thật: {tot} / {os.cpu_count()} luồng")
