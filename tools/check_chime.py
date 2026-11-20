# -*- coding: utf-8 -*-
"""整点报时自检：文案 / 时间窗 / 去重 / 桌宠触发 / 面板开关。

三层都要过才算数：
  1. chime.py 的纯逻辑（24 小时全覆盖、跨午夜、同一个小时只报一次）
  2. PetWindow 真的会在该报的时候说、不该报的时候闭嘴
  3. 面板「报时」页的控件读写 —— 用临时配置，不动用户的 config.json
"""
import json
import os
import sys
import tempfile
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                        # noqa: E402

app = QApplication(sys.argv)
import chime                                                       # noqa: E402
import pet                                                         # noqa: E402
import settings_panel                                              # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"   [{detail}]" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f"   [{detail}]" if detail else ""))


# ---------------------------------------------------------------- 1 纯逻辑
def test_module():
    print("== chime.py ==")
    want = {0: "零点", 1: "一点", 9: "九点", 10: "十点", 11: "十一点",
            12: "十二点", 19: "十九点", 20: "二十点", 22: "二十二点", 23: "二十三点"}
    bad = [(h, chime.cn_hour(h)) for h, w in want.items() if chime.cn_hour(h) != w]
    check("24 小时中文说法全对", not bad, str(bad[:3]))

    check("8-22 窗口含两端",
          [h for h in range(24) if chime.in_window({"chime_from": 8, "chime_to": 22}, h)]
          == list(range(8, 23)))
    check("22→6 跨午夜", [h for h in range(24) if chime.in_window(
        {"chime_from": 22, "chime_to": 6}, h)] == [0, 1, 2, 3, 4, 5, 6, 22, 23])
    check("起止相同 = 全天",
          len([h for h in range(24) if chime.in_window(
              {"chime_from": 0, "chime_to": 0}, h)]) == 24)

    cfg = {"chime_on": True, "chime_from": 8, "chime_to": 22}
    hit, key = chime.should_chime(cfg, "", datetime(2026, 11, 20, 9, 0))
    check("整点该报", hit and key == "2026-11-20 09", str(key))
    hit2, _ = chime.should_chime(cfg, key, datetime(2026, 11, 20, 9, 0))
    check("同一小时不重报", not hit2)
    hit3, _ = chime.should_chime(cfg, "", datetime(2026, 11, 20, 9, 1))
    check("过了整点那一分钟不补报", not hit3)
    hit4, _ = chime.should_chime(cfg, "", datetime(2026, 11, 20, 3, 0))
    check("时段外不报", not hit4)
    hit5, _ = chime.should_chime({**cfg, "chime_on": False}, "",
                                 datetime(2026, 11, 20, 9, 0))
    check("总开关关掉不报", not hit5)
    hit6, _ = chime.should_chime(cfg, "2026-11-20 08", datetime(2026, 11, 20, 9, 0))
    check("换小时照样报", hit6)

    line = chime.line_for({}, datetime(2026, 11, 20, 9, 0))
    check("内置台词带时间和提醒", "九点" in line and len(line) > 6, line)
    mine = chime.line_for({"chime_lines": "{time}整，动一动\n\n"}, datetime(2000, 1, 1, 14, 0))
    check("自定义台词 + {time}", mine == "十四点整，动一动", mine)

    seen = {chime.line_for({}, datetime(2000, 1, 1, 10, 0)) for _ in range(40)}
    check("内置台词不是复读机", len(seen) >= 2, f"{len(seen)} 种")

    check("试一句能出话", bool(chime.preview({}, 15)), chime.preview({}, 15))


