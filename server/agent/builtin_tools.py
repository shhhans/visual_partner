"""内置工具实现（无需外部 MCP 进程）：datetime、calculate、weather、web_search。

weather  — Open-Meteo（免费无 key）
web_search — TAVILY_API_KEY 存在时走 Tavily；否则降级 DuckDuckGo Instant Answer
"""

import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import TAVILY_API_KEY

# ---------- get_datetime ----------

def get_datetime() -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.strftime('%Y年%m月%d日')} {weekdays[now.weekday()]} {now.strftime('%H:%M')}"


# ---------- calculate ----------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("不支持的表达式类型")


def calculate(expression: str) -> str:
    try:
        result = _eval_node(ast.parse(expression.strip(), mode="eval").body)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return str(result)
    except ZeroDivisionError:
        return "除零错误"
    except Exception as e:
        return f"计算出错：{e}"


# ---------- get_weather ----------

_WMO = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}


async def get_weather(city: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"},
            )
            results = geo.json().get("results", [])
            if not results:
                return f"找不到城市：{city}"
            loc = results[0]
            name = loc.get("name", city)

            wr = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                    "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
                    "timezone": "auto",
                },
            )
            cur = wr.json().get("current", {})
            temp = cur.get("temperature_2m", "?")
            humidity = cur.get("relative_humidity_2m", "?")
            wind = cur.get("wind_speed_10m", "?")
            cond = _WMO.get(cur.get("weather_code", -1), "未知")
            return f"{name}当前：{cond}，{temp}°C，湿度{humidity}%，风速{wind} km/h"
    except Exception as e:
        return f"天气查询失败：{e}"


# ---------- web_search ----------

async def web_search(query: str) -> str:
    if TAVILY_API_KEY:
        return await _tavily(query)
    return await _duckduckgo(query)


async def _tavily(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": 3,
                    "search_depth": "basic",
                },
            )
            items = r.json().get("results", [])
            if not items:
                return "没有找到相关结果"
            return "\n\n".join(
                f"{it['title']}：{it['content'][:200]}" for it in items[:3]
            )
    except Exception as e:
        return f"搜索失败：{e}"


async def _duckduckgo(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
            )
            data = r.json()
        abstract = data.get("AbstractText", "").strip()
        if abstract:
            return abstract[:400]
        topics = [
            t for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ]
        if topics:
            return "\n".join(t["Text"][:150] for t in topics[:3])
        return "没有找到相关结果（提示：设置 TAVILY_API_KEY 可获得更好的搜索效果）"
    except Exception as e:
        return f"搜索失败：{e}"
