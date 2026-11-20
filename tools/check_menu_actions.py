# -*- coding: utf-8 -*-
"""勾选类菜单项真跑一遍：点之前记状态，点之后看状态变了没。

不碰会开窗口 / 连手机 / 花钱的项，只翻开关类。翻完什么都不变，
就是「勾上了但没生效」。

用 offscreen + 配置写盘拦截，不动真配置。
"""
import os
import sys
import json
import copy

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import pet

FAIL = []
SKIP = ("退出", "设置…", "Breeze", "连接手机", "自动找一下", "断开连接",
        "启动通知监听", "停止通知监听", "看监听日志", "打开收藏面板",
        "打开看板", "打开设置文件", "设置（打开配置）")


EXTRA = []          # 写向独立配置文件的记录（抖音 / AI 开关）


def state(w):
    """能反映「设置真的落地了」的那些状态。"""
    return {
        "extra": list(EXTRA),
        "cfg": dict(w.cfg),
        "mode": w.mode,
        "cur_h": w.cur_h,
        "bubble_on": w.bubble_on,
        "sleeping": w.sleeping,
        "tts_on": None if w.tts is None else True,
        "anim_cfg": json.dumps(w.anim_cfg, sort_keys=True, default=str),
        "flags": int(w.windowFlags()),
    }


def diff(a, b):
    out = []
    for k in a:
        if k == "cfg":
            for ck in set(a["cfg"]) | set(b["cfg"]):
                if a["cfg"].get(ck) != b["cfg"].get(ck):
                    out.append(f"cfg.{ck}: {a['cfg'].get(ck)} → {b['cfg'].get(ck)}")
        elif a[k] != b[k]:
            out.append(f"{k}: {a[k]} → {b[k]}")
    return out


def main():
    # 拦写盘：pet 的主配置、抖音配置、ai_talk 配置，一律只记录不落地
    pet.PetWindow.save_config = lambda self: None
    pet.PetWindow._ai_cfg_write = lambda self, patch: (
        EXTRA.append(("ai", json.dumps(patch, sort_keys=True))) or True)
    try:
        import douyin_archive as da
        da.save_config = lambda patch: EXTRA.append(
            ("douyin", json.dumps(patch, sort_keys=True)))
    except Exception as e:
        print("  （抖音模块没加载，跳过它的开关检查）:", e)

    w = pet.PetWindow()
    if w.tts is not None:
        try:
            w.tts.stop = lambda: None
            w.tts.say = lambda *a, **k: None
        except Exception:
            pass

    m = w._build_menu()
    rows = []

    def walk(menu, where="根"):
        for it in menu._items:
            if it.sep:
                continue
            path = f"{where}/{it.text()[:18]}"
            if it.sub is not None:
                walk(it.sub, path)
                continue
            if not it.isEnabled() or not it.isCheckable():
                continue
            if any(s in it.text() for s in SKIP):
                continue
            rows.append((path, it))

    walk(m)
    print(f"勾选类可执行项 {len(rows)} 个（已跳过开窗口/联网类）\n")

    for path, it in rows:
        before = state(w)
        try:
            it.trigger()
            app.processEvents()
        except Exception as e:
            FAIL.append(f"{path} → 触发异常 {type(e).__name__}: {e}")
            print(f"  ✗ {path}  异常 {e}")
            continue
        after = state(w)
        d = diff(before, after)
        if d:
            print(f"  ✓ {path}")
            for line in d:
                print(f"        {line}")
        else:
            # 单选组（大小 / 亮度 / 模式）：点到的正好是当前那一档，本来就不该有变化
            if any(seg in path for seg in ("/大小/", "/亮度/", "/模式/")):
                print(f"  · {path}  已经是这一档，没有变化属正常")
            else:
                print(f"  ⚠ {path}  翻了但状态没变")
                FAIL.append(f"{path} 无变化")

    w.close()
    print("\n" + "=" * 60)
    if FAIL:
        print(f"{len(FAIL)} 处需要看：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("每个勾选项都真的改了状态")
    return 0


def run():
    code = main()
    QTimer.singleShot(80, lambda: app.exit(code))


QTimer.singleShot(120, run)
code = app.exec()
sys.exit(code)
