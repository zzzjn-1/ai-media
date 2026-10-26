# -*- coding: utf-8 -*-
"""api/core/hot_metrics.py — 推荐流事件与采纳率（P2-7）

为什么单独一个模块：这是**唯一**一个「只关乎产品判断、不关乎推荐算法」的落库点。
放在这里而不是塞进 hot_signal / hot_rank，是因为那两块改一次就要重跑整条聚合链，
而埋点表将来一定会被加字段、改口径，不该跟聚合逻辑耦合。

三个动作（一条链路三级漏斗，任何一级缺失都只能自嗨）：
    expose   曝光：前端真正渲染出一屏时上报一次，带本次渲染条数 → 分母
    adopt    采纳：点了「用它做选题」→ 意图分子（弱）
    generate 出题：/topics/generate 带 signal_id 成功 → 真用了（强，服务端写，客户端伪造不了）

指标口径（写死在这里，避免各处各算一套）：
    CTR = adopt / expose 条数   —— 低于 5% 说明推荐不准，不是用户懒
    成稿率 = generate / adopt    —— 低于 50% 说明「点了却写不出来」，问题在选题质量
两个比率都必须同时看：只盯 CTR 会把推荐调成标题党。

★ 时间一律显式写本地时间（datetime.now()）。schema 里的 DEFAULT 是 UTC，
  与本地 now() 差 8 小时，"近 7 天"窗口会整体偏移——hot_collect_log 已经踩过这个坑。
"""
from datetime import datetime, timedelta

from ..config import HOTSPOT_ADOPT_DEDUPE_SECONDS

ACTIONS = ("expose", "adopt", "generate")
_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(_FMT)


def record_expose(conn, user_id: int, surface: str = "feed",
                  count: int = 0, domain: str | None = None) -> int:
    """一次渲染上报一条（不是一条热点一行）。

    为什么按"屏"记而不是按"条"记：曝光率的口径本来就是「这一屏被看了几次」，
    按条记等于把一条热点的多次重复刷屏拆成多行，既涨表又改不了结论。
    """
    count = max(0, min(int(count or 0), 100))
    if count == 0:
        return 0
    conn.execute(
        "INSERT INTO hot_feed_event(user_id,action,surface,item_count,domain,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (user_id, "expose", surface or "feed", count, domain, _now()),
    )
    conn.commit()
    return 1


def record_adopt(conn, user_id: int, hotspot_id: int,
                 surface: str | None = None, position: int | None = None) -> dict:
    """点「用它做选题」→ 记一条采纳。

    短窗去重：双击、点完返回再点，在 UI 上是同一个动作，不该算两次采纳；
    但隔天再点同一条（比如同一热点有新进展要复写）是真实行为，必须允许。
    """
    row = conn.execute(
        "SELECT id,signal_id,domain FROM hotspots WHERE id=?", (hotspot_id,)
    ).fetchone()
    if not row:
        return {"recorded": 0, "deduped": False, "miss": True}

    since = (datetime.now() - timedelta(seconds=HOTSPOT_ADOPT_DEDUPE_SECONDS)).strftime(_FMT)
    dup = conn.execute(
        "SELECT 1 FROM hot_feed_event WHERE user_id=? AND action='adopt' "
        "AND hotspot_id=? AND created_at>=? LIMIT 1",
        (user_id, hotspot_id, since),
    ).fetchone()
    if dup:
        return {"recorded": 0, "deduped": True, "miss": False}

    conn.execute(
        "INSERT INTO hot_feed_event(user_id,action,hotspot_id,signal_id,surface,"
        "position,item_count,domain,created_at) VALUES(?,?,?,?,?,?,1,?,?)",
        (user_id, "adopt", hotspot_id, row["signal_id"], surface or "feed",
         position, row["domain"], _now()),
    )
    conn.commit()
    return {"recorded": 1, "deduped": False, "miss": False}


