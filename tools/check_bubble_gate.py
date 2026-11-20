# -*- coding: utf-8 -*-
"""气泡总开关自检：谁都不许绕过它出字。

跑法（在 dafeiyu-pet-main 目录下）：
    ..\\venv\\Scripts\\python.exe tools\\check_bubble_gate.py

两道检查：
1. 静态——全项目扫一遍，写 self.bubble_text 的地方必须在 pet.py 的白名单函数里。
   以后谁新加模块直接往气泡里塞字，这里会红。
2. 运行——真开一个桌宠（offscreen），关掉开关后 say() 一个字都不许弹，
   但「念出台词」还得照念：气泡和声音是两件事，不能互相绑架。
"""
import os
import re
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


# ---- 1. 静态：气泡的写入点 -------------------------------------------------

# 允许写 bubble_text 的函数：初始化置空、say() 出字、手动收字、开关收字
ALLOWED = {"__init__", "say", "stop_speech", "set_bubble_on"}
# tools/ 里是一堆假 PetWindow（只提供被自检的那几个方法），它们的
# self.bubble_text 是给假对象用的字段，不是真桌宠在出字，所以整目录不扫。
SKIP_DIRS = {"venv", "__pycache__", "_deprecated", ".git", "assets_anim_v2", "tools"}


def write_sites(path):
    """返回 [(函数名, 行号, 代码行)]，只挑对 bubble_text 赋值的行。"""
    sites = []
    func = "<模块级>"
    with open(path, encoding="utf-8", errors="replace") as stream:
        for no, line in enumerate(stream, 1):
            m = re.match(r"^(\s*)def\s+([A-Za-z_]\w*)\s*\(", line)
            if m and len(m.group(1)) <= 4:
                func = m.group(2)
            if re.search(r"\bbubble_text\s*=(?!=)", line):
                sites.append((func, no, line.strip()))
    return sites


def scan(bad, allowed):
    me = os.path.abspath(__file__)
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            # 只扫真正能 import 的模块：pet.before-xxx.py 这种备份副本不算，
            # 文件名里多点一段就不是合法模块名，Python 也加载不到它。
            if not name.endswith(".py") or name.count(".") != 1:
                continue
            full = os.path.join(base, name)
            if os.path.abspath(full) == me:      # 本文件里的示例文本不算
                continue
            for func, no, line in write_sites(full):
                rel = os.path.relpath(full, ROOT)
                if rel.replace("\\", "/") == "dafeiyu-pet-main/pet.py" and func in allowed:
                    continue
                bad.append(f"{rel}:{no} {func} -> {line}")


