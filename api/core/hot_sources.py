# -*- coding: utf-8 -*-
"""
api/core/hot_sources.py — 热榜来源注册表与适配器（P1-3）

职责边界（非常重要）：
    适配器只做一件事——把各家异构 JSON 变成同构条目：
        {source_code, title, summary, url, rank, raw_heat}
    ⚠️ 适配器【不做】领域判定。领域判定统一由 hot_signal 在聚合阶段调用
    domain_keywords 完成。否则会出现"适配器一套词表、聚合一套词表"的漂移。

三层降级：
    ① HOTSPOT_API_BASE（自托管 DailyHot）→ ② vvhan 免费 JSON → ③ tenapi 免费 JSON
    任一适配器抛错都只记错并尝试下一个；全部失败返回 ([], "原因")，绝不上抛。
    单源失败必须隔离——一个源挂掉不能拖垮整轮采集。

命名铁律：
    本模块的 code（weibo/zhihu/...）是「热榜来源码」，
    与 platform_rules.platform（weixin/xhs/douyin）「发布平台码」语义不同，禁止混用。
"""
from __future__ import annotations

import concurrent.futures
import re

from ..config import HOTSPOT_API_BASE, HOTSPOT_TIMEOUT

# -------------------------------------------------------------
# 来源注册表
#   interval_min：每源独立采集节奏（不复用全局间隔，避免"改一个数全变"）
#   weight      ：来源权威度，预留给打分（当前归一化只按 rank，不混源权）
# -------------------------------------------------------------
SOURCES: dict[str, dict] = {
    "weibo":    {"name": "微博热搜",   "interval_min": 10, "weight": 1.0,
                 "vvhan": "wbHot", "tenapi": "weibohot",    "dailyhot": "weibo"},
    "zhihu":    {"name": "知乎热榜",   "interval_min": 15, "weight": 1.0,
                 "vvhan": "zhHot", "tenapi": "zhihuhot",    "dailyhot": "zhihu"},
    "baidu":    {"name": "百度热搜",   "interval_min": 15, "weight": 1.0,
                 "vvhan": "bdHot", "tenapi": "baiduhot",    "dailyhot": "baidu"},
    "douyin":   {"name": "抖音热榜",   "interval_min": 15, "weight": 0.9,
                 "vvhan": "dyHot", "tenapi": "douyinhot",   "dailyhot": "douyin"},
    "bilibili": {"name": "B站热门",    "interval_min": 20, "weight": 0.8,
                 "vvhan": "blHot", "tenapi": "bilibilihot", "dailyhot": "bilibili"},
    "toutiao":  {"name": "头条热榜",   "interval_min": 15, "weight": 0.9,
                 "vvhan": "ttHot", "tenapi": "toutiaohot",  "dailyhot": "toutiao"},
}

SOURCE_CODES = list(SOURCES)


def source_label(code: str) -> str:
    """来源码 → 中文展示名（前端展示与 summary 兜底文案都用它）。"""
    return SOURCES.get(code, {}).get("name", code)


# -------------------------------------------------------------
# 原始 JSON → 同构条目
# -------------------------------------------------------------
_LIST_KEYS = ("data", "list", "result", "items", "hotlist", "rows")
_TITLE_KEYS = ("title", "name", "word", "keyword", "query", "hotword", "content")
_URL_KEYS = ("url", "link", "href", "mobileUrl", "mobil_url")
_SUM_KEYS = ("desc", "description", "summary", "digest", "brief")
_HEAT_KEYS = ("hot", "hotness", "heat", "hotValue", "hot_value", "num", "score", "index")


def _to_int(value) -> int | None:
    """热度字符串转整数：支持 '1234.5万' / '1.2亿' / '12,345' / '98210 热度'。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    num = float(m.group(1))
    if "亿" in s:
        num *= 100_000_000
    elif "万" in s or "w" in s.lower():
        num *= 10_000
    return int(num)


def _first(raw: dict, keys) -> str | None:
    for k in keys:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _pick_list(payload) -> list:
    """在多层 JSON 里找出"条目标表"，兼容 data / data.list / data.data 等形状。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in _LIST_KEYS:
        v = payload.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for inner in _LIST_KEYS:
                if isinstance(v.get(inner), list):
                    return v[inner]
    return []


