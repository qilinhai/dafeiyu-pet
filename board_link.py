"""抖音收藏看板：按需起服务、页面关了自动收。

看板本身是 `WorkBuddy/2026-11-01-12-41-47/serve.mjs`，托管在 127.0.0.1:4174。
以前桌宠只会 `webbrowser.open` 地址——服务没在跑的时候，用户看到的是
「无法访问此网站」，还以为看板坏了。

这里补上两头：

1. `ensure_server()`：探活，没活就拉一个 `--auto-exit` 实例，等它就绪再放行。
2. 那个实例带页面守卫：网页每 4 秒报一次到，关页面时立刻报「走了」，
   6 秒内没人再报到就自己退出（多开一个标签页不会被误杀）。

三条硬约束（都是踩过的坑）：

- **不能用 `os.system` / `shell=True` 拉 node**：中文用户名路径会被吃掉。
- **探活必须绕开系统代理**：本机代理 127.0.0.1:3373 会劫持回环请求，
  一律 `ProxyHandler({})`。
- **主线程不许等**：起服务最多 25 秒，必须在后台线程里做，桌宠不能被拖住。
"""

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser

BOARD_DIR = r"C:\Users\ASUS\WorkBuddy\2026-11-01-12-41-47"
BOARD_PORT = 4174
BOARD_URL = "http://127.0.0.1:%d/" % BOARD_PORT

# 页面关掉后多久没人报到就退出。给足余量：浏览器对后台标签页的定时器
# 会节流到 1 分钟一次，阈值太短会把「挂在后台的看板」误杀。
IDLE_MS = 300000
# 起服务最多等这么久（首次运行要构建站点，实测 10 秒上下）
BOOT_TMO = 40.0

NO_WINDOW = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))

_proc = None          # 我们自己拉起来的那个 serve.mjs
_lock = threading.Lock()


def _opener():
    """不走代理的 opener：系统代理会把 127.0.0.1 也劫持走。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _port_open(timeout=0.15):
    """端口有没有人在听。

    别省这一步直接发 HTTP：这台机器上「连一个没人听的本地端口」要等 **2 秒**
    才回 ConnectionRefused（测过 127.0.0.1:4173/4174 都一样，是系统层的怪延迟），
    探活就会白等。连接成功的端口是毫秒级完成的，0.15 秒足够分辨。
    """
    s = socket.socket()
    try:
        s.settimeout(timeout)
        s.connect(("127.0.0.1", BOARD_PORT))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def health(timeout=1.5):
    """服务活着就返回 True（顺便确认它真能应答，不只是端口占着）。"""
    if not _port_open():
        return False
    try:
        with _opener().open(BOARD_URL + "api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def find_node():
    """按「WorkBuddy 自带 → 标准安装位置 → PATH」三级找 node.exe。"""
    home = os.path.expanduser("~")
    root = os.path.join(home, ".workbuddy", "binaries", "node", "versions")
    cands = []
    try:
        for name in sorted(os.listdir(root), reverse=True):
            cands.append(os.path.join(root, name, "node.exe"))
    except Exception:
        pass
    cands.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "nodejs", "node.exe"))
    for p in cands:
        if os.path.isfile(p):
            return p
    # 最后试 PATH（shutil.which 不依赖 shell 解析，中文路径也安全）
    import shutil
    return shutil.which("node")


def ensure_server():
    """确保看板服务在线。返回 (ok, 说明)。

    已经活着就秒回——不管是谁起的（用户双击的 .cmd 也算活着，
    不能去动别人的进程）。没活才自己拉一个 --auto-exit 实例。
    """
    if health():
        return True, "看板服务已经在跑"

    node = find_node()
    if not node:
        return False, "找不到 node.exe，看板起不来"
    if not os.path.isfile(os.path.join(BOARD_DIR, "serve.mjs")):
        return False, "看板目录不见了：%s" % BOARD_DIR

    env = dict(os.environ)
    env["NO_OPEN"] = "1"                 # 浏览器我们自己开，别开两份
    env["BOARD_IDLE_MS"] = str(IDLE_MS)
    try:
        proc = subprocess.Popen(
            [node, os.path.join(BOARD_DIR, "serve.mjs"), "--auto-exit"],
            cwd=BOARD_DIR, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except Exception as exc:
        return False, "拉服务失败：%s" % exc
    with _lock:
        globals()["_proc"] = proc
    p = proc

    t0 = time.time()
    while time.time() - t0 < BOOT_TMO:
        if health(1.0):
            return True, "看板服务已就绪（%.1fs）" % (time.time() - t0)
        if p.poll() is not None:
            # 进程死了还探不到：端口被别的东西占着，或 node 报错退出
            if health(1.0):
                return True, "看板服务已经在跑"
            return False, "服务起来了又退了（端口被占？）"
        time.sleep(0.4)
    return False, "服务 %.0f 秒没就绪，可能正在重建站点" % BOOT_TMO


def open_board(on_done=None):
    """起服务 + 开网页，全程在后台线程里，主线程不等。

    `on_done(ok, msg)` 在后台线程结束时回调。桌宠那边不能直接动气泡，
    必须用信号转回主线程——回调里只许 emit。
    """
    def work():
        ok, msg = False, "起看板失败"
        try:
            ok, msg = ensure_server()
            if ok:
                webbrowser.open(BOARD_URL)
                msg = "看板打开了"
        except Exception as exc:
            ok, msg = False, "起看板出错：%s" % exc
        if on_done is not None:
            try:
                on_done(ok, msg)
            except Exception:
                pass

    t = threading.Thread(target=work, name="board-open", daemon=True)
    t.start()
    return t


def stop_server(timeout=5.0):
    """主动停掉我们自己拉的那个实例（用户自己起的 .cmd 不动）。"""
    p = _proc
    if p is None or p.poll() is not None:
        return False
    try:
        p.terminate()
        p.wait(timeout=timeout)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    return True


if __name__ == "__main__":
    t0 = time.time()
    print(json.dumps(dict(zip(("ok", "msg"), ensure_server())), ensure_ascii=False),
          "%.1fs" % (time.time() - t0))
    print("health:", health())
