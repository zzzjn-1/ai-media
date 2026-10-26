# -*- coding: utf-8 -*-
"""
api/core/hot_signal.py — 热点聚合层与物化（P1-5 / P1-6）

一条链路：热榜原始条目 → hot_raw_items → hot_signals → hotspots

解决三个在"一张表"结构下无解的问题：
  ① 热度跨源不可比   → 归一化到 0-100，且只在同源内比较排名
  ② 跨源同话题重复   → canonical_key 归一化后合并，source_count = 真趋势强度
  ③ 老热点永不下架   → last_seen_at 生命周期 + TTL 退榜（不是额外补丁，是模型自带性质）

⚠️ 两层去重是分工，不是矛盾：
   raw 层：跨平台同名各自保留（保真，用于统计覆盖度）
   signal 层：同一话题有意合并为一条（趋势识别的价值所在）
   禁止在 raw 层做跨源合并，也禁止在 signal 层保留同话题多行。

⚠️ 前端在展示、但极易没有写入来源的三个字段，本模块负责兜住：
   summary（不得为 NULL）、trend（必须每轮推导）、heat（取自最优排名那条的原始热度）。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta

from ..config import (
    HOTSPOT_NEW_WINDOW_HOURS,
    HOTSPOT_SIGNAL_TTL_HOURS,
    HOTSPOT_SOURCE_STALE_MINUTES,
)
from .domain_keywords import detect_domain
from .hot_sources import source_label

# 归一化后至少保留这么长的标题才参与合并，避免"公告""热搜"这类极短标题互相误合并
MIN_CANONICAL_LEN = 4
# 同源排名线性映射的参考深度（榜单通常展示 20 条）
SOURCE_RANK_DEPTH = 20
# 趋势判定阈值
TREND_RANK_DELTA = 2      # 排名变化 >= 2 位视为变化
TREND_HEAT_DELTA = 5.0    # 归一化热度变化 >= 5 分视为变化

_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F\u200D\u20E3"
    "]"
)
_HASH_RE = re.compile(r"[#＃]")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


# -------------------------------------------------------------
# 归一化与指纹
# -------------------------------------------------------------
def normalize_title(title: str) -> str:
    """标题归一化：全角→半角 → 去 emoji → 去 # 话题符 → 去空白与标点 → 小写。"""
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", title)   # ｉＰｈｏｎｅ → iPhone
    s = _EMOJI_RE.sub("", s)
    s = _HASH_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)                   # 顺带去掉空格
    return s.lower()


def canonical_key(title: str) -> str:
    norm = normalize_title(title)
    if len(norm) < MIN_CANONICAL_LEN:
        # 极短标题不走跨源合并：用原文（含来源无关）单独成键，避免误合并
        norm = f"_short_{norm}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# -------------------------------------------------------------
# 工具
# -------------------------------------------------------------
def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _dicts(cur) -> list[dict]:
    """把游标结果转成 dict 列表，且不依赖调用方是否设置了 row_factory。

    data/clean_chunk.py 用的是裸 sqlite3 连接（tuple 行），api 用 sqlite3.Row。
    库函数如果假设 Row，会在脚本侧直接崩（已踩过一次）。
    """
    cols = [d[0] for d in (cur.description or [])]
    out = []
    for r in cur.fetchall():
        out.append(dict(r) if hasattr(r, "keys") else dict(zip(cols, r)))
    return out


def _rank_norm(rank: int | None) -> float:
    """同源内排名 → 0-100。rank 1 = 100 分，越靠后越低（参考深度 20）。"""
    r = max(1, int(rank or 1))
    return max(0.0, 100.0 * (1.0 - (r - 1) / SOURCE_RANK_DEPTH))


def _coverage_factor(source_count: int) -> float:
    """覆盖度加权：单源 0.9、双源及以上 1.0（多源同时出现 = 真趋势）。"""
    return min(1.0, 0.8 + 0.1 * max(1, source_count))


def _derive_trend(prev_rank, prev_heat, new_rank, new_heat) -> str:
    if prev_rank is None and prev_heat is None:
        return "平稳"          # 首轮无上轮值
    d_rank = (prev_rank or 999) - (new_rank or 999)      # >0 = 排名上升
    d_heat = (new_heat or 0) - (prev_heat or 0)
    if d_rank >= TREND_RANK_DELTA or d_heat >= TREND_HEAT_DELTA:
        return "上升"
    if d_rank <= -TREND_RANK_DELTA or d_heat <= -TREND_HEAT_DELTA:
        return "下降"
    return "平稳"


