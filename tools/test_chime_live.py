# -*- coding: utf-8 -*-
"""真机跑一次整点报时：不等到整点，把时间伪造到整点触发。

只做一件真事 —— 走完整的 _maybe_chime 链路（判断 → 选词 → say → 气泡 + 语音），
所以能听出「到点到底会不会响」。不动系统时间，不写 config.json。

用法：
    ../venv/Scripts/python.exe tools/test_chime_live.py            # 用当前小时
    ../venv/Scripts/python.exe tools/test_chime_live.py 15         # 指定小时
    ../venv/Scripts/python.exe tools/test_chime_live.py 15 --mute  # 验证静音只弹字
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

import pet                                             # noqa: E402
import chime                                           # noqa: E402

HOUR = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
MUTE = "--mute" in sys.argv

# 构造 / 收尾都可能写盘，一律拦掉，真实 config.json 一个字都不改
pet.PetWindow.save_config = lambda self: None

app = QApplication([])
w = pet.PetWindow()
w.hide()

hour = HOUR if HOUR is not None else datetime.now().hour
stamp = chime.cn_hour(hour)
print(f"== 伪造时间：今天 {hour:02d}:00  ({stamp}整) ==")

if MUTE:
    w.cfg["mute_all"] = True
    print("   （--mute：应只弹气泡、不出声）")

print(f"   语音: tts={'有' if w.tts is not None else '无'} "
      f"muted={w.muted} chime_speak={w.cfg.get('chime_speak')}")

# 静音是 say() 内部把 speak 改成 False，光看入参看不出来，
# 所以直接数「真去合成/播放」几次 —— 那才是「有没有出声」的证据。
spoken = []
if w.tts is not None:
    _real_tts = w.tts.speak

    def spy_tts(text, **kw):
        spoken.append(text)
        return _real_tts(text, **kw)

    w.tts.speak = spy_tts

# 让「两秒看一次表」的节流放行（t 从 0 起算）
w._chime_check_t = -10.0
w._chime_key = ""


class FakeDT(datetime):
    """只换 now()，其余照旧 —— chime 里是 `from datetime import datetime`，
    换掉模块里的这个名字就等于换了它的表。"""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 20, hour, 0, 0)


chime.datetime = FakeDT

hits = []
_real_say = w.say


def spy(text, **kw):
    hits.append((text, kw.get("speak"), kw.get("force")))
    return _real_say(text, **kw)


w.say = spy

print("   -> 触发 _maybe_chime()")
w._maybe_chime()

if not hits:
    print("   !! 没有触发。检查 chime_on / 时段 / sleeping / dragging")
    sys.exit(1)

text, speak, force = hits[0]
print(f"   气泡文字：{text}")
print(f"   speak={speak} force={force}")
print("   等 9 秒让语音播完……")

QTimer.singleShot(9000, app.quit)
app.exec()

print(f"   >>> 实际去合成/播放语音：{len(spoken)} 次"
      f"{'（静音生效，没出声）' if MUTE and not spoken else ''}")
print("   第二次调用应被「同一小时只报一次」挡掉：")
w._chime_check_t = -10.0
n_before = len(hits)
w._maybe_chime()
print(f"   再触发一次，say 新增 {len(hits) - n_before} 次（应为 0）")
