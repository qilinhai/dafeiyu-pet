"""真机端到端：开一个记事本，设成「可清理」，看它到底关不关。

只碰记事本，别的应用一个不动。配置走临时文件，不动用户真实配置。

用法：
    ../venv/Scripts/python.exe tools/check_clean_live.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import cleaner  # noqa: E402
import psutil  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"   [{detail}]" if detail else ""))


def notepad_pids():
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() == "notepad.exe":
                out.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="clean_live_"))
    cleaner.CFG_PATH = tmp / "cleaner_config.json"
    cleaner.save_cfg({"min_mb": 0, "apps": {}})

    print("== 开一个记事本 ==")
    before = set(notepad_pids())
    proc = subprocess.Popen(["notepad.exe"])
    try:
        app = None
        for _ in range(20):
            time.sleep(0.3)
            new = set(notepad_pids()) - before
            if not new:
                continue
            target = new.pop()
            # 必须按「刚起来的那个 PID」找它所在的应用组。
            # 按进程名找会误中别的应用（360 桌面助手自己也带一个 notepad.exe，
            # 第一次就是这么把 360 关掉的）。
            apps = cleaner.scan_apps()
            app = next((a for a in apps if target in a["pids"]), None)
            if app and app["has_win"]:
                break
        if app is None:
            check("记事本起来了", False, "扫不到 notepad.exe")
            return 1

        check("记事本起来了", True, f"{app['label']} / id={app['id']}")
        check("它有可见窗口", app["has_win"])
        check("没设置时是「不管」", app["mode"] == cleaner.MODE_IGNORE, app["mode_text"])

        print("== 设成可清理 ==")
        ok, msg = cleaner.set_mode(app["id"], cleaner.MODE_CLEAN)
        check("设成可清理成功", ok, msg)

        again = next(a for a in cleaner.scan_apps() if a["id"] == app["id"])
        check("窗口开着也算「够格动手」", again["eligible"] is True,
              f"has_win={again['has_win']} eligible={again['eligible']}")
        check("说明写着先关窗再结束", "先关窗" in again["why"], again["why"])

        print("== dry 计划 ==")
        d = cleaner.run(dry=True)
        names = [x[0] for x in d["plan"]["close"]]
        check("dry 把记事本列进关窗计划", app["label"] in names, ",".join(names))
        check("dry 没真关", len(notepad_pids()) > 0, f"{len(notepad_pids())} 个进程")

        print("== 真跑 ==")
        before_n = len(notepad_pids())
        r = cleaner.run()
        time.sleep(0.8)
        after_n = len(notepad_pids())
        print(f"  结果：{cleaner.say_line(r)}")
        check("关了至少一个窗口", r["closed"] >= 1, f"closed={r['closed']}")
        check("记事本进程没了", after_n == 0, f"{before_n} → {after_n}")
        check("没被记成「关不掉」", app["label"] not in (r.get("stuck") or []),
              str(r.get("stuck")))
        check("播报不为空", bool(cleaner.say_line(r)), cleaner.say_line(r))

        print("== 重复执行 ==")
        r2 = cleaner.run()
        check("第二次不再有可关的", r2["closed"] == 0 and r2["count"] == 0,
              cleaner.say_line(r2))

        return 0 if FAIL == 0 else 1
    finally:
        for pid in notepad_pids():
            try:
                psutil.Process(pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            proc.wait(timeout=1)
        except Exception:      # noqa: BLE001
            pass
        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")


if __name__ == "__main__":
    sys.exit(main())