# -------------------------------------------------------------
# 写入 raw 层
# -------------------------------------------------------------
def insert_raw(conn, items: list[dict], batch_id: str,
               now: datetime | None = None) -> int:
    """把采集条目写入 raw 层。同批次同源同标题幂等（唯一约束 + OR IGNORE）。"""
    ts = _fmt(_now(now))
    n = 0
    for it in items:
        cur = conn.execute(
            "INSERT OR IGNORE INTO hot_raw_items"
            "(source_code,title,summary,url,rank,raw_heat,batch_id,fetched_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (it.get("source_code", ""), (it.get("title") or "").strip(),
             it.get("summary") or "", it.get("url") or "", it.get("rank"),
             it.get("raw_heat"), batch_id, ts),
        )
        n += max(0, cur.rowcount)
    conn.commit()
    return n


def _load_rows(conn, batch_id: str | None, now: datetime):
    """取用于聚合的 raw 条目。

    - 指定 batch_id（种子/单批调试）：只取该批次
    - 否则：取每个来源的「最新一批」快照，并优先限制在新鲜窗口内
      （每源独立节奏，所以不能用单一批次，否则 source_count 恒为 1）
    - 新鲜窗口内无数据时，退回"各源最新快照"（不设时限）——
      这是二级兜底：宁可展示旧数据并标注降级，也不要空屏
    """
    sql_cols = ("SELECT source_code,title,summary,url,rank,raw_heat,batch_id,fetched_at "
                "FROM hot_raw_items ")
    if batch_id:
        return _dicts(conn.execute(sql_cols + "WHERE batch_id=?", (batch_id,)))

    latest_join = (
        "JOIN (SELECT source_code, MAX(batch_id) AS mb FROM hot_raw_items "
        "      GROUP BY source_code) t "
        "  ON r.source_code=t.source_code AND r.batch_id=t.mb "
    )
    cutoff = _fmt(now - timedelta(minutes=HOTSPOT_SOURCE_STALE_MINUTES))
    rows = _dicts(conn.execute(
        "SELECT r.source_code,r.title,r.summary,r.url,r.rank,r.raw_heat,r.batch_id,r.fetched_at "
        "FROM hot_raw_items r " + latest_join + "WHERE r.fetched_at >= ?",
        (cutoff,),
    ))
    if rows:
        return rows
    return _dicts(conn.execute(
        "SELECT r.source_code,r.title,r.summary,r.url,r.rank,r.raw_heat,r.batch_id,r.fetched_at "
        "FROM hot_raw_items r " + latest_join
    ))


# -------------------------------------------------------------
# 聚合：raw → signals
# -------------------------------------------------------------
def aggregate(conn, batch_id: str | None = None,
              now: datetime | None = None) -> int:
    """把 raw 条目聚合进 hot_signals（upsert），返回写入/更新的 signal 数。"""
    now = _now(now)
    now_str = _fmt(now)
    rows = _load_rows(conn, batch_id, now)
    if not rows:
        return 0

    # 先取旧值：trend 需要"上轮"对比，而 UPSERT 的 SET 只能看到旧行或新值
    old = {
        r["canonical_key"]: r
        for r in _dicts(conn.execute(
            "SELECT canonical_key,best_rank,norm_heat,first_seen_at FROM hot_signals"
        ))
    }

    groups: dict[str, list[dict]] = {}
    for d in rows:
        if not (d.get("title") or "").strip():
            continue
        groups.setdefault(canonical_key(d["title"]), []).append(d)

    written = 0
    for key, items in groups.items():
        # 展示标题取「覆盖源中 rank 最优」的那条原题——
        # 首次出现的那条可能恰好是带 # 或截断最脏的一条
        best = min(items, key=lambda x: (x.get("rank") or 999, len(x.get("title") or "")))
        sources = sorted({i["source_code"] for i in items})
        source_count = len(sources)

        summary = ""
        for i in sorted(items, key=lambda x: (x.get("rank") or 999)):
            if (i.get("summary") or "").strip():
                summary = i["summary"].strip()
                break
        if not summary:
            # 前端 line-clamp-3 依赖该字段，禁止 NULL
            summary = f"「{source_label(best['source_code'])}」热榜话题"

        base_heat = max(_rank_norm(i.get("rank")) for i in items)
        norm_heat = round(min(100.0, base_heat * _coverage_factor(source_count)), 2)
        best_rank = min((i.get("rank") or 999) for i in items)

        prev = old.get(key)
        prev_rank = prev["best_rank"] if prev else None
        prev_heat = prev["norm_heat"] if prev else None
        trend = _derive_trend(prev_rank, prev_heat, best_rank, norm_heat)

        main_domain, matched = detect_domain(best["title"], summary)

        conn.execute(
            "INSERT INTO hot_signals"
            "(canonical_key,canonical_title,summary,source_count,sources,best_rank,"
            " best_raw_heat,norm_heat,trend,domain_hint,main_domain,"
            " first_seen_at,last_seen_at,source_url) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(canonical_key) DO UPDATE SET "
            " canonical_title=excluded.canonical_title,"
            " summary=excluded.summary,"
            " source_count=excluded.source_count,"
            " sources=excluded.sources,"
            " prev_best_rank=hot_signals.best_rank,"
            " prev_norm_heat=hot_signals.norm_heat,"
            " best_rank=excluded.best_rank,"
            " best_raw_heat=excluded.best_raw_heat,"
            " norm_heat=excluded.norm_heat,"
            " trend=excluded.trend,"
            " domain_hint=excluded.domain_hint,"
            " main_domain=excluded.main_domain,"
            " last_seen_at=excluded.last_seen_at,"
            " source_url=excluded.source_url",
            (key, best["title"], summary, source_count, ",".join(sources),
             best_rank, best.get("raw_heat"), norm_heat, trend,
             ",".join(matched), main_domain,
             now_str, now_str, best.get("url") or ""),
        )
        written += 1
    conn.commit()
    return written


