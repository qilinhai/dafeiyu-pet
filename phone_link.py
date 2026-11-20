# -*- coding: utf-8 -*-
"""大肥鱼 · 手机连接：把「手机连没连上」变成一句能问出来的答案。

Escrcpy 只负责投屏，连接断了它不管；通知监听要的是 adb 在线。
这里做三件事：
    1. 查状态（谁在设备列表里、是不是 device）
    2. 断了自己连回来：走 mDNS 发现无线调试服务，不用知道 IP 和端口
    3. 记下上次成功的地址，mDNS 不灵时用它兜底

无线调试的手动配对码只输一次，之后 connect 是免密的——所以这条路能全自动。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ADB = r"C:\Users\ASUS\AppData\Local\Programs\escrcpy\resources\extra\win\scrcpy\adb.exe"
CACHE = os.path.join(HERE, "phone_link.json")

# Windows 上每调一次 adb 默认都会起一个 conhost.exe（控制台宿主）。
# 只加 CREATE_NO_WINDOW 是藏窗口，conhost 还在；DETACHED_PROCESS 才真的不创建，
# 断线重连时这个函数会连着调很多次，漏了它整块屏幕都在闪。
NO_WINDOW = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))

# adb connect 在同 WiFi 内成功只要 0.3~1 秒；手机不在这个网（换了 IP、关了无线调试）
# 就只会干等到 timeout。给 8 秒的代价是调用方整块冻住 8 秒（2026-11-18 实测
# 右键菜单卡 9.47 秒），3 秒足够判断「这个地址现在连不上」。
CONNECT_TMO = 3.0


def _run(args, timeout=10):
    try:
        r = subprocess.run(
            [ADB] + args,
            capture_output=True, timeout=timeout,
            creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def devices():
    """返回 [(serial, state)]，state 正常是 device。"""
    out = _run(["devices"], timeout=6)
    got = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            got.append((parts[0], parts[1]))
    return got


def online():
    """有真的可用设备吗（offline / unauthorized 都不算）。"""
    return any(s == "device" for _, s in devices())


def _cache_addr():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return (json.load(f) or {}).get("addr") or ""
    except Exception:
        return ""


def _save_addr(addr):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"addr": addr, "ts": int(time.time())}, f)
    except Exception:
        pass


def _mdns_addrs(budget=8):
    """mDNS 发现无线调试服务，返回 ['ip:port', ...]。"""
    out = _run(["mdns", "services"], timeout=budget)
    addrs = []
    for line in out.splitlines():
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)", line)
        if m and "_adb-tls-connect" in line:
            addrs.append(m.group(1))
    return addrs


PAIR_TMO = 12.0   # 配对握手正常 1~2 秒就回来；12 秒不回基本就是不在同一网/没开无线调试


def _run2(args, timeout=10):
    """带 stderr 和返回码。配对失败的原因全在 stderr 里，只读 stdout 会看不到。"""
    try:
        r = subprocess.run(
            [ADB] + args,
            capture_output=True, timeout=timeout,
            creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        return out, err, r.returncode
    except subprocess.TimeoutExpired:
        # 中性文案：这条同时用于 pair 和 connect，具体原因交给调用方补
        return "", "adb 等了 %.0f 秒没回应" % timeout, -1
    except Exception as e:
        return "", str(e), -1


def local_ips():
    """本机 IPv4，局域网段排前面。窗口里给用户对照「是不是同一个网」。"""
    ips = []
    try:
        import socket
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:
        # 探出口网卡地址：UDP connect 不发包，只让系统选路
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip not in ips:
            ips.insert(0, ip)
    except Exception:
        pass
    ips = [ip for ip in ips if ip and not ip.startswith("127.")]
    ips.sort(key=lambda x: (not x.startswith(("192.168.", "10.", "172.")), x))
    return ips


def clean_host(ip):
    return str(ip or "").strip().strip("[]").strip()


def valid_host(ip):
    """拦空串和明显错的输入；主机名（手机名.local）也放过。"""
    ip = clean_host(ip)
    return bool(ip) and len(ip) <= 64 and " " not in ip and "\t" not in ip


def valid_port(port):
    s = str(port or "").strip()
    return s.isdigit() and 0 < int(s) < 65536


def discovered(budget=6):
    """mDNS 找到的无线调试连接地址列表。配对成功后常能直接拿到连接端口。"""
    return _mdns_addrs(budget=budget)


def pair(ip, port, code, timeout=PAIR_TMO):
    """无线调试配对：`adb pair IP:配对端口 配对码`。返回 (ok, adb 原文)。

    原文要原样给用户看——配对失败的原因（码输错、端口过期、不在同网）
    全在 adb 的返回里，翻译成「配对失败」等于把线索扔了。
    """
    ip = clean_host(ip)
    port = str(port or "").strip()
    code = str(code or "").strip().replace(" ", "")
    if not valid_host(ip):
        return False, "手机 IP 没填对"
    if not valid_port(port):
        return False, "配对端口要填数字（手机弹窗里冒号后面那个）"
    if not code:
        return False, "配对码没填（手机弹窗里的 6 位数字）"
    out, err, rc = _run2(["pair", "%s:%s" % (ip, port), code], timeout=timeout)
    msg = (out.strip() + " " + err.strip()).strip()
    low = msg.lower()
    ok = "successfully paired" in low or "already paired" in low
    if not msg:
        msg = "adb 没有返回内容"
    return ok, msg


def connect_to(addr, timeout=CONNECT_TMO):
    """连一个明确地址。返回 (ok, adb 原文)。成功就记进缓存，下次自动连。"""
    addr = str(addr or "").strip()
    if ":" not in addr:
        return False, "地址要写成 IP:端口"
    out, err, rc = _run2(["connect", addr], timeout=timeout)
    msg = (out.strip() + " " + err.strip()).strip()
    low = msg.lower()
    ok = ("connected to" in low and "failed" not in low and "cannot" not in low)
    if not ok and online():          # adb 有时回 already connected，主语不同
        ok = True
    if ok:
        _save_addr(addr)
    return ok, msg or "adb 没有返回内容"


def ensure(budget=14):
    """确保手机在线。返回 (ok, 说明)。

    已经连着就秒回；断了才走「mDNS → 缓存地址」两条路，总耗时不超 budget 秒。

    注意 budget 是**硬**上限：每次 adb 调用的 timeout 都从剩余预算里取。
    以前这里写死 timeout=8，budget=6 的调用方（右键菜单）照样被拖到 9.5 秒，
    菜单整个冻住——预算写在参数上却管不住内部，等于没有。
    """
    t0 = time.time()

    def left():
        return budget - (time.time() - t0)

    def conn(addr):
        """连一次。超时取「剩余预算」与 CONNECT_TMO 的较小值。"""
        return _run(["connect", addr],
                    timeout=max(1.0, min(CONNECT_TMO, left())))

    if online():
        return True, "已连着"

    tried = []
    # 1. mDNS 发现（端口每次重开无线调试会变，所以不能只记死地址）
    for addr in _mdns_addrs(budget=min(8.0, max(2.0, left() - 2))):
        if left() <= 1.0:
            break
        conn(addr)
        tried.append(addr)
        time.sleep(1.0)
        if online():
            _save_addr(addr)
            return True, "已连上 %s" % addr

    # 2. 上次成功的地址兜底（mDNS 偶尔不灵，比如路由器挡了多播）
    last = _cache_addr()
    if last and last not in tried and left() > 1.0:
        conn(last)
        tried.append(last)
        time.sleep(1.0)
        if online():
            return True, "已连上 %s（旧地址）" % last

    devs = devices()
    if devs and not online():
        st = devs[0][1]
        return False, "手机在但状态是 %s（可能要重新配对）" % st
    return False, "没发现手机（无线调试可能关了，或者不在同一个 WiFi）"


def summary():
    """一句话说清现在什么状况，给催他的话用。"""
    ok, msg = ensure()
    if ok:
        return True, msg
    devs = devices()
    if devs:
        return False, "%s 掉线了" % (devs[0][0].split("-")[0] if "-" in devs[0][0] else "手机")
    return False, "手机没连上"


if __name__ == "__main__":
    ok, msg = ensure()
    print(("OK  " if ok else "FAIL") + " " + msg)
    raise SystemExit(0 if ok else 2)
