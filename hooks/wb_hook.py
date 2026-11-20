# -*- coding: utf-8 -*-
"""WorkBuddy -> 大肥鱼 事件桥。

WorkBuddy 在每个事件上调用本脚本（stdin 给一段 JSON），
本脚本把事件翻译成一句人话，推给桌上那只鱼，
鱼负责冒泡 + 出声。

设计约束：
- 永远 exit 0、永远不输出阻止性 JSON（避免把用户的对话卡住）
- 只依赖标准库，Python 3.8+ 可跑
- 鱼没开就静默退出；开了「自动拉起」则顺手把鱼叫起来
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
PET_PKG = os.path.join(BASE, "dafeiyu-pet-main")      # ding.py 在这，定时任务要直接播提示音
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DaFeiYuBridge")
ENDPOINT = os.path.join(APP_DIR, "endpoint.json")
TOKEN = os.path.join(APP_DIR, "token.txt")
CONFIG = os.path.join(BASE, "hooks", "hook_config.json")
STATE = os.path.join(BASE, "hooks", "hook_state.json")
LAUNCHER = os.path.join(BASE, "launcher.py")
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
PYW = os.path.join(BASE, "venv", "Scripts", "pythonw.exe")

DEFAULT_CFG = {
    "enabled": True,
    "events": {
        "Stop": True,
        "Notification": True,
        "UserPromptSubmit": True,
        "SessionStart": True,
        "SessionEnd": False,
        "SubagentStop": False,
    },
    "speak": True,
    "include_summary": True,
    "max_chars": 40,
    "min_turn_seconds": 20,
    "cooldown_sec": 3,
    "auto_start_pet": True,
    "auto_start_min_interval": 180,
    "send_timeout": 4,
    "memory": True,
    "log": True,
    # ---- 定时任务（automation）：只叮，不念 ----
    # TTS 是花钱的。定时任务每小时自己跑一轮，念一句「收工了：本轮无新增收藏」
    # 既没人在听，又白烧积分。所以定时任务的会话一律只播提示音：
    # 开始叮一声、结束叮两声，中途出事（Notification）只叮一声。
    "automation_mode": "ding",      # ding=只叮 / speak=照旧播报 / mute=完全静音
    "automation_open_dings": 1,     # 任务开始：叮几声
    "automation_close_dings": 2,    # 任务结束：叮几声
    # ---- 例外：中途被授权卡住，必须念 ----
    # Notification 的实际文案是「CodeBuddy is waiting for your input」，
    # 也就是要你点授权/回个话。定时任务跑到一半停在这儿，光叮一声人分辨不出
    # 是「跑完了」还是「卡着等你」，只能念一句。这句不算白烧积分。
    # 台词里可写 {detail}，会被换成通知原文里抠出来的中文细节；
    # 留空＝不念（只叮）。
    "automation_notify_speak": True,
    "automation_notify_text": "定时任务卡住了，等你点一下",
    "automation_notify_gap": 90,    # 同一次任务两次提醒的最小间隔（秒）
}

LOG = os.path.join(BASE, "hooks", "hook.log")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def log(cfg, msg):
    if not cfg.get("log"):
        return
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ---------------- 与鱼通信（一律直连 127.0.0.1，绕开沙箱代理） ----------------
def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_endpoint():
    ep = load_json(ENDPOINT, None)
    if not isinstance(ep, dict):
        return None
    tok = ""
    try:
        with open(TOKEN, encoding="utf-8") as f:
            tok = f.read().strip()
    except Exception:
        tok = ep.get("token", "")
    if not ep.get("port") or not tok:
        return None
    return {"port": ep["port"], "token": tok, "pid": ep.get("pid")}


def pet_health(ep, timeout=2.0):
    if not ep:
        return None
    req = urllib.request.Request(
        "http://127.0.0.1:%s/health" % ep["port"],
        headers={"X-Pet-Token": ep["token"]},
    )
    try:
        with _opener().open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def pet_say(ep, text, seconds, speak, timeout, anim=None):
    payload = {"text": text, "seconds": seconds, "speak": speak}
    if anim:
        payload["anim"] = anim
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%s/say" % ep["port"], data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Pet-Token": ep["token"]},
    )
    with _opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def launch_pet(cfg):
    """鱼没开时顺手叫起来；有节流，避免每次收工都试一遍。"""
    if not cfg.get("auto_start_pet"):
        return None
    state = load_json(STATE, {})
    now = time.time()
    if now - float(state.get("last_launch", 0)) < float(cfg.get("auto_start_min_interval", 180)):
        return None
    state["last_launch"] = now
    save_json(STATE, state)

    exe = PYW if os.path.exists(PYW) else PY
    if not os.path.exists(exe) or not os.path.exists(LAUNCHER):
        log(cfg, "auto_start: 缺少 %s 或 launcher.py" % exe)
        return None
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([exe, LAUNCHER], cwd=BASE, env=env,
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:
        log(cfg, "auto_start 失败: %r" % e)
        return None

    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(0.5)
        ep = read_endpoint()
        if ep and pet_health(ep, timeout=1.5):
            log(cfg, "auto_start 成功 pid=%s" % ep.get("pid"))
            return ep
    log(cfg, "auto_start 超时")
    return None


# ---------------- 摘要：从会话记录里取最后一句话 ----------------
NOISE = re.compile(r"[`*#>|\[\]]|^\s*[-+]\s*|^\s*\d+[.)]\s*")


def _clean(text):
    text = text.replace("\r", "")
    lines = []
    for ln in text.split("\n"):
        ln = NOISE.sub("", ln).strip()
        if ln:
            lines.append(ln)
    return " ".join(lines)


# 句末标点：优先在这里收尾，念出来才是「说完了」
SENT_END = "。！？!?；;"
# 停顿标点：次选，念出来是自然停顿，不算断在半句
PAUSE_END = "，,、：:"


def _shorten(text, limit=40):
    """把一段话压到 limit 字左右，但必须收在一个自然的停顿上。

    以前的写法取「limit 内最后一个任意标点」再补省略号，会把句子从逗号处
    硬砍成半句（「…缓存本来就有…」），语音念出来就像话没说完。现在：

    1. limit 内有句末标点 -> 取最后一个，干净结束
    2. 没有句末但有逗号 -> 取最后一个，保留逗号（自然停顿，不是断掉）
    3. 都没有 -> 往后多看一点（最多 limit*1.6），宁可多念十来个字也要说完整
    4. 还是没有 -> 硬切并补逗号，**不放省略号**（念省略号会有奇怪停顿）
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    head = text[:limit]
    floor = max(6, limit // 3)      # 太靠前切出来的「好。」没信息量，宁可再找

    idx = max(head.rfind(m) for m in SENT_END)
    if idx >= floor:
        return head[:idx + 1].strip()

    idx = max(head.rfind(m) for m in PAUSE_END)
    if idx >= floor:
        return head[:idx + 1].strip()

    # 往后找最近的句末标点，允许超到 limit*1.6
    tail = text[limit:int(limit * 1.6)]
    for i, ch in enumerate(tail):
        if ch in SENT_END:
            return text[:limit + i + 1].strip()

    # 硬切：以前补「，」，念出来是悬着的上扬语气，听着就像话没说完；改补句号。
    # 英文片段不能补中文标点（会念出一个莫名其妙的停顿），纯 ASCII 就直接返回。
    head = head.rstrip("。，、；：,.!?;: ")
    if not re.search(r"[\u4e00-\u9fff]", head):
        return head
    return head + "。"


# ---------------- 通知翻成中文 ----------------
# 「要人处理」的判定词，英文通知也算。
# 中文这一组必须写全：实测「需要授权」「等待授权」「请确认」都不含「确认授权」
# 或「需要你」这些子串，漏一个就会把真正的授权提示当回执静默掉。
NOTIFY_WANT = (r"waiting|permission|approve|approval|confirm|required|need you|input|"
               r"需要你|等待你|等你|需要授权|等待授权|待授权|请确认|需要确认|"
               r"请求授权|等你授权|需要批准|确认授权|点一下|卡住")

# 英文通知一律不念：TTS 念半截英文又慢又怪，听着像故障。按类型给一句纯中文
NOTIFY_KINDS = [
    (r"permission|approve|approval|confirm|授权|批准|确认", "有个授权要你点一下"),
    (r"waiting|input|需要你|等待你|等你", "我在等你回个话"),
    (r"卡住|stuck|error|失败|出错", "我这儿卡住了，等你来看看"),
]
DEFAULT_NOTIFY = "我卡住了，等你点一下"


def _cjk_only(text):
    """只留汉字和中文标点，英文词/路径/代码一律删掉。返回空串表示没有能念的中文。

    英文段直接删除而不是换成空格：中文不靠空格断句，留个空格反而会把
    「写入 pet.py 这个文件」切成「写入 这个文件」，念出来是断的。
    """
    t = re.sub(r"[A-Za-z][A-Za-z0-9_\-\./'@]*", "", text or "")
    t = re.sub(r"[^\u4e00-\u9fff，。！？；：、]", "", t)
    return t.strip("，。！？；：、 ")


def _notify_detail(cn, limit=18):
    """中文细节取「第一小句」，避免硬切出「写入 这个。」这种半句。"""
    head = re.split(r"[：，。；]", cn, 1)[0].strip()
    if 4 <= len(head) <= limit:
        return head
    return _shorten(cn, limit)


def _notify_msg(data):
    """Notification 正文洗净 + 判断要不要人处理。返回 (正文, 要不要)。

    普通会话和定时任务两处都走这里：两边判断必须一模一样，否则会出现
    「手打的时候念了、定时任务却静默」这种谁也说不清的分裂。

    正文为空时当成要处理——没有正文根本无从判断，而定时任务里漏掉一次授权
    就是卡到第二天，宁可多喊一声。
    """
    msg = _clean(str(data.get("message") or ""))
    msg = re.sub(r"\s+", " ", msg).strip()
    if not msg:
        return "", True
    return msg, bool(re.search(NOTIFY_WANT, msg, re.I))


def _notify_text(raw):
    """把 WorkBuddy 的通知变成一句能念的中文。

    Notification 正文常常是英文（`CodeBuddy is waiting for your input`），
    以前直接拼在「在等你点一下：」后面，结果既念英文又断在半句。
    """
    cn = _cjk_only(raw)
    kind = ""
    for pat, line in NOTIFY_KINDS:
        if re.search(pat, raw or "", re.I):
            kind = line
            break
    if len(cn) >= 4:
        return "在等你点一下：" + _notify_detail(cn)
    return kind or DEFAULT_NOTIFY


def _raw_user_text(rec):
    """user 消息里所有文本拼起来（含被标签包着的系统提示），用于识别脏数据。"""
    if rec.get("type") != "message" or rec.get("role") != "user":
        return ""
    parts = []
    for blk in rec.get("content") or []:
        if isinstance(blk, dict) and blk.get("type") in ("input_text", "text"):
            parts.append(blk.get("text") or "")
    return "\n".join(parts)


# 定时任务跑起来时，user 消息里塞的是系统写的任务书，不是人话。
# 主流程的 is_automation 偶尔认不出来（payload 里没带标记、会话文件太大），
# 漏一次就往记忆里塞一条「Do not create a new automation」——那种假问题
# 会一直被算成「你在忙这个」。这里是第二道闸。
FAKE_PROMPT = re.compile(
    r"automation_system_reminder|scheduled automation task|"
    r"Do not create a new automation|This is an automated scheduled", re.I)


def _real_prompt(rec):
    """区分「用户真的提了问」和系统塞进来的通知（<system-reminder>/<task-notification>）。"""
    if not _user_text(rec):
        return False
    return not FAKE_PROMPT.search(_raw_user_text(rec))


USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.S)


