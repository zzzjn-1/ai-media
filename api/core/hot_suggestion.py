# -*- coding: utf-8 -*-
"""
api/core/hot_suggestion.py — 热点切入点建议（P2-1）

用户面对一条热榜，卡住的从来不是「要不要写」，而是「从哪儿切」。
所以这里产出的不是摘要复述，而是一个可执行的角度。

三条硬约束（每条都对应一个真实的成本或故障）：
  ① 惰性生成：只在 feed 真正要返回、且该条还没有建议时才生成。
     采集时对 100 条全量调 LLM，首屏要等十几秒、每轮都烧钱，而用户一屏只看 20 条。
  ② 批量化：一次请求把 N 条压成 1 次 LLM 调用，N 由
     HOTSPOT_SUGGESTION_MAX_PER_REQUEST 决定（默认 5）。
  ③ 确定性兜底：无 Key / 返回非 JSON / 超时 / 解析失败都必须给出一条建议。
     否则「建议角度」这个卖点会随机消失，用户只会判断成 bug。
     LLM 连续失败时进冷却期（默认 10 分钟），期间直接走兜底，避免每次开看板都卡在超时上。
"""
from __future__ import annotations

import json
import re
import time

from ..config import HOTSPOT_SUGGESTION_ENABLED, HOTSPOT_SUGGESTION_MAX_PER_REQUEST
from .domain_keywords import DOMAIN_KEYWORDS
from .llm import get_llm
from .prompts import SYSTEM_HOT_ANGLE, build_hot_angle_prompt

# 与任务书验收口径一致：一条建议 ≤40 字
MAX_LEN = 40
# LLM 失败后的冷却窗口（秒）：期间只走兜底，不再发起网络调用
FAIL_COOLDOWN_SECONDS = 600
# 空返回的冷却窗口更短：模型偶尔吐空串（传输抖动/风控）是一次性故障，
# 用 10 分钟把整页建议都打成模板话，代价比多试一次大得多
EMPTY_COOLDOWN_SECONDS = 60

_fail_until = 0.0


# -------------------------------------------------------------
# 确定性兜底：不依赖任何外部服务
# -------------------------------------------------------------
def _pick_keyword(title: str, summary: str, domain: str | None) -> str:
    """从领域词表里挑一个"真的出现在标题里"的具体名词作为切入锚点。

    优先标题再摘要、优先高权重词——这样建议才落在具体事物上，
    而不是"这件事值得关注"这种正确但没用的空话。
    """
    text = f"{title} {summary}".lower()
    table = DOMAIN_KEYWORDS.get(domain or "", [])
    # 兜底：领域缺失时按「强识别词优先」扫全表，避免只能退到截断标题
    pools = [table] + [v for k, v in DOMAIN_KEYWORDS.items() if k != domain]
    for pool in pools:
        for kw, _weight in sorted(pool, key=lambda x: -x[1]):
            if kw.lower() in text:
                return kw
    # 一个词都没命中：取标题最前面的实词片段（去掉常见修饰词）
    clean = re.sub(r"^[#＃\s\d.、,，]+", "", title or "").strip()
    return (clean or (title or "这条热点"))[:10]


_TEMPLATES = (
    "从「{kw}」切入，给出 3 个可验证的判断",
    "拆「{kw}」的成本账：谁受益、谁承担",
    "把「{kw}」做成对比：以前 vs 现在",
    "给「{kw}」列一份避坑清单",
    "反着说「{kw}」：多数人的直觉是错的",
)


def _clip(text: str, limit: int = MAX_LEN) -> str:
    """裁剪到 limit 字，尽量断在标点处——不要把一个词切成两半。"""
    s = (text or "").strip().strip('"“”\'「」')
    if len(s) <= limit:
        return s
    head = s[:limit]
    for sep in ("，", "；", "。", "、", ",", ";"):
        i = head.rfind(sep)
        if i >= int(limit * 0.6):
            return head[:i]
    return head


def heuristic_angle(title: str, summary: str = "", domain: str | None = None,
                    seed: int = 0) -> str:
    """规则生成一条建议角度（≤40 字）。同一标题稳定产出，便于前端缓存与测试断言。"""
    kw = _pick_keyword(title or "", summary or "", domain)
    return _clip(_TEMPLATES[seed % len(_TEMPLATES)].format(kw=kw))


