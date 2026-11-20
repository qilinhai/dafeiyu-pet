# -*- coding: utf-8 -*-
"""设置面板「改 → 保存 → 生效」端到端。

真的开一个 PetWindow，真的用设置面板控件改值，真的点保存，
然后查三处：进程内的运行时状态、磁盘上的配置文件、热加载后的模块。

跑完会把备份的配置文件还原（备份在本目录 _bak_settings_test）。
"""
import os
import sys
import json
import shutil
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
BAK = os.path.join(BASE, "_bak_settings_test")
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import pet
import settings_panel as sp

FAIL = []
def ok(cond, name, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{info}]" if info else ""))
    if not cond:
        FAIL.append(name)

def jread(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def restore():
    pairs = [("config.json", os.path.join(APP, "config.json")),
             ("anim_config.json", os.path.join(APP, "anim_config.json")),
             ("sound_config.json", os.path.join(APP, "sound_config.json")),
             ("voice_style.json", os.path.join(BASE, "voice_style.json")),
             ("persona.json", os.path.join(BASE, "persona.json")),
             ("hook_config.json", os.path.join(BASE, "hooks", "hook_config.json")),
             ("notify_config.json", os.path.join(BASE, "notify_config.json"))]
    for name, dst in pairs:
        src = os.path.join(BAK, name)
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                print("  还原失败", name, e)


def main():
    w = pet.PetWindow()
    # save_config 照常落盘（apply_settings 的落盘就靠它）；
    # 跑完 restore() 会把 config.json 整个盖回备份，窗口坐标不会留下。

    # ---------------- 运行页
    print("\n【运行页：语音 / 城市 / 拖拽 / 菜单配色】")
    r = sp.RuntimeTab(w)
    r.speed.setValue(1.3)
    r.volume.setValue(0.55)
    r.city.setText("北京")
    idx = r.auto_eat.findData(1500)
    r.auto_eat.setCurrentIndex(idx if idx >= 0 else 0)
    tidx = r.menu_theme.findData("dark")
    if tidx >= 0:
        r.menu_theme.setCurrentIndex(tidx)
    ok1, m1 = r.save()
    ok(ok1, "运行页保存成功", m1[:60])

    ok(abs(float(w.cfg.get("tts_speed", 0)) - 1.3) < 1e-6, "内存：语速 1.3",
       str(w.cfg.get("tts_speed")))
    if w.tts is not None:
        ok(abs(w.tts.speed - 1.3) < 1e-6, "TTS：立刻用新语速", str(w.tts.speed))
        ok(abs(w.tts._volume() - 0.55) < 1e-6, "TTS：立刻用新音量", str(w.tts._volume()))
    ok(w.cfg.get("city") == "北京", "内存：城市", str(w.cfg.get("city")))
    ok(int(w.cfg.get("drag_auto_eat_ms", 0)) == 1500, "内存：自动吃 1.5s",
       str(w.cfg.get("drag_auto_eat_ms")))
    ok(w.cfg.get("menu_theme") == "dark", "内存：菜单配色 dark",
       str(w.cfg.get("menu_theme")))

    disk = jread(os.path.join(APP, "config.json"))
    ok(abs(float(disk.get("tts_speed", 0)) - 1.3) < 1e-6, "磁盘：语速写进去了")

    # 天气缓存该被清掉（城市变了）
    ok(w._weather_cache is None, "换城市后天气缓存已清")

    # ---------------- 声音模板（独立文件，靠 mtime 热加载）
    print("\n【运行页：提示音】")
    r.snd_vol.setValue(35)
    tpls = [r.snd_tpl.itemData(i) for i in range(r.snd_tpl.count())]
    if len(tpls) > 1:
        r.snd_tpl.setCurrentIndex(1)
    r.snd_on.setChecked(False)
    ok2, m2 = r.save()
    ok(ok2, "提示音保存成功", m2[:60])
    snd = jread(os.path.join(APP, "sound_config.json"))
    ok(snd.get("enabled") is False, "磁盘：提示音已关", str(snd.get("enabled")))
    ok(abs(float(snd.get("volume", -1)) - 0.35) < 1e-6, "磁盘：音量 0.35",
       str(snd.get("volume")))
    if len(tpls) > 1:
        ok(snd.get("template") == tpls[1], "磁盘：模板换成第二个", str(snd.get("template")))

    # ---------------- 动作节奏（anim_config 才是权威）
    print("\n【运行页：动作节奏】")
    r.fg0.setValue(6)
    r.fg1.setValue(12)
    r.ag0.setValue(60)
    r.ag1.setValue(120)
    ok3, m3 = r.save()
    ok(ok3, "节奏保存成功", m3[:60])
    ac = jread(os.path.join(APP, "anim_config.json"))
    ok(ac.get("fidget_gap") == [6, 12], "磁盘：小动作间隔 6-12s", str(ac.get("fidget_gap")))
    ok(ac.get("act_gap") == [60, 120], "磁盘：表演间隔 60-120s", str(ac.get("act_gap")))
    ok(w.anim_cfg.get("fidget_gap") == [6, 12], "内存：pet 已重读节奏",
       str(w.anim_cfg.get("fidget_gap")))

    # ---------------- 定时任务提示音（hook_config，hook 现读）
    print("\n【运行页：定时任务提示音】")
    if r.auto_mode.count() > 1:
        r.auto_mode.setCurrentIndex(1)
    r.ding_open.setValue(3)
    r.ding_close.setValue(4)
    ok4, m4 = r.save()
    ok(ok4, "定时任务设置保存成功", m4[:60])
    hk = jread(os.path.join(BASE, "hooks", "hook_config.json"))
    ok(hk.get("automation_mode") == r.auto_mode.currentData(), "磁盘：模式一致",
       str(hk.get("automation_mode")))
    ok(hk.get("automation_open_dings") == 3, "磁盘：开工叮 3 声")
    ok(hk.get("automation_close_dings") == 4, "磁盘：收工叮 4 声")

    # ---------------- 人设页
    print("\n【人设页】")
    p = sp.PersonaTab()
    p.max_chars.setValue(55)
    ok5, m5 = p.save()
    ok(ok5, "人设保存成功", m5[:60])
    pj = jread(os.path.join(BASE, "persona.json"))
    ok((pj.get("speech") or {}).get("max_chars") == 55, "磁盘：字数上限 55",
       str((pj.get("speech") or {}).get("max_chars")))

    # ---------------- 通知页
    print("\n【通知页】")
    n = sp.NotifyTab(w)
    n.poll.setValue(3.5)
    n.flood_max.setValue(3)
    ok6, m6 = n.save()
    ok(ok6, "通知保存成功", m6[:60])
    nc = jread(os.path.join(BASE, "notify_config.json"))
    ok(abs(float(nc.get("poll_seconds", 0)) - 3.5) < 1e-6, "磁盘：抓取间隔 3.5s",
       str(nc.get("poll_seconds")))
    ok(int(nc.get("flood_max", 0)) == 3, "磁盘：刷屏上限 3")

    w.close()
    print("\n" + "=" * 62)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("运行页 / 人设页 / 通知页：改的值都落地了")
    return 0


def run():
    code = 1
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    finally:
        restore()
        print("（配置文件已还原）")
    QTimer.singleShot(60, lambda: app.exit(code))


QTimer.singleShot(150, run)
code = app.exec()
sys.exit(code)