def _user_text(rec):
    """取用户真正问的那句话。

    WorkBuddy 的 user 消息里，正文常常被 `<user_query>` 包着，前面还堆了一串
    `<system-reminder>`。直接判断「是不是以 < 开头」会把真问题也一起扔掉，
    所以先剥标签，再取正文。
    """
    if rec.get("type") != "message" or rec.get("role") != "user":
        return ""
    for blk in rec.get("content") or []:
        if not isinstance(blk, dict) or blk.get("type") not in ("input_text", "text"):
            continue
        t = (blk.get("text") or "").strip()
        if not t:
            continue
        m = USER_QUERY.search(t)
        if m:
            return _clean(m.group(1))[:600]
        if not t.startswith("<"):
            return _clean(t)[:600]
    return ""


# 过渡句里必有的词：出现即判定「这话是在说要去干什么」，不是干完了。
# 判据只扫开头 12 字——结论后面顺手带一句「我先把日志翻一下」不该整段作废。
TRANSITION_WORDS = (
    "我先", "让我", "我这就", "先看", "先查", "先读", "先确认", "先验证",
    "接下来", "稍等", "正在", "下面我", "现在去", "好的，我这就",
)


def _is_substantive(text):
    """这一段是结论，还是「我这就去查」那类过程话。

    以前取「最后一条 assistant 消息」，实测经常拿到「先验证一下损失有多大：
    我把真实会话回放一遍」——这是过程，不是结果。记进记忆等于记了句废话。
    踩过的坑：一开始拿长度当判据（短句一律跳过），结果「结构通了。」这种
    真结论也被扔掉。结论本来就短，所以只看开头有没有过渡词、结尾在不在引下文。
    """
    t = (text or "").strip()
    if len(t) < 4:          # 「好」「嗯」这种不算结论，但「结构通了。」要算
        return False
    if t.endswith(("：", ":", "，", ",")):
        return False
    head = t[:12]
    return not any(w in head for w in TRANSITION_WORDS)