def normalize_payload(payload, source_code: str) -> list[dict]:
    """把任意来源的 JSON 压成同构条目。空标题条目直接丢弃（噪声源常见）。"""
    items: list[dict] = []
    for i, raw in enumerate(_pick_list(payload), start=1):
        if not isinstance(raw, dict):
            continue
        title = _first(raw, _TITLE_KEYS)
        if not title:
            continue
        rank = _to_int(raw.get("index")) or _to_int(raw.get("rank")) \
            or _to_int(raw.get("position")) or i
        heat = None
        for k in _HEAT_KEYS:
            if k in raw:
                heat = _to_int(raw.get(k))
                if heat is not None:
                    break
        items.append({
            "source_code": source_code,
            "title": title,
            "summary": _first(raw, _SUM_KEYS) or "",
            "url": _first(raw, _URL_KEYS) or "",
            "rank": int(rank),
            "raw_heat": heat,
        })
    # 去重（同源同名）+ 按榜单排名重排，避免源里重复项污染覆盖度统计
    seen: set[str] = set()
    deduped = []
    for it in sorted(items, key=lambda x: x["rank"]):
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        deduped.append(it)
    for idx, it in enumerate(deduped, start=1):
        it["rank"] = idx
    return deduped


# -------------------------------------------------------------
# 适配器（只负责 HTTP 取数，解析统一走 normalize_payload）
# -------------------------------------------------------------
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _get_json(url: str, timeout: float, headers: dict | None = None):
    import httpx
    h = {"User-Agent": _BROWSER_UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=h) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _fetch_dailyhot(source_code: str, timeout: float):
    route = SOURCES[source_code].get("dailyhot")
    if not HOTSPOT_API_BASE or not route:
        raise RuntimeError("未配置 HOTSPOT_API_BASE")
    return _get_json(f"{HOTSPOT_API_BASE.rstrip('/')}/{route}", timeout)


# ----- 官方接口适配（平台自家前端在用的 JSON，比免费聚合源更稳、更实时） -----
# 每个 parser 只做一件事：把平台原生结构映射成 normalize_payload 能吃的
# {title, desc, url, hot, rank}。字段名大小写/嵌套层级差异全部收敛在这里，
# 上层（fetch_source / 聚合 / 物化）完全不感知。

def _p_baidu(payload):
    """top.baidu.com/api/board → data.cards[0].content[0].content[]（word/url，无热度）"""
    cards = ((payload or {}).get("data") or {}).get("cards") or []
    out = []
    for card in cards:
        for grp in (card.get("content") or []):
            for i, it in enumerate(grp.get("content") or [], start=1):
                if isinstance(it, dict) and it.get("word"):
                    out.append({"title": it["word"], "url": it.get("url") or "",
                                "desc": "", "hot": None, "rank": i})
    return {"data": out}


def _p_bilibili(payload):
    """api.bilibili.com/x/web-interface/search/square → data.trending.list[]（keyword/heat_score）"""
    lst = ((((payload or {}).get("data") or {}).get("trending") or {}).get("list")) or []
    out = []
    for i, it in enumerate(lst, start=1):
        title = (it.get("show_name") or it.get("keyword") or "").strip()
        if title:
            out.append({"title": title, "url": "", "desc": "",
                        "hot": it.get("heat_score"), "rank": i})
    return {"data": out}


def _p_douyin(payload):
    """douyin.com/aweme/v1/web/hot/search/list → data.word_list[]（word/hot_value）"""
    lst = ((payload or {}).get("data") or {}).get("word_list") or []
    out = []
    for i, it in enumerate(lst, start=1):
        title = (it.get("word") or "").strip()
        if title:
            out.append({"title": title, "url": "", "desc": "",
                        "hot": it.get("hot_value"), "rank": i})
    return {"data": out}


def _p_toutiao(payload):
    """toutiao.com/hot-event/hot-board → data[]（Title/HotValue，注意首字母大写）"""
    out = []
    for i, it in enumerate((payload or {}).get("data") or [], start=1):
        title = (it.get("Title") or "").strip()
        if title:
            out.append({"title": title, "url": it.get("Url") or "", "desc": "",
                        "hot": it.get("HotValue"), "rank": i})
    return {"data": out}


def _p_weibo(payload):
    """weibo.com/ajax/side/hotSearch → data.realtime[]（word/num）。需 Referer，无 Cookie 会 403。"""
    lst = ((payload or {}).get("data") or {}).get("realtime") or []
    out = []
    rank = 0
    for it in lst:
        title = (it.get("word") or "").strip()
        if not title or it.get("is_ad"):
            continue
        rank += 1
        out.append({"title": title, "url": f"https://s.weibo.com/weibo?q=%23{title}%23",
                    "desc": it.get("note") or "", "hot": it.get("num"), "rank": rank})
    return {"data": out}


