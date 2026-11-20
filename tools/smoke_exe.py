# -*- coding: utf-8 -*-
"""打包产物冒烟：在隔离的 LOCALAPPDATA 下运行 exe，验桥接后收掉。

用法：python tools/smoke_exe.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
EXE = os.path.join(APP, "dist", "DaFeiYuPet", "DaFeiYuPet.exe")
PORT = "8798"


def main():
    if not os.path.exists(EXE):
        print("找不到 exe：", EXE)
        return 1

    tmp = tempfile.mkdtemp(prefix="dafeiyu_exe_")
    env = dict(os.environ)
    env["LOCALAPPDATA"] = tmp
    env["DAFEIYU_PORT"] = PORT
    env.pop("PYTHONPATH", None)

    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("启动 exe PID", proc.pid)

    ep = os.path.join(tmp, "DaFeiYuBridge", "endpoint.json")
    tok = os.path.join(tmp, "DaFeiYuBridge", "token.txt")
    deadline = time.time() + 60
    ok = False
    while time.time() < deadline:
        time.sleep(1)
        if proc.poll() is not None:
            print("进程已退出，退出码", proc.returncode)
            break
        try:
            info = json.load(open(ep, encoding="utf-8"))
            token = open(tok, encoding="utf-8").read().strip()
            req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/health",
                                         headers={"X-Pet-Token": token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as r:
                print("桥接健康检查:", json.loads(r.read().decode("utf-8")))
            ok = True
            break
        except Exception:
            continue

    alive = False
    for _ in range(10):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
    else:
        alive = True
    print("存活 5 秒:", "是" if alive else "否")

    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    print("结论:", "通过" if (ok and alive) else "未通过")
    return 0 if (ok and alive) else 1


if __name__ == "__main__":
    sys.exit(main())
