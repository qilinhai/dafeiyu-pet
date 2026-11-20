"""大肥鱼本地桥接：只监听 127.0.0.1，供 WorkBuddy 技能调用。"""
import json, os, secrets, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DaFeiYuBridge")
TOKEN_FILE = os.path.join(APP_DIR, "token.txt")
ENDPOINT_FILE = os.path.join(APP_DIR, "endpoint.json")
MAX_TEXT = 200
MIN_INTERVAL = 0.5


def _ensure_token():
    os.makedirs(APP_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(32))
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return f.read().strip()


def start_bridge(pet, port=8790):
    token = _ensure_token()
    last = [0.0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            if self.headers.get("X-Pet-Token") != token:
                self._json(401, {"ok": False, "error": "bad token"})
                return False
            return True

        def do_GET(self):
            if not self._authed():
                return
            if self.path == "/health":
                return self._json(200, {"ok": True, "pid": os.getpid(), "port": port})
            if self.path == "/status":
                tts = getattr(pet, "tts", None)
                return self._json(200, {
                    "ok": True,
                    "bubble": getattr(pet, "bubble_text", ""),
                    "mode": getattr(pet, "mode", ""),
                    "x": pet.x(), "y": pet.y(), "w": pet.width(), "h": pet.height(),
                    "clip": getattr(pet, "clip", None),
                    "working": bool(getattr(pet, "working", False)),
                    "speaking": bool(getattr(tts, "speaking", False)),
                    "tts": {
                        "enabled": bool(getattr(tts, "enabled", False)),
                        "voice": getattr(tts, "voice_id", ""),
                        "voice_name": getattr(tts, "voice_name", ""),
                        "speaking": bool(getattr(tts, "speaking", False)),
                        "queued": int(getattr(tts, "queued", 0)),
                        "position_ms": tts.position_ms() if tts else 0,
                        "duration_ms": tts.duration_ms() if tts else 0,
                        "last_error": getattr(tts, "last_error", ""),
                        "stats": tts.stats() if tts is not None else {},
                    },
                })
            if self.path == "/tts":
                tts = getattr(pet, "tts", None)
                return self._json(200, {
                    "ok": True,
                    "available": tts is not None,
                    "enabled": bool(getattr(tts, "enabled", False)),
                    "has_key": bool(getattr(tts, "key", "")),
                    "voice": getattr(tts, "voice_id", ""),
                    "voice_name": getattr(tts, "voice_name", ""),
                    "speaking": bool(getattr(tts, "speaking", False)),
                    "last_error": getattr(tts, "last_error", ""),
                    "stats": tts.stats() if tts is not None else {},
                })
            if self.path == "/credits":
                tts = getattr(pet, "tts", None)
                key = getattr(tts, "key", "") if tts else ""
                if not key:
                    return self._json(200, {"ok": False, "error": "no tts key"})
                try:
                    from credits_guard import daily_claim_status, pick_line
                    st = daily_claim_status(key, timeout=12)
                    st["line"] = pick_line(st)
                    return self._json(200, st)
                except Exception as e:
                    return self._json(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            if self.path == "/memory":
                try:
                    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    if root not in sys.path:
                        sys.path.insert(0, root)
                    from pet_memory import AmbientMemory, phase_of, is_consolidated
                    am = AmbientMemory()
                    now = int(time.time())
                    items = []
                    for it in am.items()[:20]:
                        items.append({
                            "topic": it.get("topic"),
                            "phase": phase_of(it, now),
                            "consolidated": is_consolidated(it),
                            "protected": bool(it.get("protected")),
                            "hits": it.get("hits", 1),
                            "days": it.get("days", 1),
                            "last": time.strftime("%Y-%m-%d %H:%M",
                                                  time.localtime(int(it.get("last_ts") or 0))),
                            "sources": [s.get("ref") for s in (it.get("sources") or [])],
                        })
                    return self._json(200, {"ok": True, "items": items,
                                            "preferences": am.preferences(),
                                            "summary": am.summary()})
                except Exception as e:
                    return self._json(200, {"ok": False,
                                            "error": f"{type(e).__name__}: {e}"})
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._authed():
                return
            now = time.time()
            if now - last[0] < MIN_INTERVAL:
                return self._json(429, {"ok": False, "error": "too fast"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})

            if self.path == "/say":
                text = " ".join(str(payload.get("text", "")).split())[:MAX_TEXT]
                if not text:
                    return self._json(400, {"ok": False, "error": "empty text"})
                try:
                    seconds = min(max(float(payload.get("seconds", 6)), 1.0), 60.0)
                except (TypeError, ValueError):
                    return self._json(400, {"ok": False, "error": "bad seconds"})
                last[0] = now
                # speak 默认 true：说出来的话就该有声音；调用方可用 --silent 关掉
                tts = getattr(pet, "tts", None)
                speak = bool(payload.get("speak", True))
                # 可选：说完强制演的动作名（通知播报用，绕过随机克制）
                anim = payload.get("anim") or None
                # 可选：念出来那份和显示那份可以不一样。
                # 通知播报会在事实和梗之间插 [停顿] 这类标签——气泡里露出来很难看，
                # 所以气泡用 text，合成用 speech。
                speech = " ".join(str(payload.get("speech") or "").split())[:MAX_TEXT] or None
                style = str(payload.get("style") or "").strip()[:20] or None
                pet._queue_say(text, seconds, speak, anim, speech, style)
                return self._json(200, {
                    "ok": True, "text": text,
                    "speak": bool(speak and tts is not None and tts.enabled),
                })

            if self.path == "/tts":
                tts = getattr(pet, "tts", None)
                if tts is None:
                    return self._json(400, {"ok": False, "error": "tts unavailable"})
                action = str(payload.get("action", "stop"))
                last[0] = now
                if action == "stop":
                    tts.stop()
                    return self._json(200, {"ok": True, "action": "stopped"})
                if action == "preview":
                    pet._queue_say(f"我是{tts.voice_name}，这么说话你听得惯吗", 6.0, True)
                    return self._json(200, {"ok": True, "action": "preview"})
                if action == "voice":
                    want = str(payload.get("voice", "")).strip()
                    from tts_breeze import VOICES
                    vid = next((v for n, v in VOICES if want in (v, n)), "")
                    if not vid:
                        return self._json(400, {"ok": False, "error": "unknown voice"})
                    pet.set_tts_voice(vid, next(n for n, v in VOICES if v == vid))
                    return self._json(200, {"ok": True, "action": "voice", "voice": vid})
                if action == "speed":
                    try:
                        sp = float(payload.get("speed"))
                    except (TypeError, ValueError):
                        return self._json(400, {"ok": False, "error": "bad speed"})
                    pet.set_tts_speed(sp)
                    return self._json(200, {"ok": True, "action": "speed", "speed": sp})
                return self._json(400, {"ok": False,
                                        "error": "action must be stop/preview/voice/speed"})

            if self.path == "/mode":
                mode = str(payload.get("mode", ""))
                if mode not in ("wander", "follow", "still"):
                    return self._json(400, {"ok": False, "error": "mode must be wander/follow/still"})
                last[0] = now
                pet.set_mode(mode)
                return self._json(200, {"ok": True, "mode": mode})

            self._json(404, {"ok": False, "error": "not found"})

    # 关键：Windows 下 SO_REUSEADDR（allow_reuse_address=1，HTTPServer 默认开）
    # 会允许两个进程同时 bind 同一端口，导致「双鱼」——第二条鱼照样能起桥，
    # 端口归属混乱，变成一条哑的、不响应事件的鱼。这里必须关掉，
    # 让端口真正成为互斥锁：第二实例 bind 失败抛 OSError，pet.py 捕获后退出。
    # 注意：allow_reuse_address 必须在 server_bind() 之前设才生效，
    # 所以不能构造后再改，得在构造前就关掉。
    class _NoReuseHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False

    srv = _NoReuseHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with open(ENDPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(),
                   "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, ensure_ascii=False)
    print(f"[bridge] listening on 127.0.0.1:{port}")
