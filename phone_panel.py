# -*- coding: utf-8 -*-
"""大肥鱼 · 手机控制面板的后端逻辑。

把「连接手机」「监听手机通知」这两件事，封装成桌宠右键菜单能直接调用的函数。

依赖（同目录或上一层的同名模块）：
    phone_link.py     —— adb 无线连接（mDNS 发现 + 缓存地址兜底）
    phone_notify.py   —— 通知监听主循环（独立进程，端口 8795 单实例锁）
    notify_launcher.py / notify_stop.py —— 启动 / 停止监听的幂等封装

本模块不自己起 adb、不自己跑监听循环，只做三件事：
    1. 查状态：手机连没连、监听在不在跑
    2. 发指令：连一次、断开、启动监听、停止监听
    3. 把结果翻译成一句人话，交回桌宠去说（气泡 + 语音）
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)          # 旧布局：模块在上一层
if not os.path.exists(os.path.join(BASE, "venv", "Scripts", "python.exe")):
    BASE = HERE                       # 绿色版：模块与本文件同层
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):             # 没建 venv 就退回系统 Python
    PY = sys.executable
    PYW = (PY[:-10] + "pythonw.exe") if PY.endswith("python.exe") else PY

ADB = (os.environ.get("ADB_PATH")
       or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "escrcpy",
                       "resources", "extra", "win", "scrcpy", "adb.exe")
       or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk",
                       "platform-tools", "adb.exe"))

# 调外部 exe 不闪黑框：DETACHED_PROCESS 才真的不创建 conhost
NO_WINDOW = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))

# 保证能 import 到上层的 phone_link / phone_notify / notify_launcher
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def adb_available():
    return os.path.exists(ADB)


# ---------------------------------------------------------------- 连接
def _phone_link():
    try:
        import phone_link
        return phone_link
    except Exception:
        return None


def link_status():
    """返回 (ok, 说明)。ok 表示手机当前在线。"""
    if not adb_available():
        return False, "没找到 adb（Escrcpy 没装或不在默认位置）"
    pl = _phone_link()
    if pl is None:
        return False, "连接模块没找到"
    try:
        return pl.ensure(budget=6)
    except Exception as e:
        return False, f"查状态失败：{e}"


def connect():
    """主动连一次。返回 (ok, 说明)。"""
    pl = _phone_link()
    if pl is None:
        return False, "连接模块没找到"
    if pl.online():
        return True, "手机本来就连着"
    try:
        return pl.ensure(budget=14)
    except Exception as e:
        return False, f"连接失败：{e}"


def notify_phone(title, text):
    """给手机弹一条通知（用 adb 的 notification post，手机自己会显示+响铃）。

    标题/正文必须是纯 ASCII 可传的字符串，中文没问题。返回 (ok, 说明)。
    这是「大肥鱼已连接」「大肥鱼已潜入你的手机」这类反向提醒的通道。
    """
    if not adb_available():
        return False, "没找到 adb"
    pl = _phone_link()
    if pl is None:
        return False, "连接模块没找到"
    if not pl.online():
        return False, "手机没在线，发不了"
    try:
        # 走 shell 直接 post：-t 是标题，正文就是 <text> 位置参数。
        # 不整 bigtext 样式——多一道 shell 引号转义，反而容易把命令搞乱。
        # 整条命令用一条字符串交给 adb shell，中文由 subprocess 直接传。
        cmd = "cmd notification post -t '%s' 'DaFeiYu' '%s'" % (
            str(title).replace("'", ""), str(text).replace("'", ""))
        r = subprocess.run(
            [ADB, "shell", cmd],
            capture_output=True, timeout=10, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL)
        if r.returncode == 0:
            return True, "已发到手机"
        err = r.stderr.decode("utf-8", errors="replace").strip()
        return False, "发送失败：" + (err or f"退出码 {r.returncode}")[:40]
    except Exception as e:
        return False, f"发送失败：{e}"


def disconnect():
    """断开无线调试（手机本身不掉，只是断了 adb 这条线）。"""
    pl = _phone_link()
    if pl is None:
        return False, "连接模块没找到"
    try:
        pl._run(["disconnect"])       # 断开所有 adb 连接
        if pl.online():
            return False, "没断干净，还有设备在线"
        return True, "已断开"
    except Exception as e:
        return False, f"断开失败：{e}"


# ---------------------------------------------------------------- YOYO
# 荣耀的 YOYO 智能体 = com.hihonor.magicvoice，主界面是 MagicFloatActivity。
# 那个 Activity 没导出（not exported），adb 直接 -n 过去会吃 SecurityException；
# 但应用自己发布的快捷方式入口 MagicVoiceActivityAlias 是导出的，走它能拉起
# 同一个 MagicFloatActivity（2026-11-06 真机验证，见调试手册）。
YOYO_ALIAS = ("-a com.hihonor.magicvoice.action.shortcut "
              "-n com.hihonor.magicvoice/.startup.MagicVoiceActivityAlias")


def _sh(cmd, timeout=12):
    """跑一条 adb shell 命令，返回 (ok, 输出)。am start 失败时退出码常是 0，
    真正的错在输出里的 Exception/SecurityException，所以别只看返回码。"""
    try:
        r = subprocess.run([ADB, "shell", cmd], capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        return True, (out + "\n" + err).strip()
    except Exception as e:
        return False, str(e)


def keyguard_showing():
    """现在是不是锁屏。锁屏时界面起得来但被盖住，得说清楚，别让人以为没反应。"""
    ok, out = _sh("dumpsys window | grep -m1 isKeyguardShowing", timeout=10)
    return ok and "isKeyguardShowing=true" in out


def open_yoyo():
    """唤起手机上的 YOYO 智能体。返回 (ok, 一句给桌宠说的话)。"""
    if not adb_available():
        return False, "没找到 adb，叫不了悠悠"
    pl = _phone_link()
    if pl is not None and not pl.online():
        ok, msg = pl.ensure(budget=10)     # 断线了顺手重连一次
        if not ok:
            return False, f"手机没连上：{msg}"

    # 灭屏时先点亮：不亮屏的话界面会起在后台，看着像没反应
    _sh("input keyevent KEYCODE_WAKEUP", timeout=8)
    locked = keyguard_showing()

    if locked:
        # 锁屏下 YOYO 只给语音：长按电源键是它自己的锁屏入口（用户提供，实测有效，
        # 出来的是底部「正在倾听…」，可以直接说话）。
        # 这里千万别再 am start 别名——那会把刚起来的倾听浮层顶掉，界面躲回锁屏
        # 后面，看着就像没反应（踩过一次）。
        ok, out = _sh("input keyevent --longpress KEYCODE_POWER", timeout=10)
        if not ok:
            return False, f"锁屏下叫不动悠悠：{out[:60]}"
        return True, "悠悠在听你说话，直接讲"

    ok, out = _sh("am start " + YOYO_ALIAS, timeout=15)
    bad = (not ok) or ("Exception" in out) or ("Error" in out)
    if bad:
        # 兜底：长按电源键，荣耀上就是唤起 YOYO（和锁屏那条同一条路）
        ok2, out2 = _sh("input keyevent --longpress KEYCODE_POWER", timeout=10)
        if not ok2:
            return False, "悠悠没起来，手机上可能把它停用了"
        print(f"[yoyo] 别名入口失败，退回长按电源键：{out[:120]}")
        return True, "悠悠在听你说话"

    return True, "悠悠来了"


# ---------------------------------------------------------------- 监听
def _find_notify_pids():
    """返回所有 phone_notify.py 的进程 PID 列表（可能多个：手动 + 计划任务各起一个）。"""
    try:
        import psutil
        me = os.getpid()
        out = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name not in ("python.exe", "pythonw.exe"):
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                if "phone_notify.py" in cmd and p.info.get("pid") != me:
                    out.append(p.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out
    except Exception:
        return []


def notify_running():
    """监听在不在跑。用进程检测（命令行含 phone_notify.py），不用端口探测。

    为什么不用端口：phone_notify 的单实例锁是 `listen(1)` 且从不 accept，
    用 connect 探测端口会把那唯一的 backlog 槽位占满，第二次探测就失败，
    同一秒里两次调用会得到一真一假两个矛盾结果。进程检测没有这个坑。
    """
    return bool(_find_notify_pids())


def notify_start():
    """启动监听（幂等）。返回 (ok, 说明)。

    优先触发计划任务 DaFeiYuNotify（监听的标准托管方式，和开机自启一致）；
    计划任务不可用时退回直接拉起 phone_notify.py。
    """
    if not adb_available():
        return False, "没找到 adb，监听起不了"
    if notify_running():
        return True, "监听已经在跑"

    # 1. 优先走计划任务：和「开机自动监听」同一套机制，不会出现两个管理源
    try:
        r = subprocess.run(
            ["schtasks", "/run", "/tn", "DaFeiYuNotify"],
            capture_output=True, timeout=20, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return _wait_listening(15, "监听已启动，每 2 秒抓一次",
                                   "监听起了但没占上端口（看 phone_notify.log）")
    except Exception:
        pass

    # 2. 计划任务没了，退回直接拉起（pythonw 后台）
    if not os.path.exists(PYW):
        return False, "找不到 pythonw，虚拟环境没建好"
    try:
        logf = open(os.path.join(BASE, "phone_notify.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [PYW, "-u", os.path.join(BASE, "phone_notify.py")],
            cwd=BASE, creationflags=NO_WINDOW,
            close_fds=True, stdout=logf, stderr=logf)
        logf.close()
    except Exception as e:
        return False, f"启动失败：{e}"
    return _wait_listening(15, "监听已启动，每 2 秒抓一次",
                           "监听起了但没占上端口（看 phone_notify.log）")


def _wait_listening(tries, ok_msg, fail_msg):
    """等监听进程真的起来（命令行出现 phone_notify.py），起来才算成功。"""
    import time
    for _ in range(tries):
        time.sleep(0.4)
        if _find_notify_pids():
            return True, ok_msg
    return False, fail_msg


def notify_stop():
    """停止监听。返回 (ok, 说明)。杀掉所有 phone_notify.py，一个不漏。"""
    pids = _find_notify_pids()
    if not pids:
        return True, "监听本来就没在跑"
    try:
        # taskkill 一次只能 /PID 一个，多个进程就逐个杀
        killed = 0
        for p in pids:
            subprocess.run(
                ["taskkill", "/PID", str(p), "/F", "/T"],
                capture_output=True, timeout=15,
                creationflags=NO_WINDOW)
            killed += 1
        return True, f"监听已停止（{killed} 个进程）"
    except Exception as e:
        return False, f"停止失败：{e}"


def notify_tail(n=6):
    """读监听日志末尾几行，给「现在什么状态」用。"""
    logf = os.path.join(BASE, "phone_notify.log")
    try:
        with open(logf, encoding="utf-8", errors="replace") as f:
            lines = [l.rstrip() for l in f.readlines()[-n:]]
        return lines
    except Exception:
        return []


# ---------------------------------------------------------------- 状态汇总
def full_status():
    """一口气说清现在什么情况。返回一段人话。"""
    if not adb_available():
        return "手机功能没装好：找不到 adb（需要先装 Escrcpy）"

    parts = []
    ok, msg = link_status()
    parts.append("手机：" + msg)

    if notify_running():
        parts.append("通知监听：在跑")
    else:
        parts.append("通知监听：没开")

    return "；".join(parts)


if __name__ == "__main__":
    print(full_status())
