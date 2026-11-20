# -*- coding: utf-8 -*-
"""大肥鱼 · 手机通知监听 启动器（幂等）。

- 已经在跑 -> 直接报告，不重复启动
- 没在跑   -> 用 pythonw 后台启动 phone_notify.py（前台主循环）
- 中文输出交给本脚本，start-notify.cmd 保持纯 ASCII。

用「进程特征」判断是否已在跑：命令行含 phone_notify.py 的 pythonw 进程。
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))   # 安装目录：跟着文件走，可整体搬移
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
if not os.path.exists(PY):                 # 没建 venv 就退回系统 Python
    PY = sys.executable
    PYW = (PY[:-10] + "pythonw.exe") if PY.endswith("python.exe") else PY
SCRIPT = os.path.join(BASE, "phone_notify.py")
LOG = os.path.join(BASE, "phone_notify.log")


def _fix_console_encoding():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
    except Exception:
        return
    enc = {65001: "utf-8", 936: "gbk", 950: "big5", 437: "cp437"}.get(cp)
    if not enc:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=enc, errors="replace")
        except Exception:
            pass


def find_adb():
    """按「环境变量 -> 常见安装位置 -> PATH」三级找 adb，找不到返回空串。"""
    import shutil
    cands = [
        os.environ.get("ADB_PATH", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "escrcpy",
                     "resources", "extra", "win", "scrcpy", "adb.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk",
                     "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Android", "platform-tools", "adb.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return shutil.which("adb") or ""


def already_running():
    """找命令行里含 phone_notify.py 的 python/pythonw 进程（排除自己）。

    这台精简系统砍了 wmic，改用 PowerShell 的 Get-CimInstance。
    """
    me = os.getpid()
    ps = r'''Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like '*phone_notify.py*' } | Select-Object -ExpandProperty ProcessId'''
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return False
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit() and int(line) != me:
            return True
    return False


def main():
    _fix_console_encoding()

    ADB = find_adb()
    if not ADB:
        print("找不到 Escrcpy 自带的 adb，请确认 Escrcpy 装在了默认位置。")
        return 1

    if already_running():
        print("手机通知监听已经在跑了，不用重复启动。")
        return 0

    if not os.path.exists(PYW):
        print(f"找不到 {PYW}，虚拟环境没建好。")
        return 1

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    logf = open(LOG, "a", encoding="utf-8")
    proc = subprocess.Popen([PYW, "-u", SCRIPT], cwd=BASE, env=env,
                            creationflags=flags, close_fds=True,
                            stdout=logf, stderr=logf)
    logf.close()
    print(f"手机通知监听已启动（PID {proc.pid}），每 2 秒抓一次，日志在 phone_notify.log。")
    print("要停掉：任务管理器里结束这个 PID，或双击 stop-notify.cmd。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