def main():
    bad = []
    scan(bad, ALLOWED)
    check("没有模块绕过总开关直接写气泡", not bad, " | ".join(bad[:4]))

    # 反证：扫描器本身得能抓到真写入，否则「通过」是假的
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write("class X:\n    def sneak(self):\n        self.bubble_text = '偷写'\n")
        probe = tmp.name
    try:
        hits = write_sites(probe)
        check("扫描器能抓到偷写的气泡", len(hits) == 1 and hits[0][0] == "sneak", str(hits))
    finally:
        os.unlink(probe)

    # ---- 2. 运行 ------------------------------------------------------------
    from PySide6.QtWidgets import QApplication
    import pet

    folder = tempfile.mkdtemp(prefix="bubble_gate_")
    pet.CONFIG_PATH = os.path.join(folder, "config.json")
    app = QApplication.instance() or QApplication([])

    class FakeTTS:
        """不联网、不出声，只记下「谁被念了」。"""

        def __init__(self):
            self.spoken = []
            self.speaking = False

        def speak(self, text, force=False, style=None):
            self.spoken.append(text)
            return True

        def stop(self):
            pass

    old_available = pet.TTS_AVAILABLE
    pet.TTS_AVAILABLE = False
    window = pet.PetWindow()
    window.timer.stop()
    window.tts = FakeTTS()
    try:
        window.bubble_on = True
        window.say("开关开着，这句要看得见", force=True, speak=False)
        check("开着时气泡正常出字", window.bubble_text == "开关开着，这句要看得见",
              window.bubble_text)

        window.bubble_on = False
        window.bubble_text = ""
        window.say("开关关着，这句不许弹", force=True, speak=False)
        check("关掉后 force 也说不出字", window.bubble_text == "", repr(window.bubble_text))

        window.say("关掉后连桥接喂的话也得憋住", force=True, speak=True)
        check("关掉后桥接/通知那条路也憋住", window.bubble_text == "")

        check("关掉气泡不影响念出台词", window.tts.spoken != [], str(window.tts.spoken[-1:]))
        check("念的正是那句话", window.tts.spoken[-1] == "关掉后连桥接喂的话也得憋住",
              str(window.tts.spoken[-1:]))

        window.bubble_on = True
        window.say("开着", force=True, speak=False)
        check("重新打开后能出字", window.bubble_text == "开着")
        window.set_bubble_on(False)
        check("关开关立刻收掉正在显示的那条", window.bubble_text == "")
        check("开关状态落到了配置里", window.cfg.get("bubble_on") is False)
        window.set_bubble_on(True)
        check("打开后有回执气泡", window.bubble_text != "")

        # ---- 3. 吃文件的说法 -------------------------------------------------
        import file_feeding as ff

        kinds = ff.summarize(["/a/1.png", "/a/2.jpg", "/a/3.docx"])
        check("类型摘要认得出来", kinds == "2 个图片、1 个文档", kinds)
        check("不认识的算其他", ff.summarize(["/a/x.qqq"]) == "1 个其他文件",
              ff.summarize(["/a/x.qqq"]))
        check("空输入不算出错", ff.summarize([]) == "")

        lines = {ff.fed_line(3, [], "2 个图片、1 个文档")[0] for _ in range(40)}
        check("同一个数量也不是每次同一句话", len(lines) > 1, f"{len(lines)} 种")
        ok_bubble, ok_spoken = ff.fed_line(3, [], "2 个图片、1 个文档")
        check("气泡带类型摘要", "2 个图片、1 个文档" in ok_bubble, ok_bubble)
        check("念的那句不带括号", "（" not in ok_spoken, ok_spoken)
        part, _ = ff.fed_line(2, ["a.txt"], "1 个图片、1 个文档")
        check("有没吃动的要报数", "1 个没吃动" in part, part)
        none_line, _ = ff.fed_line(0, ["a.txt", "b.txt"])
        check("全失败也要有话说", "2 个" in none_line, none_line)

        fact = pet.PetWindow._fed_fact(3, ["a.txt"], "2 个图片、1 个文档", "a.png、b.jpg、c.docx")
        check("给模型的事实句带类型和失败数",
              "2 个图片" in fact and "1 个没吃动" in fact, fact)
        check("事实句带文件名（只给名字，不给内容）",
              "a.png" in fact and "c.docx" in fact, fact)
        check("事实句以句号收口", fact.endswith("。"), fact)

        # 文件夹：认得出、算得出、说得对
        check("认得文件夹", ff.is_folder(os.path.dirname(ff.__file__)) is True)
        check("摘要里分得开文件夹", ff.summarize(["/a/1.png", os.path.dirname(ff.__file__)])
              == "1 个文件夹、1 个图片",
              ff.summarize(["/a/1.png", os.path.dirname(ff.__file__)]))
        folder_line, folder_spoken = ff.fed_line(1, [], "1 个文件夹", "项目备份/")
        check("只吃一个文件夹时说文件夹名", "文件夹「项目备份」" in folder_line, folder_line)
        check("文件夹那句也念得出来", "1 个文件夹" in folder_spoken, folder_spoken)
        many_dirs = ff.fed_line(3, [], "3 个文件夹")[0]
        check("多个文件夹不套「N 个文件」", "个文件（" not in many_dirs, many_dirs)
        check("长名字会截断", len(ff.names_hint(["/a/" + "长" * 40 + ".txt"])) <= 20,
              ff.names_hint(["/a/" + "长" * 40 + ".txt"]))
        check("名字多了只报总数",
              "等 9 项" in ff.names_hint([f"/a/{i}.txt" for i in range(9)]),
              ff.names_hint([f"/a/{i}.txt" for i in range(9)]))

        # 吃完要真去后台要一句看法（走的是 say_ai 那层，不是硬编码台词）
        asked = []
        window._ai_remark = lambda fact, kind="notice", extra="": asked.append((fact, kind))
        window.tts.spoken.clear()
        window._fed_files(2, [], "2 个图片")
        check("投喂会去后台要一句看法", asked and asked[0][1] == "file", str(asked[:1]))
        check("投喂那句话是念出来的", window.tts.spoken != [], str(window.tts.spoken[-1:]))
    finally:
        window.timer.stop()
        window.tray.hide()
        window.close()
        window.deleteLater()
        app.processEvents()
        pet.TTS_AVAILABLE = old_available

    print(f"\n通过 {len(OK)} 项" + (f"，失败 {len(FAIL)} 项：{FAIL}" if FAIL else "，全过"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
