# -*- coding: utf-8 -*-
"""真启动冒烟：在隔离的 LOCALAPPDATA 下起一只鱼，等桥接连上，再收掉。

不碰正在运行的实例：桥接/令牌/缓存全部走临时目录。
用法：python tools/smoke_launch.py
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
PORT = "8799"


def main():
    tmp = tempfile.mkdtemp(prefix="dafeiyu_smoke_")
    env = dict(os.environ)
    env["LOCALAPPDATA"] = tmp          # 端点/令牌/缓存全落临时目录
    env["DAFEIYU_PORT"] = PORT
    env.pop("PYTHONPATH", None)

    exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable

    log = open(os.path.join(tmp, "pet.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([exe, "pet.py"], cwd=APP, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    print(f"启动 PID {proc.pid}，端口 {PORT}")

    ep = os.path.join(tmp, "DaFeiYuBridge", "endpoint.json")
    tok = os.path.join(tmp, "DaFeiYuBridge", "token.txt")
    deadline = time.time() + 40
    ok = False
    while time.time() < deadline:
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"进程已退出，退出码 {proc.returncode}")
            break
        try:
            with open(ep, encoding="utf-8") as f:
                info = json.load(f)
            with open(tok, encoding="utf-8") as f:
                token = f.read().strip()
            req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/health",
                                         headers={"X-Pet-Token": token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
            print("桥接健康检查:", data)
            ok = True
            break
        except Exception:
            continue

    # 再观察 6 秒，确认不是「一起来就崩」
    alive_at = None
    for _ in range(12):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
    else:
        alive_at = True

    print("存活 6 秒:", "是" if alive_at else "否")
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log.close()

    txt = open(os.path.join(tmp, "pet.log"), encoding="utf-8", errors="replace").read()
    bad = [ln for ln in txt.splitlines()
           if any(k in ln for k in ("Traceback", "Error", "错误", "失败"))]
    if bad:
        print("日志里的异常行：")
        for ln in bad[:10]:
            print("  ", ln)
    shutil.rmtree(tmp, ignore_errors=True)

    print("结论:", "通过" if (ok and alive_at) else "未通过")
    return 0 if (ok and alive_at) else 1


if __name__ == "__main__":
    sys.exit(main())
