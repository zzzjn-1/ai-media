# -*- coding: utf-8 -*-
"""
api/core/hot_rank.py — 相关性打分与可解释理由（P1-7）

为什么不用 norm_heat 直接排：
    全网红 ≠ 值得你写。对个人博主来说，"这条跟我账号有没有关系、是不是新出现、
    有没有多平台在讨论"比绝对热度更重要。所以 feed 视图按 5 因子加权：
        领域命中 0.30 + 新颖度 0.25 + 覆盖度 0.20 + 归一化热度 0.15 + 可创作性 0.10
    新 > 热 —— 用"首次上榜时间"而不是热度绝对值决定新鲜度。

无账号领域时的行为：
    退化为 norm_heat 排序，不报错、不返回 relevance/reason（避免让用户误以为
    全榜也做了个性化）。
"""
from __future__ import annotations

from datetime import datetime

from ..config import HOTSPOT_FEED_WEIGHTS, HOTSPOT_NEW_WINDOW_HOURS

# 领域匹配分档
FIT_IN_DOMAIN = 1.0
FIT_GENERIC = 0.55      # 「综合」：与账号领域可能沾边，但不保证
FIT_OUT_DOMAIN = 0.15   # 明确属于其他领域


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def get_user_domains(conn, user_id: int | None) -> tuple[list[str], str]:
    """读取账号领域与定位。user_id 为空（未登录）时返回空领域表。"""
    if not user_id:
        return [], ""
    row = conn.execute(
        "SELECT domains, positioning FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row:
        return [], ""
    domains = [d.strip() for d in (row["domains"] or "").split(",") if d.strip()]
    return domains, row["positioning"] or ""


def _hours_since_first_seen(item: dict, now: datetime) -> float:
    dt = _parse_dt(item.get("first_seen_at"))
    if not dt:
        return float(HOTSPOT_NEW_WINDOW_HOURS)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _domain_fit(item: dict, user_domains: list[str]) -> float:
    domain = item.get("domain") or "综合"
    if domain in user_domains:
        return FIT_IN_DOMAIN
    if domain == "综合":
        return FIT_GENERIC
    return FIT_OUT_DOMAIN


def _novelty(item: dict, now: datetime) -> float:
    hours = _hours_since_first_seen(item, now)
    window = max(1, HOTSPOT_NEW_WINDOW_HOURS)
    return max(0.0, 1.0 - min(1.0, hours / window))


def _coverage(item: dict) -> float:
    return min(1.0, float(item.get("source_count") or 1) / 3.0)


def _creatability(item: dict) -> float:
    summary = (item.get("summary") or "").strip()
    if len(summary) >= 20:
        return 1.0
    if summary:
        return 0.7
    return 0.4


def _build_reason(item: dict, user_domains: list[str], now: datetime) -> str:
    domain = item.get("domain") or "综合"
    parts: list[str] = []
    if domain in user_domains:
        parts.append(f"命中{domain}")
    elif domain == "综合":
        parts.append("跨领域，需自行判断")
    else:
        parts.append(f"{domain}（非账号主领域）")
    parts.append(f"{int(item.get('source_count') or 1)} 个平台在讨论")
    hours = _hours_since_first_seen(item, now)
    if hours < 1:
        parts.append("刚刚上榜")
    else:
        parts.append(f"{int(hours)} 小时前首次出现")
    return " · ".join(parts)


def rank_feed(items: list[dict], user_domains: list[str],
              now: datetime | None = None) -> list[dict]:
    """给候选热点打相关性分并排序。

    items 需含：domain / norm_heat / source_count / first_seen_at / summary。
    返回值在入参基础上追加 relevance（0-100）与 reason，并按分数降序。
    """
    now = now or datetime.now()
    if not user_domains:
        # 无人设：退化为热度排序，不返回 relevance/reason
        out = sorted(items, key=lambda x: x.get("norm_heat") or 0, reverse=True)
        for it in out:
            it.pop("relevance", None)
            it.pop("reason", None)
        return out

    w_domain, w_novel, w_cover, w_heat, w_create = HOTSPOT_FEED_WEIGHTS
    scored: list[dict] = []
    for it in items:
        score = (
            w_domain * _domain_fit(it, user_domains)
            + w_novel * _novelty(it, now)
            + w_cover * _coverage(it)
            + w_heat * (float(it.get("norm_heat") or 0) / 100.0)
            + w_create * _creatability(it)
        )
        it = dict(it)
        it["relevance"] = round(score * 100, 1)
        it["reason"] = _build_reason(it, user_domains, now)
        scored.append(it)
    scored.sort(key=lambda x: (x["relevance"], x.get("norm_heat") or 0), reverse=True)
    return scored
