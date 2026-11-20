# -*- coding: utf-8 -*-
"""逐条点评 + 长播报队列自检。

跑法（dafeiyu-pet-main 目录下）：
    ..\\venv\\Scripts\\python.exe tools\\check_daily_takes.py           # 不花钱，只跑缓存和队列
    ..\\venv\\Scripts\\python.exe tools\\check_daily_takes.py --live    # 再多花几分钱，真调一次模型

只读真实存档；缓存命中就不调接口。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)

from PySide6.QtCore import QObject                 # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

import pet                                         # noqa: E402
import douyin_archive as da                        # noqa: E402
import douyin_take as dt                           # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class FakeTTS:
    def __init__(self):
        self.speaking = False
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.speaking = False


FAKE_TIME = [100.0]


class FakePet(QObject):
    """只把播报队列要用的字段给齐，不构造整个桌宠。"""
    speak_segments = pet.PetWindow.speak_segments
    stop_speech = pet.PetWindow.stop_speech
    _pump_speech = pet.PetWindow._pump_speech
    _speech_seconds = staticmethod(pet.PetWindow._speech_seconds)

    def __init__(self):
        super().__init__()
        self._speech_q = []
        self._speech_timer = None
        self._speech_until = 0.0
        self._speech_hard_until = 0.0
        self._speech_gap = 0.5
        self.tts = FakeTTS()
        self.bubble_until = 5
        self.bubble_text = "x"
        self.ambient_hold_until = 0.0
        self.t = 0
        self.cfg = {}
        self.said = []

    def _now(self):
        return FAKE_TIME[0]

    def say(self, text, **kw):
        self.said.append(text)


def main(argv):
    app = QApplication.instance() or QApplication([])
    live = "--live" in argv

    # ---------- 一、段落拼装 ----------
    items = da.today()
    if not items:
        items = da.recent(2)
    print(f"=== 素材：{len(items)} 条收藏")
    takes = dt.get_takes(items, dt.load_key(), use_cache=True)
    segs = pet.PetWindow._take_segments(None, items, takes)
    check("一条收藏拼一段", len(segs) == len(items), f"{len(segs)} 段")
    check("每段都以第N条开头", all(s.startswith("第") for s in segs), segs[0][:24] if segs else "")
    check("每段都有书名号标题", all("《" in s and "》" in s for s in segs))
    check("每段都以句号收尾", all(s.endswith(("。", "！", "？")) for s in segs))
    check("段内没有半角逗号", all("," not in s for s in segs), "、".join(s[:30] for s in segs))
    for s in segs:
        print("   " + s)

    # ---------- 二、降级：没 key 也要有话说 ----------
    fb = dt.get_takes(items[:1], "", use_cache=False)
    one = list(fb.values())[0]
    check("无 key 走降级", one.get("src") == "fallback", str(one.get("src")))
    check("降级仍有点要", bool(one.get("point")), one.get("point", "")[:30])

    # ---------- 三、播报队列 ----------
    p = FakePet()
    p.speak_segments(["第一段。", "第二段。", "第三段。"])
    check("队列开念第一段", p.said == ["第一段。"], str(p.said))
    check("队列里还剩两段", len(p._speech_q) == 2, str(len(p._speech_q)))

    # 时间没到：不该抢话
    FAKE_TIME[0] += 1.0
    p._pump_speech()
    check("没念完不抢下一段", len(p.said) == 1, str(p.said))

    # 时间到了：接着念
    FAKE_TIME[0] += 60
    p._pump_speech()
    check("念完一段才上下一段", p.said == ["第一段。", "第二段。"], str(p.said))

    # TTS 还在出声：即使时长到了也得等
    p.tts.speaking = True
    FAKE_TIME[0] += 60
    p._pump_speech()
    check("tts 还在念就等它", len(p.said) == 2, str(p.said))
    p.tts.speaking = False
    p._pump_speech()
    check("tts 停了继续", len(p.said) == 3, str(p.said))
    check("念完队列自动清空", p._speech_q == [], str(p._speech_q))

    # 硬上限：speaking 卡住不能永远堵
    p2 = FakePet()
    p2.tts.speaking = True
    p2.speak_segments(["甲。", "乙。"])
    first = len(p2.said)
    FAKE_TIME[0] += 10000        # 远超 hard_until
    p2._pump_speech()
    check("speaking 卡死有硬线放行", len(p2.said) > first, f"{first} -> {len(p2.said)}")

    # 追加 / 替换
    p3 = FakePet()
    p3.speak_segments(["A。"])
    p3.speak_segments(["B。"], replace=False)
    check("追加模式不清空", p3._speech_q == ["B。"], str(p3._speech_q))
    p3.speak_segments(["C。"], replace=True)
    check("替换模式扔掉没念的旧段", "B。" not in p3.said and p3.said[-1] == "C。", str(p3.said))

    # 停
    p4 = FakePet()
    p4.speak_segments(["一。", "二。", "三。"])
    p4.stop_speech()
    check("stop_speech 清空队列", p4._speech_q == [], str(p4._speech_q))
    check("stop_speech 停掉声音", p4.tts.stopped is True)
    check("stop_speech 收气泡", p4.bubble_text == "")

    # 时长估算：长文不设上限
    short = pet.PetWindow._speech_seconds("一句话。")
    long_ = pet.PetWindow._speech_seconds("字" * 600)
    check("短句至少 6 秒", short >= 6.0, f"{short:.1f}s")
    check("600 字不封顶", long_ > 150, f"{long_:.0f}s")

    # ---------- 四、真调一次模型（要加 --live） ----------
    if live:
        key = dt.load_key()
        if not key:
            check("--live 需要 key", False, "deepseek_key.txt 不存在")
        else:
            probe = dict(items[0])
            probe["aweme_id"] = "live-probe-" + probe.get("aweme_id", "x")
            t0 = __import__("time").monotonic()
            got = dt.take_one(probe, key)
            cost = __import__("time").monotonic() - t0
            check("真调模型成功", got.get("src") == "llm", str(got.get("src")))
            check("真调有点要", bool(got.get("point")), got.get("point", "")[:30])
            check("真调有看法", bool(got.get("take")), got.get("take", "")[:40])
            check("单条用时合理", cost < 30, f"{cost:.1f}s")
            print(f"   模型原话：{got.get('point')} / {got.get('take')}")

    # ---------- 五、整条链路：后台拼 → 信号 → 队列逐段念 ----------
    from PySide6.QtCore import Signal

    class FakeReportPet(FakePet):
        report_ready = Signal(list)
        _build_daily_report = pet.PetWindow._build_daily_report
        _weather_line = pet.PetWindow._weather_line
        _compose_daily_report = pet.PetWindow._compose_daily_report
        _ai_closing = pet.PetWindow._ai_closing
        _take_segments = pet.PetWindow._take_segments
        _daily_report_worker = pet.PetWindow._daily_report_worker
        _on_report_ready = pet.PetWindow._on_report_ready
        WEATHER_MAP = pet.PetWindow.WEATHER_MAP
        WMO_MAP = pet.PetWindow.WMO_MAP

        def __init__(self):
            super().__init__()
            self.cfg = {"ds_api_key": ""}      # 走降级，不花钱
            self._weather_cache = None
            self._report_busy = False
            self.spoken = []
            self.report_ready.connect(self._on_report_ready)

    # 隔离「说话前想一下」：它默认会去读 C:\DaFeiYu\deepseek_key.txt，
    # 自检一旦漏过这一步就真调模型、真花钱、还真改今天的用量台账。
    # 这里换成假实现，顺带把「日报会多一段收尾」这条接线也测了。
    import say_ai as _sa
    _AI_SENTINEL = "今天先把缴费那条弄了，别的都能等。"
    _orig_state = _sa.STATE_PATH
    _sa.STATE_PATH = os.path.join(tempfile.mkdtemp(prefix="sayai-"), "state.json")
    _orig_opine = _sa.opine
    _sa.opine = lambda *a, **kw: _AI_SENTINEL

    rp = FakeReportPet()
    orig_say = rp.say

    def say_and_record(text, **kw):
        orig_say(text, **kw)
        if kw.get("speak"):
            rp.spoken.append(text)

    rp.say = say_and_record
    rp._report_busy = True
    # 日报读的是「今天」的收藏，夹具用的却是 today() 空了之后的 recent(2)。
    # 今天一条没收的时候两边就对不上，段数白差两条。这里把「今天」钉成夹具，
    # 测的是拼装逻辑，不是日切。
    _orig_today = da.today
    da.today = lambda: list(items)
    rp._daily_report_worker()          # 真跑一次拼装（含天气 4 秒上限）
    da.today = _orig_today
    app.processEvents()                # 信号回到槽
    check("日报拼完落了队列", len(rp._speech_q) >= 1 or len(rp.spoken) >= 1,
          f"队列 {len(rp._speech_q)}，已念 {len(rp.spoken)}")
    expect = 2 + len(items)            # 基础日报 + 收尾判断 + 每条收藏一段
    check("段数=日报+收尾+每条收藏", len(rp.spoken) + len(rp._speech_q) == expect,
          f"{len(rp.spoken) + len(rp._speech_q)} vs {expect}")
    check("第一段是基础日报", "月" in (rp.spoken[0] if rp.spoken else ""),
          (rp.spoken[0][:30] if rp.spoken else ""))

    # 推进假时间，把队列念完
    for _ in range(len(items) + 5):
        if not rp._speech_q:
            break
        FAKE_TIME[0] += 300
        rp._pump_speech()
        app.processEvents()
    check("队列能一路念到底", not rp._speech_q, f"剩 {len(rp._speech_q)}")
    check("每段都进了语音", len(rp.spoken) == expect, f"{len(rp.spoken)} 段")
    # 收尾那句要排在基础日报之后、点评之前：先听完「有什么」，再听「所以我怎么看」
    check("第二段是收尾判断", (rp.spoken[1] if len(rp.spoken) > 1 else "") == _AI_SENTINEL,
          (rp.spoken[1][:30] if len(rp.spoken) > 1 else ""))
    if len(rp.spoken) > 2:
        check("第三段起是收藏点评", rp.spoken[2].startswith("第1条"), rp.spoken[2][:30])
    for s in rp.spoken:
        print("   念：" + s[:70] + ("…" if len(s) > 70 else ""))

    # 还原：不还原的话，后面别的自检会拿到假 opine
    _sa.opine = _orig_opine
    _sa.STATE_PATH = _orig_state

    print()
    print(f"通过 {len(OK)} 项，失败 {len(FAIL)} 项")
    for n in FAIL:
        print("  未通过：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