def record_generate(conn, user_id: int, signal_id: int,
                    hotspot_id: int | None = None, domain: str | None = None) -> int:
    """服务端侧记录"这条热点真的出了选题"。客户端只传 signal_id，骗不了这一层。

    同样做短窗去重：客户端常见的双发（React 严格模式把 effect 跑两遍、用户连点、
    失败重试）在用户心里是"我出过一次题"，不该让漏斗第三级翻倍——实测确实出现过
    一次点击记两条 generate，把 generate/adopt 算成了 2.0。
    """
    if hotspot_id is None and signal_id:
        r = conn.execute(
            "SELECT id,domain FROM hotspots WHERE signal_id=? ORDER BY id DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        if r:
            hotspot_id, domain = r["id"], r["domain"] or domain

    if hotspot_id:
        since = (datetime.now() - timedelta(seconds=HOTSPOT_ADOPT_DEDUPE_SECONDS)).strftime(_FMT)
        dup = conn.execute(
            "SELECT 1 FROM hot_feed_event WHERE user_id=? AND action='generate' "
            "AND hotspot_id=? AND created_at>=? LIMIT 1",
            (user_id, hotspot_id, since),
        ).fetchone()
        if dup:
            return 0

    conn.execute(
        "INSERT INTO hot_feed_event(user_id,action,hotspot_id,signal_id,surface,"
        "item_count,domain,created_at) VALUES(?,?,?,?,?,1,?,?)",
        (user_id, "generate", hotspot_id, signal_id, "topics", domain, _now()),
    )
    conn.commit()
    return 1


def adoption_summary(conn, days: int = 7) -> dict:
    """近 N 天采纳漏斗。没有分母时 ratio 返回 None——不要用 0 假装"没人点"。"""
    since = (datetime.now() - timedelta(days=days)).strftime(_FMT)
    rows = conn.execute(
        "SELECT action, COUNT(*) AS n, COALESCE(SUM(item_count),0) AS s "
        "FROM hot_feed_event WHERE created_at >= ? GROUP BY action",
        (since,),
    ).fetchall()
    by = {r["action"]: (int(r["n"]), int(r["s"] or 0)) for r in rows}

    impressions = by.get("expose", (0, 0))[1]
    adopts = by.get("adopt", (0, 0))[0]
    generates = by.get("generate", (0, 0))[0]
    users = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM hot_feed_event WHERE created_at >= ?",
        (since,),
    ).fetchone()[0]

    top = [dict(r) for r in conn.execute(
        "SELECT e.hotspot_id, MAX(e.signal_id) AS signal_id, MAX(e.domain) AS domain, "
        "       COUNT(*) AS adopts, MAX(h.title) AS title "
        "FROM hot_feed_event e LEFT JOIN hotspots h ON h.id = e.hotspot_id "
        "WHERE e.action='adopt' AND e.created_at >= ? "
        "GROUP BY e.hotspot_id ORDER BY adopts DESC LIMIT 5",
        (since,),
    ).fetchall()]

    return {
        "window_days": days,
        "impressions": impressions,
        "adopts": adopts,
        "generates": generates,
        "ctr": round(adopts / impressions, 4) if impressions else None,
        "adopt_to_generate": round(generates / adopts, 4) if adopts else None,
        "active_users": int(users or 0),
        "top_adopted": top,
        # 口径说明随数据一起返回：这个数被解读错，比没有这个数更糟
        "note": "ctr=adopt/expose 条数（<5% 多为推荐不准）；"
                "adopt_to_generate=generate/adopt（<50% 多为选题不可写）；"
                "两者需同时看，单看 ctr 会把推荐调成标题党",
    }


def cleanup(conn, days: int) -> int:
    """事件表保留窗口。比 raw 层长得多（指标要看趋势），但不能无限涨。"""
    cutoff = (datetime.now() - timedelta(days=max(1, days))).strftime(_FMT)
    n = conn.execute("DELETE FROM hot_feed_event WHERE created_at < ?", (cutoff,)).rowcount
    conn.commit()
    return max(0, n)