def analyze_turn(path, max_chars=40):
    """从会话记录尾部回推这一轮：最后一段结论、这一轮多久、调了几次工具、你说了什么。

    返回 (summary, turn_seconds, tool_calls, user_text)。拿不到就给保守值。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 600 * 1024:
                f.seek(size - 600 * 1024)
                f.readline()
            raw = f.read().decode("utf-8", "ignore")
    except Exception:
        return "", 0.0, 0, ""

    summary = ""
    fallback = ""
    user_text = ""
    start_ts = 0
    last_ts = 0
    tools = 0
    lines = raw.splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        t = rec.get("type")
        if t == "message" and rec.get("role") == "assistant":
            last_ts = last_ts or int(rec.get("timestamp") or 0)
            if not summary:
                parts = []
                for blk in rec.get("content") or []:
                    if isinstance(blk, dict) and blk.get("type") in ("output_text", "text"):
                        parts.append(blk.get("text") or "")
                text = _clean(" ".join(parts))
                text = re.sub(r"\s+", " ", text).strip()
                if text and not fallback:
                    fallback = _shorten(text, max_chars)   # 全是过渡话时的兜底
                if text and _is_substantive(text):
                    summary = _shorten(text, max_chars)
        elif t == "function_call":
            tools += 1
        elif _real_prompt(rec):
            start_ts = int(rec.get("timestamp") or 0)
            user_text = _user_text(rec)
            break
    summary = summary or fallback
    dur = (last_ts - start_ts) / 1000.0 if (start_ts and last_ts and last_ts >= start_ts) else 0.0
    return summary, dur, tools, user_text


def remember(text, cfg, ref="", user_text=None, did=None, dur=0.0, tools=0):
    """把这一轮提炼成一个短话题存起来，供桌宠碎碎念用。失败绝不影响主流程。

    ref 是来源标注（会话文件名），只用于回溯「这条记忆哪来的」，不存正文。
    did 是这一轮的结论，会作为事件卡挂到话题下面——话题说明「在忙什么」，
    卡说明「那次到底干了啥」。
    """
    if not cfg.get("memory", True) or not text:
        return None
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        from pet_memory import remember_turn
        return remember_turn(text, ref=ref, user_text=user_text,
                             did=did, dur=dur, tools=tools)
    except Exception as e:
        log(cfg, "话题记忆失败: %r" % e)
        return None


# ---------------- 定时任务（automation）判定 ----------------
# 定时任务跑起来时，WorkBuddy 会在会话首条用户消息里塞一段标记：
#   <automation_system_reminder>This is a scheduled automation task ...
# 拿它当指纹。三个来源依次试，命中一个就算：
#   1. payload 全文（UserPromptSubmit 会带 prompt，里面就有）
#   2. payload 里的 transcript_path（SessionStart 时文件里已经写好了首条消息）
#   3. 用 cwd + session_id 反推会话文件路径（本机实测能对上）
AUTO_MARKS = ("automation_system_reminder", "scheduled automation task")
PROJECTS = os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")


def _transcript_candidates(data):
    p = data.get("transcript_path") or data.get("transcriptPath") or ""
    if p:
        yield p
    sid = data.get("session_id") or data.get("sessionId") or ""
    cwd = data.get("cwd") or ""
    if sid and cwd:
        # c:\Users\ASUS\WorkBuddy\2026-11-01-12-41-47 -> c-Users-ASUS-...
        slug = re.sub(r"[^A-Za-z0-9]+", "-", cwd).strip("-")
        yield os.path.join(PROJECTS, slug, "%s.jsonl" % sid)


def is_automation(data):
    """这条事件是不是定时任务发出来的。"""
    try:
        if any(m in json.dumps(data, ensure_ascii=False) for m in AUTO_MARKS):
            return True
    except Exception:
        pass
    for path in _transcript_candidates(data):
        if not (path and os.path.exists(path)):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                head = f.read(40000)          # 首条消息就在最前面，不用读全
        except Exception:
            continue
        if any(m in head for m in AUTO_MARKS):
            return True
    return False


def _auto_first(state, sid, kind, window=30):
    """同一个定时任务会话里，「开始」「结束」各只提示一次。

    SessionStart 和 UserPromptSubmit 都会触发开始，不去重就会连着叮两声。
    拿不到 session_id 时退化成时间窗口——绝不能用一个固定的「unknown」键
    永久占位，那样第二次定时任务就不会再响了。
    """
    auto = state.setdefault("auto", {})
    if not sid:
        if time.time() - float(auto.get("last_" + kind, 0)) < window:
            return False
        auto["last_" + kind] = round(time.time(), 1)
        return True
    sess = auto.setdefault("sessions", {})
    rec = sess.setdefault(sid, {})
    if rec.get(kind):
        return False
    rec[kind] = round(time.time(), 1)
    if len(sess) > 20:                        # 只留最近 20 个会话
        old = sorted(sess.items(), key=lambda kv: max(kv[1].values() or [0]))
        for k, _ in old[:-20]:
            sess.pop(k, None)
    return True


def _auto_notify_ok(state, sid, gap):
    """同一个定时任务里，两次「卡住了」提醒至少隔 gap 秒。

    Notification 会连发好几次（实测一条授权能来三遍），所以不能用「一辈子
    只提醒一次」的去重——用户处理完授权、任务又卡第二次就永远不响了。
    按时间间隔放行才对，和上面 _auto_first 的语义正好相反。
    """
    auto = state.setdefault("auto", {})
    now = time.time()
    gap = max(5.0, float(gap or 90))
    if not sid:
        if now - float(auto.get("last_notify", 0)) < gap:
            return False
        auto["last_notify"] = round(now, 1)
        return True
    ts = auto.setdefault("notify_ts", {})
    if now - float(ts.get(sid, 0)) < gap:
        return False
    ts[sid] = round(now, 1)
    if len(ts) > 20:
        for k, _ in sorted(ts.items(), key=lambda kv: kv[1])[:-20]:
            ts.pop(k, None)
    return True


def pet_muted():
    """鱼是不是被一键静音了。

    hook 是独立进程，播「叮」不经过鱼，所以得自己读一眼配置——
    否则用户按了静音，任务完成的叮当声照响，等于没静。
    读不到就当没静音（宁可响一声，也别因为路径变了彻底没提示）。
    """
    try:
        import json
        with open(os.path.join(PET_PKG, "config.json"), encoding="utf-8") as f:
            return bool(json.load(f).get("mute_all", False))
    except Exception:
        return False


def play_ding(times, cfg):
    """只播提示音：不走 TTS、不冒泡、也不把鱼叫起来。

    block=True 是必须的：hook 播完就退出进程，异步播的声音会被一起掐掉。
    """
    if pet_muted():
        log(cfg, "静音中，跳过提示音")
        return False
    try:
        if PET_PKG not in sys.path:
            sys.path.insert(0, PET_PKG)
        import ding as d
        return d.play_times(int(times), block=True)
    except Exception as e:
        log(cfg, "提示音失败: %r" % e)
        return False


def speak_to_pet(cfg, text, seconds, anim=None, speak=None):
    """把一句话送到鱼那儿（鱼没开就顺手拉起来）。返回 (成功?, 详情)。

    定时任务用得到：那条鱼可能根本没开，而「卡住了」这句正是要叫人回来的，
    所以这里必须容忍冷启动，不能只在鱼已经活着时才张嘴。
    """
    if speak is None:
        speak = bool(cfg.get("speak", True))
    try:
        ep = read_endpoint()
        if not (ep and pet_health(ep)):
            ep = launch_pet(cfg)
        if not ep:
            log(cfg, "未送达：鱼没在跑（%s）" % text)
            return False, "鱼没在跑"
        resp = pet_say(ep, text, seconds, speak,
                       float(cfg.get("send_timeout", 4)), anim=anim)
        log(cfg, "送达: %s | %s" % (text, json.dumps(resp, ensure_ascii=False)))
        return True, resp
    except Exception as e:
        log(cfg, "发送异常: %r" % e)
        return False, repr(e)


def _auto_notify_line(raw, cfg):
    """定时任务中途卡住时念的那句。返回空串＝不念（面板把语音提醒关了）。"""
    if not cfg.get("automation_notify_speak", True):
        return ""
    tpl = str(cfg.get("automation_notify_text") or "").strip()
    if not tpl:
        return ""
    if "{detail}" in tpl:
        detail = _notify_text(raw) if raw else ""
        tpl = tpl.replace("{detail}", detail).strip()
        if tpl and tpl[-1] not in SENT_END:
            tpl += "。"
    return tpl


# ---------------- 事件 -> 台词 ----------------
# 开工词：固定几句轮换，短、省 TTS、贴合大肥鱼「务实有点丧」的性格。
# 每次开工随机挑一句，不会像单一固定句那样让人听腻。
WORK_OPEN_LINES = (
    "开工了，活儿来了",
    "来了来了，又开工",
    "行，我盯着，你忙你的",
    "干活，别磨蹭",
    "接活了，一件接一件",
)


def _work_open_line():
    """随机挑一句开工词。"""
    return random.choice(WORK_OPEN_LINES)


# 收工得意表演动作：避开 work_ 前缀（否则会被 pet 判成工作动画、又进工作循环），
# 挑纯「庆祝/炫耀」类的，收工时让鱼显摆一下。
PROUD_ANIMS = ("dance_sway", "dance_maid", "dance_cute", "spin",
               "juggle_balls", "curtsey")


def _proud_anim():
    """随机挑一个收工得意动作。"""
    return random.choice(PROUD_ANIMS)


def compose(event, data, cfg, state=None):
    """返回 (text, seconds, note)；note 用于日志，text 为 None 表示跳过。"""
    mx = int(cfg.get("max_chars", 40))
    if event == "Stop":
        path = data.get("transcript_path") or ""
        summary = ""
        user_text = ""
        dur = 0.0
        tools = 0
        if path and os.path.exists(path):
            summary, dur, tools, user_text = analyze_turn(path, mx)
        # 有 UserPromptSubmit 记下的开场时间就以它为准，比回推更准
        start = float((state or {}).get("turn_start") or 0)
        if start and 0 < time.time() - start < 1800:
            dur = time.time() - start
        # 随口聊两句不算「任务结束」，别吵人
        need = float(cfg.get("min_turn_seconds", 20))
        if dur and dur < need and tools == 0:
            return None, 0, "轮次太短(%.0fs/%d次工具)，不提醒" % (dur, tools)
        # 你问的才算「你在干什么」，所以你的原话排在摘要前面。
        # 没有真问题（定时任务、系统事件）就不记——以前拿摘要顶着，
        # 于是「本次无新增收藏」天天占着话题榜，把「抖音内容」刷成 82 次。
        topic = None
        if user_text:
            topic = remember((user_text + " " + summary).strip(), cfg,
                             ref=os.path.basename(path) if path else "", user_text=user_text,
                             did=summary, dur=dur, tools=tools)
        if cfg.get("include_summary") and not summary:
            summary = ""
        text = ("收工了：" + summary) if summary else "这一轮干完了，结果在右边"
        note = "%.0fs/%d次工具" % (dur, tools)
        if topic:
            note += " 记下话题:「%s」" % topic
        return text, 9, note, _proud_anim()
    if event == "Notification":
        msg, want = _notify_msg(data)
        # 只播「真的要人处理」的通知，auth_success 这类回执别念出来
        if not want:
            return None, 0, "通知不像要人处理，跳过：%s" % msg[:30]
        return _notify_text(msg), 12, "原文: %s" % (msg[:40] or "(空)")
    if event == "UserPromptSubmit":
        # 你开口交代任务的那一刻 = 大肥鱼进入「干活」状态。
        # 开工词用固定几句轮换（省 TTS、又不显得每次一模一样），
        # 配工作类动画（anim 一路传给 /say，让鱼演起来）。
        text = _work_open_line()
        if cfg.get("memory", True) and isinstance(data.get("prompt"), str):
            try:
                from pet_memory import load_mem
                from pet_growth import recall
                query = _user_text({"type": "message", "role": "user",
                                    "content": [{"type": "text", "text": data["prompt"]}]})
                related = recall(load_mem(), query)
                if related:
                    text = f"记得「{related}」。开工。"
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log(cfg, "开工回忆读取失败：%r" % exc)
        return text, 6, "开工 anim=work_busy", "work_busy"
    if event == "SessionStart":
        # 开工提醒只说「开工」本身。手机连接 / 监听是 phone_notify.py
        # 独立模块的职责，不在开工时反复提示，别把两件事搅在一起。
        return _work_open_line(), 6, "开工 anim=work_busy", "work_busy"
    if event == "SessionEnd":
        return "今天就到这儿，收工", 7, ""
    if event == "SubagentStop":
        return "子任务回来了", 5, ""
    return None, 0, "未知事件"


def main(argv):
    raw = ""
    if not sys.stdin.isatty():
        try:
            # 必须按字节读再显式 utf-8 解码：Windows 下 sys.stdin 默认 cp936，
            # 会把 UTF-8 的中文读成乱码（实测 "正在授权" -> "姝ｅ湪鎺堟潈"）
            buf = getattr(sys.stdin, "buffer", None)
            if buf is not None:
                raw = buf.read().decode("utf-8", "replace")
            else:
                raw = sys.stdin.read()
        except Exception:
            raw = ""

    data = {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    event = (data.get("hook_event_name") or data.get("event") or "").strip()
    test_mode = "--test" in argv
    if test_mode:
        args = [a for a in argv if not a.startswith("--")]
        if args:
            event = args[0]
    if not event:
        print("{}")
        return 0

    cfg = load_json(CONFIG, DEFAULT_CFG)
    if event == "SessionStart":
        # 记一回 payload 字段名，以后「为什么没认出定时任务」有据可查
        log(cfg, "SessionStart payload 键: %s" % ",".join(sorted(data)))
    if not cfg.get("enabled", True):
        log(cfg, "%s 忽略（总开关关闭）" % event)
        print("{}")
        return 0
    if not (cfg.get("events") or {}).get(event, False):
        log(cfg, "%s 忽略（未开启）" % event)
        print("{}")
        return 0

    # 冷却：同一事件短时间内只提醒一次
    state = load_json(STATE, {})
    now = time.time()

    # ---- 定时任务：只叮，不念 ----
    # 定时任务每小时自己跑一轮，念给谁听？纯烧 TTS 积分。所以整段拦在这里，
    # 用提示音代替人声：开始叮一声、结束叮两声、中途出事叮一声。
    if is_automation(data):
        mode = str(cfg.get("automation_mode", "ding"))
        sid = str(data.get("session_id") or data.get("sessionId") or "")
        if mode == "mute":
            log(cfg, "%s 定时任务[%s] 静音模式" % (event, sid[:8]))
            print("{}")
            return 0
        if mode == "ding":
            # 例外：中途被授权/等输入卡住。这句必须念——没人守着的时候，
            # 一声「叮」根本分不出是「跑完了」还是「卡着等你点」。
            if event == "Notification":
                msg, want = _notify_msg(data)
                if not want:
                    log(cfg, "%s 定时任务[%s] 通知不用处理，跳过：%s"
                        % (event, sid[:8], msg[:30]))
                    print("{}")
                    return 0
                if not _auto_notify_ok(state, sid,
                                       cfg.get("automation_notify_gap", 90)):
                    log(cfg, "%s 定时任务[%s] 刚提醒过，跳过" % (event, sid[:8]))
                    print("{}")
                    return 0
                save_json(STATE, state)
                play_ding(1, cfg)
                line = _auto_notify_line(msg, cfg)
                if line:
                    speak_to_pet(cfg, line, 10)
                    log(cfg, "%s 定时任务[%s] 叮 1 声 + 念「%s」"
                        % (event, sid[:8], line))
                else:
                    log(cfg, "%s 定时任务[%s] 叮 1 声（中途语音提醒已关）"
                        % (event, sid[:8]))
                print("{}")
                return 0
            times = 0
            if event in ("SessionStart", "UserPromptSubmit"):
                if _auto_first(state, sid, "open"):
                    times = int(cfg.get("automation_open_dings", 1))
            elif event == "Stop":
                if _auto_first(state, sid, "close"):
                    times = int(cfg.get("automation_close_dings", 2))
            save_json(STATE, state)
            if times > 0:
                play_ding(times, cfg)
                log(cfg, "%s 定时任务[%s] 叮 %d 声（不念）" % (event, sid[:8], times))
            else:
                log(cfg, "%s 定时任务[%s] 已提示过，跳过" % (event, sid[:8]))
            print("{}")
            return 0
        log(cfg, "%s 定时任务[%s] 照旧播报（automation_mode=speak）" % (event, sid[:8]))

    # 用户开口的那一刻记下时间，收工时才知道这一轮干了多久
    if event == "UserPromptSubmit":
        state["turn_start"] = now
        save_json(STATE, state)

    # 「开工」这件事全局只念一次：UserPromptSubmit（你点提交）和 SessionStart
    # （会话初始化）都会触发开工词，WorkBuddy 多会话启动时 SessionStart 会连发
    # 好几次，不统一闸门就会一口气念好几篇。两者共用一个开工冷却键。
    last = state.setdefault("last", {})
    if event in ("UserPromptSubmit", "SessionStart"):
        if not test_mode and now - float(last.get("open", 0)) < float(
                cfg.get("min_turn_seconds", 20)):
            log(cfg, "%s 开工冷却中，只记时间" % event)
            print("{}")
            return 0
        last["open"] = now
        save_json(STATE, state)
        log(cfg, "%s 记下开工时间" % event)
    else:
        # 其余事件走通用冷却：同一事件短时间内只提醒一次
        if not test_mode and now - float(last.get(event, 0)) < float(
                cfg.get("cooldown_sec", 3)):
            log(cfg, "%s 冷却中跳过" % event)
            print("{}")
            return 0
        last[event] = now
        save_json(STATE, state)

    made = compose(event, data, cfg, state)
    if not made or not made[0]:
        log(cfg, "%s 跳过：%s" % (event, made[2] if made else "无台词"))
        print("{}")
        return 0
    text, seconds, note = made[:3]
    anim = made[3] if len(made) > 3 else None

    speak = None
    if event == "Stop":
        try:
            from pet_memory import MEM_PATH
            from pet_growth import quiet_stop
            if quiet_stop(MEM_PATH):
                speak = False
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log(cfg, "收工偏好读取失败：%r" % exc)
    ok, detail = speak_to_pet(cfg, text, seconds, anim, speak=speak)
    if ok:
        log(cfg, "%s 已送达[%s]" % (event, note))
    else:
        log(cfg, "%s 未送达[%s]：%s" % (event, note, detail))
    if test_mode:
        print(("OK -> %s" % text) if ok else ("FAIL %s" % detail))
    print("{}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        print("{}")
        sys.exit(0)
