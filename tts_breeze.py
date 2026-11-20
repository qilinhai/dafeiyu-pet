"""BreezeBlue 语音合成接入：本地缓存优先，后台单线程按序合成，主线程排队播放。

设计要点：
- **先查本地缓存，命中一元钱都不花**：缓存 key = sha1(音色|语速|文本)，
  同一句话（同音色同语速）永远只请求一次 API；
- 一句话里有一段没命中就整句走合成队列（保序）；全部命中就直接进播放队列，
  不排队、不合成，开播只要几十毫秒；
- **合成串行且保序**：说 A 再说 B，就一定先合成 A。以前每句起一个线程，
  B 抢在 A 前面合成完就会把刚开口的 A 打断 —— 这是「话说不全」的主因；
- **播放排队不打断**：一句播完才播下一句；
- 同一段音频：正在播就不重来；6 秒内重复触发只播一次（只对后台自语生效，
  你主动点的「试听 / 说句话」用 force=True 绕过去，想听几遍听几遍）；
- 重复播同一段时播放器不重新加载文件（setPosition(0) 即可），零开销；
- 缓存按「最近播放时间」淘汰（命中会刷新时间戳），常用的句子不会被挤掉；
- 超过 MAX_CHARS 的长句按标点切成几段，逐段合成逐段播，不会被硬砍；
- 合成失败只记 last_error，不影响气泡显示——鱼不能因为没网就不说话。
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import sys
import threading
import time

import requests
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

API_BASE = "https://api.breeze.blue/v1"

# 念法配置（voice_style.py）在上层目录：桌宠进程通常已经把 C://DaFeiYu
# 放进 sys.path，但独立跑自检脚本时没有，这里补一手。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

# 直连会话：不读 HTTP_PROXY/HTTPS_PROXY 等环境变量。
# 原来 requests 默认 trust_env=True，系统挂了代理（如 127.0.0.1:3373）时
# 请求会被强制绕一圈；代理软件一关，鱼就彻底哑了。实测 api.breeze.blue
# 在国内可直连，所以优先直连，直连不通再退回环境代理（见 _post_once）。
_DIRECT = requests.Session()
_DIRECT.trust_env = False

GUIDANCE_MIN, GUIDANCE_MAX = 1.0, 10.0   # 接口硬区间，超了会 422
DEFAULT_VOICE = "voc_rn489h83rcvs"      # 大肥鱼（偶像版，克隆音）
MAX_CHARS = 150                          # 单段最多念这么多字，防手滑烧额度
SEG_CHARS = 150                          # 拆段时的目标长度（按标点优先）
MAX_SEGS = 4                             # 一句话最多拆成几段，防长文烧额度
MAX_QUEUE = 6                            # 待播队列上限，超了丢最旧的
DEDUP_SEC = 6.0                          # 后台自语：同一段这么短时间内不重复
MAX_CACHE_FILES = 800                    # 缓存上限（约 80MB），按最近播放淘汰
MIN_CACHE_BYTES = 1024                   # 小于这个当坏文件，删掉重合成

# 账号里的中文音色（取自 GET /v1/voices，language_code=zh）
VOICES = [
    ("大肥鱼（偶像版）", "voc_rn489h83rcvs"),
    ("大肥鱼（机灵版）", "voc_u3nxkd5578wc"),
    ("大肥鱼（迷糊版）", "voc_8yud7hz7c9q2"),
    ("哈基米娘", "voc_rh6e8hdbraz6"),
    ("香农小姐", "voc_b86bfwct4cjn"),
    ("知绘小姐", "voc_rc894j8q2auu"),
    ("深思先生", "voc_reys5un5t4ed"),
]

_SOFT = "，、,：:— "


def split_segments(text):
    """长句按标点切段；短句原样返回。保证每段 <= MAX_CHARS。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= SEG_CHARS:
        return [text]
    # 先按句末标点切，再打包到 SEG_CHARS 以内
    parts = re.split(r"(?<=[。！？；!?;…])", text)
    out, buf = [], ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) <= SEG_CHARS:
            buf += p
            continue
        if buf:
            out.append(buf)
        buf = p
        while len(buf) > MAX_CHARS:          # 单句仍超长 -> 退到逗号处切
            cut = max(buf.rfind(c, MAX_CHARS // 2, MAX_CHARS) for c in _SOFT)
            cut = cut if cut > 0 else MAX_CHARS
            out.append(buf[:cut].rstrip(_SOFT))
            buf = buf[cut:].lstrip(_SOFT)
    if buf.strip():
        out.append(buf.strip())
    out = [s for s in out if s.strip()]
    return out[:MAX_SEGS]


class BreezeTTS(QObject):
    """一个进程一个实例；speak() 随时可调，线程安全。"""

    _ready = Signal(str, str, bool)   # (mp3 路径, 原文, 是否强制播)

    def __init__(self, cfg, cache_dir, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.cache_dir = cache_dir
        self._session = None                   # 自检可注入假 session；None=用 _DIRECT
        self.last_error = ""
        self.speaking = False
        self.queued = 0                        # 待播段数（给界面看）

        # 省钱的账本
        self.cache_hits = 0                    # 靠缓存省下的次数（本进程内）
        self.cache_misses = 0
        self.api_calls = 0                     # 真正花钱的请求数
        self.played_segs = 0

        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception as e:
            print("[tts] 缓存目录建不了:", e)

        self.out = QAudioOutput(self)
        self.out.setVolume(self._volume())
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.out)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media)
        self._ready.connect(self._play)

        # 跟随系统默认输出设备。
        # QAudioOutput 在构造时就把设备定死了，之后在 Windows 里换默认设备它不会跟。
        # 之前插着蓝牙耳机时创建，之后切到音响，就一直往蓝牙塞，听着就是「没声音」。
        # 三个时机重新绑：建好立刻一次、设备列表变化时、每次开播前再对一次。
        self._devices = QMediaDevices(self)
        try:
            self._devices.audioOutputsChanged.connect(self._on_outputs_changed)
        except Exception as e:
            print("[tts] 设备变化监听不可用:", e)
        self._bind_default_output()

        # 合成队列：单线程 FIFO，保证「谁先说要先出声」
        self._synth_q = queue.Queue()
        threading.Thread(target=self._synth_loop, daemon=True).start()

        # 播放队列（主线程用）
        self._play_q = []          # [path, ...]
        self._cur = ""             # 正在播的 path
        self._last_source = ""     # 播放器当前加载的 path（重复播时免重载）
        self._recent = {}          # path -> 上次开播时间
        self._watch = None         # 卡住时的兜底定时器

    # ---------- 配置 ----------
    def _volume(self):
        try:
            return min(max(float(self.cfg.get("tts_volume", 0.9)), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.9

    def set_volume(self, v):
        self.cfg["tts_volume"] = min(max(float(v), 0.0), 1.0)
        self.out.setVolume(self._volume())

    # ---------- 输出设备 ----------
    def _bind_default_output(self):
        """把播放器绑到系统当前的默认输出设备。换过了返回 True。"""
        try:
            want = QMediaDevices.defaultAudioOutput()
        except Exception as e:
            print("[tts] 读默认设备失败:", e)
            return False
        if want is None or want.isNull():
            return False
        try:
            cur = self.out.device()
            if cur is not None and not cur.isNull() and cur.id() == want.id():
                return False
        except Exception:
            pass
        try:
            # 播放中直接换设备不生效，先停住
            if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                self.player.stop()
            self.out.setDevice(want)
            print(f"[tts] 输出设备 -> {want.description()}")
            return True
        except Exception as e:
            print("[tts] 切换输出设备失败:", e)
            return False

    def _on_outputs_changed(self):
        """系统里插拔了设备 / 改了默认设备。"""
        # 正在念的时候别打断，这句念完 _pump 会重新对一次
        try:
            if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                return
        except Exception:
            pass
        self._bind_default_output()

    def current_output_name(self):
        try:
            return self.out.device().description()
        except Exception:
            return "?"

    @property
    def key(self):
        """当前生效的 key（取第一个非空）。"""
        return self.keys[0] if self.keys else ""

    @property
    def keys(self):
        """支持多个 key（逗号/换行分隔）混着用，轮流消耗额度。

        读出来去重、去空，顺序即轮询顺序。单 key 时行为与原来完全一致。
        """
        raw = str(self.cfg.get("tts_key") or "")
        seen, out = set(), []
        for k in re.split(r"[,，\n\r]+", raw):
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    @property
    def voice_id(self):
        return str(self.cfg.get("tts_voice") or DEFAULT_VOICE)

    @property
    def voice_name(self):
        for name, vid in VOICES:
            if vid == self.voice_id:
                return name
        return self.voice_id

    @property
    def enabled(self):
        return bool(self.cfg.get("tts_enabled", True)) and bool(self.key)

    @property
    def speed(self):
        """语速：1.0 原速。实测 1.5 时长约降到 72%，1.15 听起来更像活人。"""
        try:
            return min(max(float(self.cfg.get("tts_speed", 1.15)), 0.5), 2.0)
        except (TypeError, ValueError):
            return 1.15

    # ---------- 播放状态 ----------
    def _on_state(self, st):
        try:
            self.speaking = st == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            self.speaking = False

    def _on_media(self, st):
        """播完 -> 立刻接下一段；坏文件 -> 跳过。"""
        try:
            if st == QMediaPlayer.MediaStatus.EndOfMedia:
                self._finish_current()
            elif st == QMediaPlayer.MediaStatus.InvalidMedia:
                self.last_error = "InvalidMedia（音频文件读不了）"
                print("[tts]", self.last_error)
                self._finish_current()
        except Exception:
            pass

    def _finish_current(self):
        self._cur = ""
        self._cancel_watch()
        # 稍等一点再起下一段，避免在信号回调里重入 player
        QTimer.singleShot(120, self._pump)

    def _cancel_watch(self):
        if self._watch is not None:
            try:
                self._watch.stop()
            except Exception:
                pass
            self._watch = None

    def position_ms(self):
        try:
            return int(self.player.position())
        except Exception:
            return 0

    def duration_ms(self):
        try:
            return int(self.player.duration())
        except Exception:
            return 0

    def stop(self):
        """抢占式停止：清空待播，立刻静音。换音色/关语音时用。"""
        self._play_q.clear()
        self._cur = ""
        self.queued = 0
        self._cancel_watch()
        try:
            self.player.stop()
        except Exception:
            pass
        self.speaking = False

    # ---------- 缓存 ----------
    def _cache_path(self, voice, text, instructions="", guidance=0):
        """缓存 key = 音色|语速|表演指令|指导力度|文本。

        表演参数必须算进来：只改 instructions 不改文本时，
        key 不变就会一直放旧音频，看起来像「调了没效果」。
        代价是首次全量重建缓存（约 746 个文件），一次性。
        """
        # 语速量化到两位小数：避免 1.1500000001 这种浮点抖动让缓存全部失效
        ins = (instructions or "").strip()
        try:
            g = round(float(guidance or 0), 2)
        except (TypeError, ValueError):
            g = 0.0
        raw = f"{voice}|{round(self.speed, 2)}|{g}|{ins}|{text}"
        h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.cache_dir, h + ".mp3")

    def _cache_ok(self, path):
        """有文件、且大小正常。空的/截断的当没有，删掉重合成。"""
        try:
            if not os.path.exists(path):
                return False
            if os.path.getsize(path) < MIN_CACHE_BYTES:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return False
            return True
        except OSError:
            return False

    def _touch(self, path):
        """命中就刷新时间戳，这样淘汰时不会把常用的句子挤掉。"""
        try:
            os.utime(path, None)
        except OSError:
            pass

    # ---------- 合成 ----------
    def _style(self, style):
        """把「类别名」或现成字典解析成这一次的念法。

        查不到配置就退成纯念（instructions 空、guidance 0），
        宁可念得平，也不能因为少个配置就哑掉。
        """
        st, lang = {}, ""
        if isinstance(style, dict):
            st = style
            lang = str(style.get("language") or "")
        else:
            kind = str(style or "")
            try:
                import voice_style as ST
                if kind:
                    st = ST.style_for(kind)
                lang = ST.language()
            except Exception:
                st, lang = {}, ""
        try:
            g = float(st.get("guidance") or 0)
        except (TypeError, ValueError):
            g = 0.0
        return {
            "instructions": str(st.get("instructions") or "").strip(),
            "guidance": min(max(g, GUIDANCE_MIN), GUIDANCE_MAX) if g > 0 else 0.0,
            "language": lang,
        }

    def speak(self, text, blocking=False, force=False, style=None):
        """说一句话。

        force=True：你主动要它说（试听/菜单/桥接），允许重复播同一句。
        force=False：后台自语，6 秒内同一段不重复，防止刷屏。
        style：类别名（money/chat/urgent…）或现成的念法字典。
               同一副嗓子按场景换情绪，解决「毒舌台词用甜腔念」的问题。
        返回 True 表示已接收（不代表一定出声，可能命中去重）。
        """
        text = " ".join(str(text or "").split())
        if not text or not self.enabled:
            return False
        segs = split_segments(text)
        if not segs:
            return False

        st = self._style(style)
        voice = self.voice_id
        paths = [self._cache_path(voice, s, st["instructions"], st["guidance"])
                 for s in segs]
        hits = [self._cache_ok(p) for p in paths]

        if all(hits):
            # 全命中：完全不碰网络，直接进播放队列
            for p, s in zip(paths, segs):
                self.cache_hits += 1
                self._touch(p)
                self._ready.emit(p, s, force)
        else:
            # 有一段没缓存：整句走合成队列，保住顺序；
            # 已缓存的那几段在 _work 里会命中，不会重复请求
            for s in segs:
                self._synth_q.put((s, force, st))

        if blocking:
            self._synth_q.join()
        return True

    def _synth_loop(self):
        while True:
            item = self._synth_q.get()
            try:
                if isinstance(item, tuple):
                    if len(item) >= 3:
                        text, force, st = item[0], item[1], item[2]
                    else:
                        text, force = item
                        st = None
                else:                       # 兼容直接塞字符串的旧调用
                    text, force, st = item, False, None
                self._work(text, force, st)
            except Exception as e:
                self.last_error = "%s: %s" % (type(e).__name__, e)
                print("[tts] 合成线程异常:", self.last_error)
            finally:
                self._synth_q.task_done()

    def _work(self, text, force=False, st=None):
        try:
            st = st or {}
            ins = st.get("instructions", "")
            gd = st.get("guidance", 0.0)
            lang = st.get("language", "")
            voice = self.voice_id
            path = self._cache_path(voice, text, ins, gd)

            if self._cache_ok(path):             # 排队期间已有人合成好了
                self.cache_hits += 1
                self._touch(path)
                self._ready.emit(path, text, force)
                return

            self.cache_misses += 1
            self.api_calls += 1
            r = self._post_with_retry(voice, text, ins, gd, lang)
            if r is None or r.status_code != 200 or not r.content:
                code = r.status_code if r is not None else "?"
                body = r.text[:140] if r is not None else "no response"
                # 422 参数不对，401 Key 失效，402 多为额度不足
                self.last_error = f"HTTP {code} {body}"
                print("[tts] 合成失败:", self.last_error)
                self._ready.emit("", text, force)
                return
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, path)      # 原子落盘，别让播放读到半个文件
            self._prune()
            self.last_error = ""
            self._ready.emit(path, text, force)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            print("[tts] 合成异常:", self.last_error)
            self._ready.emit("", text, force)

    def _post_once(self, voice, key, text, instructions="", guidance=0, language=""):
        """发一次合成请求：直连优先，直连不通再退回系统代理。

        除了 text 和 speed，还带三样以前完全没用的东西：
        - language_code：显式 "zh" 才会用中文专用模型 breeze-tts-2，
          省略就会落到多语言模型上（现在就是这样）
        - instructions：自然语言表演指令，说「怎么念」
        - guidance_scale：指令的生效强度，1.0 等于几乎不听，表现力场景 1–3 起
        空值一律不写进请求体——空串会被当成「有指令但空白」。
        """
        url = f"{API_BASE}/text-to-speech/{voice}"
        vs = {"speed": self.speed}
        try:
            g = float(guidance or 0)
        except (TypeError, ValueError):
            g = 0.0
        if g > 0:
            vs["guidance_scale"] = min(max(g, GUIDANCE_MIN), GUIDANCE_MAX)
        body = {"text": text, "voice_settings": vs}
        ins = (instructions or "").strip()
        if ins:
            body["instructions"] = ins
        if language:
            body["language_code"] = language
        kw = dict(headers={"xi-api-key": key}, json=body, timeout=45)
        sess = self._session or _DIRECT
        try:
            return sess.post(url, **kw)
        except requests.RequestException as e:
            print(f"[tts] 直连失败（{type(e).__name__}），改走系统代理")
            return requests.post(url, **kw)

    def _post_with_retry(self, voice, text, instructions="", guidance=0, language=""):
        """多 key 轮流 + 重试。

        每个 key 独立重试：429（并发超限）/ 5xx 等 1.5s 重试，最多 3 次；
        401（key 失效）/ 402（额度不足）/ 403 视为这个 key 不可用，立即换下一个 key。
        全部 key 都试完仍失败，返回最后一次的响应。
        """
        keys = self.keys or [""]
        last = None
        for ki, key in enumerate(keys):
            for i in range(3):
                last = self._post_once(voice, key, text, instructions,
                                       guidance, language)
                if last.status_code == 200:
                    return last
                # 这个 key 本身不可用（失效/额度没了/没权限）——换下一个
                if last.status_code in (401, 402, 403):
                    print(f"[tts] key[{ki}] 不可用（HTTP {last.status_code}），换下一个")
                    break
                if last.status_code in (429, 500, 502, 503, 504) and i < 2:
                    time.sleep(1.5 * (i + 1))
                    continue
                break
        return last

    def _prune(self):
        """超量时按「最近播放时间」删最旧的；正在用的一律不删。

        命中缓存会 _touch 刷新 mtime，所以 mtime 就是 LRU 时间，
        常用的句子不会被新句子挤掉。
        """
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)
                     if f.endswith(".mp3")]
            if len(files) <= MAX_CACHE_FILES:
                return
            protect = {self._cur, self._last_source}
            files = [p for p in files if p not in protect]
            files.sort(key=os.path.getmtime)
            for p in files[:max(0, len(files) - MAX_CACHE_FILES)]:
                try:
                    os.remove(p)
                except OSError:
                    pass
        except Exception:
            pass

    def stats(self):
        """省了多少，给菜单和 /status 看。"""
        total = self.cache_hits + self.api_calls
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "api_calls": self.api_calls,
            "played_segs": self.played_segs,
            "hit_rate": round(self.cache_hits / total * 100, 1) if total else 0.0,
            "cache_files": len([f for f in os.listdir(self.cache_dir) if f.endswith(".mp3")])
            if os.path.isdir(self.cache_dir) else 0,
            "queued": self.queued,
            "output": self.current_output_name(),
        }

    # ---------- 播放（主线程） ----------
    def _play(self, path, text, force=False):
        if not path:
            return
        # 同一段正在播：后台重复触发的不重来；你主动要的从头再放（不重载文件）
        # 但 1.5 秒内的连击当误触，不理会——双击试听不该念两遍
        if path == self._cur:
            if force and time.time() - self._recent.get(path, 0) > 1.5:
                try:
                    self.player.setPosition(0)
                    self.player.play()
                except Exception:
                    pass
            return
        if not force and time.time() - self._recent.get(path, 0) < DEDUP_SEC:
            return
        if path in self._play_q:
            return
        self._play_q.append(path)
        del self._play_q[:-MAX_QUEUE]
        self.queued = len(self._play_q)
        self._pump()

    def _pump(self):
        if self._cur or not self._play_q:
            return
        # 每句开播前对一次默认设备：你中途从耳机切到音响，下一句就跟过去了
        self._bind_default_output()
        path = self._play_q.pop(0)
        self.queued = len(self._play_q)
        self._cur = path
        self._recent[path] = time.time()
        self.played_segs += 1

        if path == self._last_source and self.duration_ms() > 0:
            # 播放器已经加载过这个文件，回到开头就行（省一次重新解码）
            try:
                self.player.setPosition(0)
                self.player.play()
            except Exception:
                self.player.setSource(QUrl.fromLocalFile(path))
                self.player.play()
        else:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(path))
            self._last_source = path
            self.player.setPosition(0)
            self.player.play()
        self._arm_watch(path)

    def _arm_watch(self, path):
        """兜底：万一 6 秒还没出声（解码卡住），跳过这段，别把后面全堵死。"""
        self._cancel_watch()
        w = QTimer(self)
        w.setSingleShot(True)
        w.timeout.connect(lambda: self._watchdog(path))
        w.start(6000)
        self._watch = w

    def _watchdog(self, path):
        if path != self._cur:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        print("[tts] 播放卡住，跳过:", os.path.basename(path))
        self._finish_current()

    # ---------- 账户信息 ----------
    def usage(self):
        """返回近 30 天用量；失败返回 None。"""
        if not self.key:
            return None
        url = f"{API_BASE}/usage"
        try:
            try:
                r = _DIRECT.get(url, headers={"xi-api-key": self.key}, timeout=20)
            except requests.RequestException:
                r = requests.get(url, headers={"xi-api-key": self.key}, timeout=20)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None