# 官方适配表：源 → (URL, parser, 额外请求头)。没有条目 = 该源不支持官方接口。
_OFFICIAL: dict[str, tuple[str, object, dict]] = {
    "baidu": ("https://top.baidu.com/api/board?platform=wise&tab=realtime",
              _p_baidu, {"Referer": "https://top.baidu.com/board?tab=realtime"}),
    "bilibili": ("https://api.bilibili.com/x/web-interface/search/square?limit=30",
                 _p_bilibili, {"Referer": "https://www.bilibili.com/"}),
    "douyin": ("https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383",
               _p_douyin, {"Referer": "https://www.douyin.com/hot"}),
    "toutiao": ("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                _p_toutiao, {"Referer": "https://www.toutiao.com/"}),
    "weibo": ("https://weibo.com/ajax/side/hotSearch",
              _p_weibo, {"Referer": "https://weibo.com/"}),
}


def _fetch_official(source_code: str, timeout: float):
    spec = _OFFICIAL.get(source_code)
    if not spec:
        raise RuntimeError("该源无 official 适配")
    url, parser, headers = spec
    return parser(_get_json(url, timeout, headers=headers))


def _fetch_vvhan(source_code: str, timeout: float):
    t = SOURCES[source_code].get("vvhan")
    if not t:
        raise RuntimeError("该源无 vvhan 适配")
    return _get_json(f"https://api.vvhan.com/api/hotlist/{t}", timeout)


def _fetch_tenapi(source_code: str, timeout: float):
    t = SOURCES[source_code].get("tenapi")
    if not t:
        raise RuntimeError("该源无 tenapi 适配")
    return _get_json(f"https://tenapi.cn/v2/{t}", timeout)


# 适配器链：顺序即优先级。
# dailyhot（自托管实例，可控）→ official（平台官方接口，无需部署、最实时）
# → vvhan / tenapi（免费聚合，仅作兜底，限频与结构变更风险最高）。
_ADAPTERS = (
    ("dailyhot", _fetch_dailyhot),
    ("official", _fetch_official),
    ("vvhan", _fetch_vvhan),
    ("tenapi", _fetch_tenapi),
)


def fetch_source(source_code: str, timeout: float | None = None,
                 max_items: int = 20) -> tuple[list[dict], str | None]:
    """采集单个来源。返回 (items, error)。

    约定：任一适配器成功即返回；全部失败返回 ([], "各适配器失败原因")。
    永不抛异常——调用方（调度器）依赖这一点做单源隔离。
    """
    if source_code not in SOURCES:
        return [], f"未知来源 {source_code}"
    timeout = float(timeout or HOTSPOT_TIMEOUT)
    errors: list[str] = []
    for name, fn in _ADAPTERS:
        try:
            payload = fn(source_code, timeout)
        except Exception as e:  # noqa: BLE001 —— 单源单适配器失败必须吞掉
            errors.append(f"{name}: {type(e).__name__}: {e}"[:160])
            continue
        items = normalize_payload(payload, source_code)
        if items:
            return items[:max_items], None
        errors.append(f"{name}: 返回 0 条可用条目")
    return [], " | ".join(errors) or "无可用适配器"


def fetch_many(codes: list[str], timeout: float | None = None,
               max_items: int = 20) -> dict[str, dict]:
    """并发采集多个来源，整体耗时约等于最慢单源（而非逐一累加）。

    每个来源独立线程，异常在 fetch_source 内部已吞掉，因此这里拿到的一定是
    完整映射，不会因某个源崩溃而丢结果。
    返回 {code: {"items": [...], "error": str|None, "elapsed_ms": int}}
    """
    import time

    out: dict[str, dict] = {}
    if not codes:
        return out

    def _timed(code: str):
        t0 = time.perf_counter()
        items, err = fetch_source(code, timeout, max_items)
        return {"items": items, "error": err,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(codes))) as pool:
        futures = {pool.submit(_timed, c): c for c in codes}
        for fut in concurrent.futures.as_completed(futures):
            code = futures[fut]
            try:
                out[code] = fut.result()
            except Exception as e:  # noqa: BLE001 —— 理论上不可达，兜底防御
                out[code] = {"items": [], "error": f"线程异常 {type(e).__name__}: {e}",
                             "elapsed_ms": 0}
    return out