# -------------------------------------------------------------
# 物化：signals → hotspots
# -------------------------------------------------------------
def materialize(conn, now: datetime | None = None) -> dict:
    """把在榜 signal 物化到 hotspots（F2 直接读这张表），并把退榜项置 is_active=0。"""
    now = _now(now)
    now_str = _fmt(now)
    ttl_cutoff = _fmt(now - timedelta(hours=HOTSPOT_SIGNAL_TTL_HOURS))
    new_cutoff = _fmt(now - timedelta(hours=HOTSPOT_NEW_WINDOW_HOURS))

    signals = _dicts(conn.execute(
        "SELECT * FROM hot_signals WHERE last_seen_at >= ? ORDER BY norm_heat DESC",
        (ttl_cutoff,),
    ))

    active_keys: list[str] = []
    for s in signals:
        is_new = 1 if (s["first_seen_at"] or "") >= new_cutoff else 0
        conn.execute(
            "INSERT INTO hotspots"
            "(signal_id,title,summary,source,platform,url,domain,heat,norm_heat,"
            " source_count,trend,first_seen_at,is_new,suggestion,dedup_hash,"
            " collected_at,is_active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(dedup_hash) DO UPDATE SET "
            " signal_id=excluded.signal_id,"
            " title=excluded.title,"
            " summary=excluded.summary,"
            " source=excluded.source,"
            " platform=excluded.platform,"
            " url=excluded.url,"
            " domain=excluded.domain,"
            " heat=excluded.heat,"
            " norm_heat=excluded.norm_heat,"
            " source_count=excluded.source_count,"
            " trend=excluded.trend,"
            " first_seen_at=excluded.first_seen_at,"
            " is_new=excluded.is_new,"
            " suggestion=COALESCE(excluded.suggestion, hotspots.suggestion),"
            " collected_at=excluded.collected_at,"
            " is_active=1",
            (s["id"], s["canonical_title"], s["summary"],
             source_label((s["sources"] or "").split(",")[0]) if s["sources"] else "热榜",
             (s["sources"] or "").split(",")[0] if s["sources"] else None,
             s["source_url"], s["main_domain"] or "综合",
             s["best_raw_heat"] or 0, s["norm_heat"], s["source_count"],
             s["trend"], s["first_seen_at"], is_new, s["suggestion"],
             s["canonical_key"], now_str),
        )
        active_keys.append(s["canonical_key"])

    # 退榜：不删除（topics.hotspot_id 有外键），只置 is_active=0
    if active_keys:
        placeholders = ",".join("?" * len(active_keys))
        retired = conn.execute(
            "UPDATE hotspots SET is_active=0 "
            f"WHERE is_active=1 AND signal_id IS NOT NULL AND dedup_hash NOT IN ({placeholders})",
            tuple(active_keys),
        ).rowcount
    else:
        retired = conn.execute(
            "UPDATE hotspots SET is_active=0 WHERE is_active=1 AND signal_id IS NOT NULL"
        ).rowcount

    conn.commit()
    return {"active": len(active_keys), "retired": max(0, retired)}


# -------------------------------------------------------------
# 一键管道
# -------------------------------------------------------------
def run_pipeline(conn, items: list[dict], batch_id: str,
                 now: datetime | None = None) -> dict:
    """写入 raw → 聚合 signal → 物化 hotspots。返回各阶段计数。"""
    inserted = insert_raw(conn, items, batch_id, now=now)
    signals = aggregate(conn, batch_id if inserted else None, now=now)
    mat = materialize(conn, now=now)
    return {"inserted": inserted, "signals": signals, **mat}
