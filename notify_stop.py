# -*- coding: utf-8 -*-
"""停止手机通知监听（按命令行特征结束进程）。"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))   # 安装目录：跟着文件走，可整体搬移
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):                 # 没建 venv 就退回系统 Python
    PY = sys.executable


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


def main():
    _fix_console_encoding()
    ps = r'''Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like '*phone_notify.py*' } | Select-Object -ExpandProperty ProcessId'''
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        print("读进程列表失败。")
        return 1
    me = os.getpid()
    killed = 0
    for line in out.splitlines():
        line = line.strip()
        if not line.isdigit():
            continue
        pid = int(line)
        if pid == me:
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
            killed += 1
        except Exception:
            pass
    print(f"已停止 {killed} 个通知监听进程。" if killed else "没有在跑的通知监听进程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
