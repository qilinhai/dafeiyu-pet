"""郑州今日。公网请求只含城市信息，记忆在本机生成。"""
from datetime import datetime, timedelta, timezone
from html import escape, unescape
import json
import re
import threading
import time

import requests
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

import pet_memory


CITY_ZONE = timezone(timedelta(hours=8))
CULTURE_URL = "https://wgl.zhengzhou.gov.cn/wgxx/index.jhtml"
STORIES = (
    ("郑州商都遗址博物院把城墙、夯土和考古探方融进了建筑。进展厅前，就能从外墙开始看展。", "https://www.zhengzhou.gov.cn/view45/6664682.jhtml"),
    ("河南艺术中心有五个椭圆体。留意它们的长轴，会发现它们一起指向同一个中心。", "https://www.zhengzhou.gov.cn/view42/index.jhtml"),
    ("登封的观星台、嵩阳书院和嵩岳寺塔，同属「天地之中」历史建筑群。", "https://wwj.zhengzhou.gov.cn/mtgz/7741450.jhtml"),
    ("二七纪念馆保护的地方除了纪念塔，还有郑州铁路职工学校旧址。", "https://wwj.zhengzhou.gov.cn/wbcgtj/10028139.jhtml"),
    ("郑州博物馆文翰街馆和嵩山路馆两馆并行，文翰街馆在 2021 年正式开放。", "https://www.zhengzhou.gov.cn/view45/6432988.jhtml"),
)


def city_date():
    return datetime.now(CITY_ZONE).date()


def weather(day, descriptions, wmo):
    try:
        response = requests.get("https://wttr.in/Zhengzhou", params={"format": "j1"}, timeout=6)
        response.raise_for_status()
        data = response.json()
        area = data["nearest_area"][0]
        if abs(float(area["latitude"])-34.75) > 0.6 or abs(float(area["longitude"])-113.62) > 0.6:
            raise ValueError("城市不符")
        forecast = next(row for row in data["weather"] if row["date"] == day.isoformat())
        noon = min(forecast["hourly"], key=lambda h: abs(int(h["time"]) - 1200))
        desc = noon["weatherDesc"][0]["value"].strip()
        return f"白天{descriptions.get(desc, desc)}，{forecast['mintempC']}～{forecast['maxtempC']} ℃（wttr.in）"
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, StopIteration):
        response = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": 34.75, "longitude": 113.62, "timezone": "Asia/Shanghai",
            "daily": "weather_code,temperature_2m_min,temperature_2m_max", "forecast_days": 1,
        }, timeout=7)
        response.raise_for_status()
        daily = response.json()["daily"]
        i = daily["time"].index(day.isoformat())
        low, high = daily["temperature_2m_min"][i], daily["temperature_2m_max"][i]
        return f"{wmo.get(daily['weather_code'][i], '天气状况未知')}，{low:g}～{high:g} ℃（Open-Meteo）"


def culture(day):
    response = requests.get(CULTURE_URL, timeout=6)
    response.raise_for_status()
    entries = re.findall(r'<a\s+href="(https://wgl\.zhengzhou\.gov\.cn/wgxx/\d+\.jhtml)"[^>]*>\s*'
                         r'<span>([^<]+)</span>\s*<em>(\d{4}-\d{2}-\d{2})</em>',
                         response.content.decode("utf-8"))
    for url, title, published in entries:
        if (day-timedelta(days=30)).isoformat() <= published <= day.isoformat() and re.search(
                "展览|文旅|音乐|舞蹈|夜校|博物|文商旅|演出|非遗|戏剧", title):
            return f'文旅局 {published} 发布 · <a href="{url}">{escape(unescape(title))}</a>'
    return ""


def local_sections(day, memory_path):
    try:
        d = pet_memory.read_store(memory_path)
        notes = ["你让我记住 · " + row["text"] for row in d.get("pins", [])]
        notes.extend("你的偏好 · " + row["text"] for row in d["preferences"])
        topics = [it["topic"] for it in d["items"] if pet_memory.phase_of(it) != "dormant"]
        parts = []
        if notes:
            parts.append(notes[day.toordinal() % len(notes)])
        if topics:
            parts.append("最近聊过 · " + topics[0])
        personal = "<br>".join(map(escape, parts)) or "还没记下什么。可以从右键「记忆与成长」留一条手记。"
    except (OSError, ValueError, KeyError, TypeError):
        personal = "暂时读不到记忆存档。"
    return personal


class TodayDialog(QDialog):
    result = Signal(object, str, str, bool)

    def __init__(self, pet):
        super().__init__(pet)
        self.setWindowTitle("郑州今日")
        self.resize(455, 475)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.body)
        self.memory_path = pet.ambient.path if pet.ambient else pet_memory.MEM_PATH
        self.sources = {"weather": lambda day: weather(day, pet.WEATHER_MAP, pet.WMO_MAP),
                        "culture": culture}
        self.cache = {}
        self.pending = set()
        self.result.connect(self.received, Qt.ConnectionType.QueuedConnection)
        self.clock = QTimer(self)
        self.clock.setInterval(60000)
        self.clock.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.start()
        self.refresh()

    def hideEvent(self, event):
        self.clock.stop()
        super().hideEvent(event)

    def refresh(self):
        self.day = city_date()
        self.personal = local_sections(self.day, self.memory_path)
        for kind, source in self.sources.items():
            key = (self.day, kind)
            cached = self.cache.get(kind)
            if key not in self.pending and not (cached and cached[0] == self.day and cached[2] > time.monotonic()):
                self.pending.add(key)
                threading.Thread(target=self.fetch, args=(self.day, kind, source), daemon=True).start()
        self.render()

    def fetch(self, day, kind, source):
        try:
            value = source(day)
            ok = True
        except Exception:
            value = "暂时查不到郑州今天的预报。" if kind == "weather" else ""
            ok = False
        self.result.emit(day, kind, value, ok)

    def received(self, day, kind, value, ok):
        self.pending.discard((day, kind))
        if self.day == day:
            self.cache[kind] = (day, value, time.monotonic() + (1800 if ok else 60))
            self.render()

    def render(self):
        story, source = STORIES[self.day.toordinal() % len(STORIES)]
        def current(kind, fallback):
            cached = self.cache.get(kind)
            return cached[1] if cached and cached[0] == self.day else fallback
        forecast = escape(current("weather", "正在查郑州今天的天气…"))
        news = current("culture", "")
        self.body.setHtml(f"<h2>郑州今日　{self.day:%m月%d日}</h2><h3>天气</h3><p>{forecast}</p>"
                          f'<h3>郑州趣事</h3><p>{story} <a href="{source}">出处</a></p>'
                          f"<p>{news}</p>"
                          f"<h3>鱼记得的事</h3><p>{self.personal}</p>")
