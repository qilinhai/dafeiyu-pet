# -*- coding: utf-8 -*-
"""一键静音自检：按钮、闸门、状态同步、跟别的开关不打架。

测的是「点了之后真不出声」，不是「配置里有个字段」。
所以把 tts.speak 和 ding.play 都换成记录器，直接数调用次数。
"""
import os
import sys

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


def ok(cond, name, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{info}]" if info else ""))
    if not cond:
        FAIL.append(name)


PLAYED = []      # tts 收到的句子
DINGED = []      # ding 响了几次


def main():
    w = pet.PetWindow()
    w.timer.stop()                      # 冻结主循环，免得呼吸/自说自话干扰计数
    pet.PetWindow.save_config = lambda self: None   # 别把 offscreen 的窗口位置写脏
    w.reload_anim_cfg = lambda silent=True: None

    # ---- 打桩：所有出声都从这里过
    if w.tts is not None:
        w.tts.speak = lambda text, blocking=False, force=False, style=None: (
            PLAYED.append(text) or True)
        w.tts.stop = lambda: None
    import ding as dg
    dg.play = lambda template=None, volume=None: DINGED.append(1) or True

    w.cfg["mute_all"] = False
    w.cfg["tts_enabled"] = True
    w.cfg["tts_ambient"] = False
    w.quick_panel.set_mute(False)

    print("\n【默认状态】")
    ok(not w.muted, "默认不静音")
    ok(w.quick_panel._btn_mute.text() == "静音", "按钮写着「静音」",
       w.quick_panel._btn_mute.text())

    print("\n【按下静音】")
    PLAYED.clear()
    w.toggle_mute()
    ok(w.muted, "配置里 mute_all 打开了")
    ok(w.quick_panel._btn_mute.text() == "已静音", "按钮换成「已静音」",
       w.quick_panel._btn_mute.text())
    ok(bool(w.cfg.get("tts_enabled", True)), "没去动 tts_enabled（长期偏好保留）",
       f"tts_enabled={w.cfg.get('tts_enabled')}")

    PLAYED.clear()
    w.say("这句不该出声", force=True, speak=True, layer="system")
    ok(PLAYED == [], "强制的台词也不出声", f"{PLAYED}")
    ok(w.bubble_text == "这句不该出声", "气泡照旧显示（静音不是闭嘴）",
       f"bubble={w.bubble_text!r}")

    print("\n【静音期间的各种出声】")
    PLAYED.clear()
    w.speak_segments(["日报第一段", "日报第二段"])
    w._pump_speech()
    ok(PLAYED == [], "排队的播报也出不了声", f"{PLAYED}")
    w._speech_q = []

    PLAYED.clear()
    w.set_tts_voice(w.cfg.get("tts_voice"), "测试音色")   # 换音色本来会念一句
    ok(PLAYED == [], "换音色不会漏出一句", f"{PLAYED}")

    DINGED.clear()
    w._on_yoyo_result("悠悠在听", True)
    ok(DINGED == [], "叫悠悠那声「叮」被一起静掉了", f"{DINGED}")

    print("\n【解除静音】")
    PLAYED.clear()
    w.toggle_mute()
    ok(not w.muted, "静音解除")
    ok(w.quick_panel._btn_mute.text() == "静音", "按钮回到「静音」")
    ok(bool(w.cfg.get("tts_enabled", True)), "语音偏好还是开的", 
       f"tts_enabled={w.cfg.get('tts_enabled')}")
    PLAYED.clear()
    w.say("这句要出声", force=True, speak=True, layer="system")
    ok(PLAYED == ["这句要出声"], "解除后声音回来了", f"{PLAYED}")

    DINGED.clear()
    w._on_yoyo_result("悠悠在听", True)
    ok(DINGED == [1], "提示音也回来了", f"{DINGED}")

    print("\n【跟别的开关不打架】")
    w.cfg["tts_enabled"] = False      # 用户本来就关着念诵
    w.set_mute_all(True)
    w.set_mute_all(False)
    ok(bool(w.cfg.get("tts_enabled", True)) is False,
       "本来关着念诵的人，静音再解除也不会被强行打开")

    w.set_mute_all(True)
    w.set_tts_enabled(True)           # 静音时从菜单勾「念出台词」
    ok(not w.muted, "菜单点「念出台词」会顺手抬掉静音闸门")
    ok(w.quick_panel._btn_mute.text() == "静音", "按钮跟着回到未静音")

    print("\n【面板保存不覆盖静音】")
    w.set_mute_all(True)
    w.apply_settings({"size": 0.7, "mute_all": False})   # 面板里的旧快照
    ok(w.muted, "设置面板保存不会把静音状态冲掉",
       f"mute_all={w.cfg.get('mute_all')}")
    w.set_mute_all(False)

    print("\n【右键菜单里的开关】")
    m = w._build_menu()

    def find(menu, title):
        for it in menu._items:
            if it.sub is not None and it.text() == title:
                return it.sub
        return None

    voice = find(m, "语音")
    ok(voice is not None, "有「语音」分组")
    mu = None
    if voice is not None:
        for it in voice._items:
            if "一键静音" in it.text():
                mu = it
                break
    ok(mu is not None, "语音组里有「一键静音」")
    if mu is not None:
        ok(mu.isCheckable() and not mu.isChecked(), "默认没勾")
        mu.trigger()                  # 真触发一次
        ok(w.muted, "点一下菜单项，静音生效")
        m2 = w._build_menu()
        mu2 = None
        for it in find(m2, "语音")._items:
            if "一键静音" in it.text():
                mu2 = it
        ok(mu2 is not None and mu2.isChecked(), "重建菜单后显示已勾选")
        w.set_mute_all(False)

    print("\n【托盘提示】")
    w.set_mute_all(True)
    ok("静音" in w.tray.toolTip(), "托盘悬停能看出在静音", w.tray.toolTip())
    w.set_mute_all(False)
    ok("静音" not in w.tray.toolTip(), "解除后提示复原", w.tray.toolTip())

    print("\n【定时任务的提示音也认这个开关】")
    # hook 是独立进程，播「叮」不经过鱼，只能自己读配置。用临时目录验它的读法。
    import importlib.util
    import json as _json
    import tempfile
    spec = importlib.util.spec_from_file_location(
        "wb_hook_under_test", os.path.join(BASE, "hooks", "wb_hook.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    tmpd = tempfile.mkdtemp(prefix="mute_hook_")
    cfgp = os.path.join(tmpd, "config.json")

    def write_hook_cfg(d):
        with open(cfgp, "w", encoding="utf-8") as f:
            _json.dump(d, f)

    hook.PET_PKG = tmpd
    write_hook_cfg({"mute_all": True})
    ok(hook.pet_muted() is True, "hook 能从 config.json 读到静音")
    ok(hook.play_ding(1, {}) is False, "静音时定时任务不响「叮」")
    write_hook_cfg({"mute_all": False})
    ok(hook.pet_muted() is False, "没静音时照常播")
    os.remove(cfgp)
    ok(hook.pet_muted() is False, "配置文件读不到时不误判为静音（宁可响一声）")

    w.close()

    print("\n" + "=" * 58)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("一键静音全部通过")
    return 0


def run():
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
    QTimer.singleShot(80, lambda: app.exit(code))


QTimer.singleShot(150, run)
sys.exit(app.exec())