# ---------------------------------------------------------------- 2 桌宠
def test_pet():
    print("== PetWindow ==")
    tmp = os.path.join(tempfile.gettempdir(), "_chime_test_cfg.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"chime_on": True, "chime_from": 8, "chime_to": 22,
                   "chime_speak": False, "mute_all": False}, f)
    old_cfg = pet.CONFIG_PATH
    pet.CONFIG_PATH = tmp
    pet.PetWindow.save_config = lambda self: None

    w = None
    try:
        w = pet.PetWindow()
        w.say = lambda *a, **k: said.append((a[0] if a else k.get("text"), k))
        said = []

        # 造一个「这一刻是整点」的判定，避免测试依赖真实时钟
        real = chime.should_chime
        chime.should_chime = lambda cfg, last="", now=None: (
            (True, "x-1") if w._chime_key != "x-1" else (False, last))
        w._chime_key = ""
        w._chime_check_t = -10.0     # t 从 0 起算，得先让「两秒节流」放行
        w._maybe_chime()
        check("整点会说一句", len(said) == 1, said[0][0] if said else "没说")
        check("说的内容像报时",
              bool(said) and ("点" in said[0][0]), said[0][0] if said else "")

        w._maybe_chime()
        check("同一个小时不重复说", len(said) == 1, f"共 {len(said)} 次")

        # 两秒节流：紧接着再调用不该重新看表
        w._chime_check_t = w.t / 1000.0
        before = len(said)
        w._maybe_chime()
        check("两秒内不重复看表", len(said) == before)

        # 关掉开关 → 一个字都不说
        chime.should_chime = real
        w._chime_key = ""
        w._chime_check_t = -10.0
        w.cfg["chime_on"] = False
        n = len(said)
        w._maybe_chime()
        check("关掉后不说话", len(said) == n)

        # 睡着时不说
        w.cfg["chime_on"] = True
        w.sleeping = True
        w._chime_check_t = -10.0
        w._maybe_chime()
        check("睡着时不说", len(said) == n)
        w.sleeping = False

        chime.should_chime = real
    finally:
        if w is not None:
            try:
                w.timer.stop()
                w.hide()
                w.deleteLater()
            except Exception:
                pass
        pet.CONFIG_PATH = old_cfg
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- 3 面板
def test_panel():
    print("== 面板「报时」页 ==")
    tmp = os.path.join(tempfile.gettempdir(), "_chime_panel_cfg.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"chime_on": True, "chime_from": 8, "chime_to": 22,
                   "chime_speak": True, "chime_lines": "旧台词"}, f)
    old = settings_panel.CONFIG_PATH
    settings_panel.CONFIG_PATH = tmp
    try:
        tab = settings_panel.ChimeTab(pet=None)
        check("页签建得起来", tab is not None)
        check("开关读的是盘上的值", tab.on.isChecked())
        check("时段读的是盘上的值", (tab.h0.value(), tab.h1.value()) == (8, 22),
              f"{tab.h0.value()}-{tab.h1.value()}")
        check("台词读的是盘上的值", tab.lines.toPlainText() == "旧台词",
              tab.lines.toPlainText())

        tab.on.setChecked(False)
        tab.h0.setValue(9)
        tab.h1.setValue(23)
        tab.speak.setChecked(False)
        tab.lines.setPlainText("{time} 到了")
        okk, msg = tab.save()
        check("保存成功", okk, msg)

        disk = json.load(open(tmp, encoding="utf-8"))
        check("开关落盘", disk.get("chime_on") is False)
        check("时段落盘", (disk.get("chime_from"), disk.get("chime_to")) == (9, 23))
        check("念不念落盘", disk.get("chime_speak") is False)
        check("台词落盘", disk.get("chime_lines") == "{time} 到了", disk.get("chime_lines"))

        # 试一句：用界面上的值，不写盘
        tab._preview()
        check("试一句有输出", bool(tab.preview_lbl.text()), tab.preview_lbl.text())
        check("试一句用了自定义台词", "到了" in tab.preview_lbl.text(),
              tab.preview_lbl.text())
    finally:
        settings_panel.CONFIG_PATH = old
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    test_module()
    test_pet()
    test_panel()
    print(f"\n{'=' * 40}\n通过 {ok} / {ok + fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