# -------------------------------------------------------------
# LLM 批量生成
# -------------------------------------------------------------
def _parse_angles(raw: str, n: int) -> list[str]:
    """解析 JSON 数组；容忍模型常见的三种偏离：包裹解释文字、裸字符串、编号列表。

    「只请求 1 条」时模型经常直接返回 `"从…切入"` 而不带数组括号——这不是模型错，
    是请求形状太简单，解析端必须吃下来，否则每次首屏只补一条时都会静默退化成规则兜底。
    """
    out: list[str] = []
    try:
        lo, hi = raw.find("["), raw.rfind("]")
        if lo >= 0 and hi > lo:
            data = json.loads(raw[lo:hi + 1])
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, str) and d.strip():
                        out.append(_clip(d))
                    elif isinstance(d, dict):
                        # 兼容模型返回 {"angle": "..."} 的情况
                        v = d.get("angle") or d.get("suggestion") or ""
                        if isinstance(v, str) and v.strip():
                            out.append(_clip(v))
    except Exception:  # noqa: BLE001 —— 解析失败交给兜底，不抛给上层
        out = []

    if not out:
        body = (raw or "").strip()
        try:
            v = json.loads(body)
            if isinstance(v, str):
                out = [_clip(v)]
            elif isinstance(v, list):
                out = [_clip(x) for x in v if isinstance(x, str)]
        except Exception:  # noqa: BLE001
            out = []

    if not out:
        for line in (raw or "").splitlines():
            clean = re.sub(r"^[\s\-*•\d.、,，)）]+", "", line).strip()
            if clean:
                out.append(_clip(clean))
            if len(out) >= n:
                break

    return out[:n]


def _generate(items: list[dict]) -> tuple[list[str], str]:
    """返回 (建议列表, 实际使用的后端)。任何异常都不外抛。"""
    global _fail_until
    n = len(items)
    fallback = [
        heuristic_angle(it.get("title", ""), it.get("summary", ""),
                        it.get("domain"), seed=i)
        for i, it in enumerate(items)
    ]
    if not items:
        return [], "none"
    if time.time() < _fail_until:
        return fallback, "heuristic(cooldown)"

    llm = get_llm()
    if llm.backend == "mock":
        # 离线后端只会回一段改写文本，解析不出 JSON，直接走兜底（省一次无效调用）
        return fallback, "heuristic"

    angles: list[str] = []
    raw = ""
    for _attempt in range(2):
        try:
            # max_tokens 必须随条数增长：固定 400 时批量 5 条会在最后一条中途被截断，
            # 表现为"建议角度说到一半没了"，看起来像模型蠢，其实是 token 给少了
            raw = llm.complete(SYSTEM_HOT_ANGLE, build_hot_angle_prompt(items),
                               temperature=0.7, max_tokens=max(400, 100 * n + 200))
        except Exception as e:  # noqa: BLE001
            _fail_until = time.time() + FAIL_COOLDOWN_SECONDS
            print(f"  [热点建议] LLM 调用失败，{FAIL_COOLDOWN_SECONDS}s 内走规则兜底：{e}")
            return fallback, "heuristic"
        angles = _parse_angles(raw, n)
        if angles:
            angles += fallback[len(angles):]
            return angles, llm.backend
        if (raw or "").strip():
            break       # 有内容但解析不出来 → 是格式问题，重试无意义
        # 空返回 → 一次性抖动，立刻再试一次再判定

    if (raw or "").strip():
        _fail_until = time.time() + FAIL_COOLDOWN_SECONDS
        # 静默降级最坑：日志里什么都没有，事后只能看到"建议角度怎么都是模板话"
        print(f"  [热点建议] 模型返回无法解析，本轮改规则兜底：{raw[:80]!r}")
    else:
        _fail_until = time.time() + EMPTY_COOLDOWN_SECONDS
        print(f"  [热点建议] 模型连续返回空串，{EMPTY_COOLDOWN_SECONDS}s 内走规则兜底")
    return fallback, "heuristic"


# -------------------------------------------------------------
# 对外入口
# -------------------------------------------------------------
def ensure(conn, rows: list[dict], limit: int | None = None) -> int:
    """为缺 suggestion 的条目补齐并回写 hotspots，同时就地更新 rows。

    Args:
        conn: 已打开的 sqlite 连接（写权限）
        rows: /hotspots 即将返回的条目（需含 id/title/summary/domain/suggestion）
        limit: 本次最多生成几条；None 取配置值
    Returns:
        实际写入条数
    """
    if not HOTSPOT_SUGGESTION_ENABLED or not rows:
        return 0
    cap = max(0, limit if limit is not None else HOTSPOT_SUGGESTION_MAX_PER_REQUEST)
    if cap == 0:
        return 0

    targets = [r for r in rows if not (r.get("suggestion") or "").strip()][:cap]
    if not targets:
        return 0

    angles, backend = _generate(targets)
    if not angles:
        return 0

    written = 0
    for row, angle in zip(targets, angles):
        if not angle:
            continue
        conn.execute("UPDATE hotspots SET suggestion=? WHERE id=?", (angle, row["id"]))
        row["suggestion"] = angle          # 让本次响应立刻带上，不必等下一轮
        written += 1
    if written:
        conn.commit()
    if backend not in ("heuristic",):
        print(f"  [热点建议] 生成 {written} 条（后端 {backend}）")
    return written
