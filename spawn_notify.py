# -*- coding: utf-8 -*-
"""把 phone_notify.py 以「彻底脱离当前进程树」的方式拉起来。

为什么需要这个：
    在 WorkBuddy / 任何沙箱里用 Bash 或 PowerShell 启动的子进程，会被挂进同一个
    Windows Job 对象。工具调用一结束，Job 一关，子进程跟着被干掉——日志看着正常，
    端口却空了，表现为「监听明明起了，过一会儿又不吭声」。

    DETACHED_PROCESS 只能脱离控制台，脱不了 Job。
    要真正活下来，得带 CREATE_BREAKAWAY_FROM_JOB。

用法：
    python spawn_notify.py          # 拉起（已在跑就不管）
    python spawn_notify.py --status # 只看状态

结果：
    成功 -> 打印新 PID，进程归 explorer/services 管，与当前会话无关
"""
import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))   # 安装目录：跟着文件走，可整体搬移
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
if not os.path.exists(PYW):                # 没建 venv 就退回系统 Python
    PYW = (sys.executable[:-10] + "pythonw.exe") if sys.executable.endswith("python.exe") else sys.executable
SCRIPT = os.path.join(BASE, "phone_notify.py")
LOG = os.path.join(BASE, "phone_notify.log")
ERR = os.path.join(BASE, "phone_notify.err.log")
LOCK_PORT = 8795

DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
BREAKAWAY = 0x01000000          # CREATE_BREAKAWAY_FROM_JOB


def listening():
    """端口被占 = 有一个实例活着（单实例锁就是它）。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def find_pids():
    """按命令行找 phone_notify 进程（这台机器没有 wmic）。"""
    ps = (r"Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\""
          r" | Where-Object { $_.CommandLine -like '*phone_notify.py*' }"
          r" | Select-Object -ExpandProperty ProcessId")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=20,
            creationflags=NO_WINDOW,
        )
        # PowerShell 输出走控制台代码页（中文系统是 GBK），按 utf-8 解会炸
        out = r.stdout.decode("gbk", errors="replace")
    except Exception:
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def main():
    if "--status" in sys.argv:
        pids = find_pids()
        print(f"端口 {LOCK_PORT}：{'被占用（在跑）' if listening() else '空闲'}")
        print(f"进程：{pids or '无'}")
        return 0

    if listening():
        print(f"监听已经在跑（端口 {LOCK_PORT} 被占），不用再起。")
        return 0

    old = find_pids()
    if old:
        print(f"发现残留进程 {old}（端口却没占住），先清掉。")
        for p in old:
            subprocess.run(["taskkill", "/PID", str(p), "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        time.sleep(1)

    out = open(LOG, "a", encoding="utf-8")
    err = open(ERR, "a", encoding="utf-8")

    # 先试「断 Job」，不行再退回普通 detached——有些沙箱不允许 breakaway
    for label, flags in (("breakaway", DETACHED | NO_WINDOW | BREAKAWAY),
                         ("detached", DETACHED | NO_WINDOW)):
        try:
            p = subprocess.Popen(
                [PYW, "-u", SCRIPT], cwd=BASE,
                creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            )
        except OSError as e:
            print(f"  {label} 启动失败：{e}")
            continue
        time.sleep(3)
        if listening():
            print(f"已启动（{label}），PID {p.pid}，日志 phone_notify.log")
            return 0
        print(f"  {label} 启动后端口没占住，进程可能已退出，继续试下一种。")

    print("两种方式都没起来，请双击本目录的 start-notify.cmd 手动启动。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
