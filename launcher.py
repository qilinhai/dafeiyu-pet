"""大肥鱼启动器（幂等）：
- 已经在跑 -> 直接报告，不重复启动（避免两只鱼 + 端口冲突）
- 没在跑   -> 用 pythonw 后台启动 pet.py，等桥接就绪后打印端口
中文输出交给本脚本，start-pet.cmd 保持纯 ASCII。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))   # 安装目录：跟着文件走，可整体搬移
REPO = BASE if os.path.exists(os.path.join(BASE, "pet.py")) else os.path.join(BASE, "dafeiyu-pet-main")
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")
if not os.path.exists(PY):                 # 没建 venv 就退回系统 Python
    PY = sys.executable
    PYW = (PY[:-10] + "pythonw.exe") if PY.endswith("python.exe") else PY
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DaFeiYuBridge")
ENDPOINT = os.path.join(APP_DIR, "endpoint.json")
TOKEN = os.path.join(APP_DIR, "token.txt")
CANDIDATE_PORTS = [8790, 8791, 8792]


def _fix_console_encoding():
    """按控制台实际代码页输出，避免中文在 cmd 窗口变成乱码。"""
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


def pid_alive(pid):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=8,
        ).stdout
        return str(pid) in out
    except Exception:
        return False


def read_endpoint():
    try:
        with open(ENDPOINT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def health(port, token, timeout=2.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/health",
                                 headers={"X-Pet-Token": token})
    # 直连 127.0.0.1，绕开 http_proxy 环境变量（否则会被代理拦成 502）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_notify():
    """顺手把手机通知监听也拉起来——双击一次，鱼和耳朵一起醒。

    重复启动不用管，phone_notify 自己占 8795 端口当锁，多起的会自己退。
    """
    script = os.path.join(BASE, "phone_notify.py")
    if not os.path.exists(script):
        return
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"      # 日志按 utf-8 写，不然记事本打开是乱码
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        logf = open(os.path.join(BASE, "phone_notify.log"), "a", encoding="utf-8")
        subprocess.Popen([PYW, "-u", script], cwd=BASE, env=env,
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf)
        logf.close()
        print("手机通知监听也起来了（手机连着就会开始播报）。")
    except Exception as e:
        print(f"通知监听没能起来：{e}")


def main():
    _fix_console_encoding()
    token = ""
    if os.path.exists(TOKEN):
        with open(TOKEN, encoding="utf-8") as f:
            token = f.read().strip()

    ep = read_endpoint()
    if ep and token and health(ep.get("port"), token):
        print(f"大肥鱼已经在跑了（PID {ep.get('pid')}，端口 {ep.get('port')}），不用重复启动。")
        start_notify()
        return 0

    if not os.path.exists(PYW):
        print(f"找不到 {PYW}，虚拟环境没建好，请先执行步骤 3。")
        return 1

    port = next((p for p in CANDIDATE_PORTS if port_free(p)), None)
    if port is None:
        print("8790/8791/8792 全被占用了，请先关掉占用端口的程序。")
        return 1

    env = dict(os.environ, DAFEIYU_PORT=str(port))
    # WorkBuddy 会把自带的 shim 塞进 PYTHONPATH，桌宠不需要，清掉避免干扰
    env.pop("PYTHONPATH", None)
    try:
        os.remove(ENDPOINT)
    except OSError:
        pass

    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([PYW, "pet.py"], cwd=REPO, env=env,
                            creationflags=flags, close_fds=True)
    print(f"正在启动大肥鱼（PID {proc.pid}，端口 {port}）……")

    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(0.4)
        cur = read_endpoint()
        if cur and health(cur.get("port"), token):
            print(f"启动成功：127.0.0.1:{cur['port']}，PID {cur.get('pid')}。")
            start_notify()
            return 0
        if proc.poll() is not None:
            print(f"桌宠进程已退出（退出码 {proc.returncode}）。请用前台方式看报错：")
            print(f'  "{PY}" "{os.path.join(REPO, "pet.py")}"')
            return 1

    print("等了 25 秒桥接还没就绪，可能启动失败。请用前台方式看报错：")
    print(f'  "{PY}" "{os.path.join(REPO, "pet.py")}"')
    return 1


if __name__ == "__main__":
    sys.exit(main())
