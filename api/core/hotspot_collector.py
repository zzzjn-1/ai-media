# -*- coding: utf-8 -*-
"""
api/core/hot_collector.py — 热点采集调度器（P1-4 / P1-10 / P1-13 重写）

与旧版（每 60 分钟跑一次 mock 源）的区别：
  1. 每源独立节奏（间隔来自 hot_sources.SOURCES，不再有全局 INTERVAL 配置）
  2. batch_id 秒级 + 全局采集锁 → 同一分钟内两次采集不会被唯一约束静默吞掉
  3. 熔断退避：连续失败 3 次进入指数退避，退避期内跳过该源
  4. 采集日志落库（hot_collect_log）→ 成功率与 P95 耗时有唯一数据来源
  5. 每日清理 raw 与日志（追加式表不清必膨胀：6 源×10 条按 30 分钟/轮
     ≈ 2880 行/天、52 万行/半年）
  6. seed_if_empty() 降级为最后一级兜底，且改走 raw→signal→物化同一条链路

⚠️ 聚合必须用 aggregate(conn, None)（各源最新快照），不能用 batch_id：
   分源独立调度下每批通常只有一个源，按批次聚合会让 source_count 恒为 1，
   跨源合并直接失效。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import (
    HOTSPOT_EVENT_RETENTION_DAYS,
    HOTSPOT_LOG_RETENTION_DAYS,
    HOTSPOT_MAX_SOURCES_PER_REFRESH,
    HOTSPOT_MIN_REFRESH_INTERVAL,
    HOTSPOT_RAW_RETENTION_DAYS,
    HOTSPOT_SOURCE_STALE_MINUTES,
    HOT_SOURCES_ENABLED,
)
from ..database import get_conn
from .hot_seed import SEED_BATCH, SEED_ITEMS
from .hot_signal import aggregate, insert_raw, materialize, run_pipeline
from .hot_sources import SOURCES, fetch_many

# 熔断参数
FAIL_STREAK_THRESHOLD = 3      # 连续失败几次进入退避
BACKOFF_BASE_SECONDS = 300     # 首次退避 5 分钟，之后翻倍
BACKOFF_MAX_SECONDS = 3600     # 最长 1 小时

_lock = threading.Lock()
_current_batch: str | None = None


# -------------------------------------------------------------
# 工具
# -------------------------------------------------------------
def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def make_batch_id(now: datetime | None = None) -> str:
    """秒级批次号。分钟级会让同分钟内两次采集共用 batch_id，后一次被唯一约束吞掉。"""
    return (now or datetime.now()).strftime("%Y%m%dT%H%M%S")


def _unique_batch_id(conn, now: datetime | None = None) -> str:
    """秒级基础上再保证全局唯一：同一秒内的第二次采集追加 -N 后缀。

    秒级已经解决了"同分钟互相吞数据"，但同一秒（例如并发重试或测试）仍可能撞号，
    撞号的表现是后一批被 (source_code,title,batch_id) 唯一约束静默丢弃——没有报错，
    只是数据不见了，属于最难查的一类。
    """
    base = make_batch_id(now)
    candidate = base
    n = 1
    while conn.execute(
        "SELECT 1 FROM hot_collect_log WHERE batch_id=? LIMIT 1", (candidate,)
    ).fetchone():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def enabled_sources() -> list[str]:
    """配置里声明且注册表认识的来源（过滤掉拼写错误，避免整轮静默为空）。"""
    return [s for s in HOT_SOURCES_ENABLED if s in SOURCES]


def _init_state(conn, code: str) -> dict:
    row = conn.execute(
        "SELECT source_code,last_ok_at,last_error,fail_streak,backoff_until,total_fetched "
        "FROM hot_source_state WHERE source_code=?", (code,)
    ).fetchone()
    if row:
        return dict(row)
    conn.execute("INSERT OR IGNORE INTO hot_source_state(source_code) VALUES(?)", (code,))
    conn.commit()
    return {"source_code": code, "last_ok_at": None, "last_error": None,
            "fail_streak": 0, "backoff_until": None, "total_fetched": 0}


def in_backoff(conn, code: str, now: datetime | None = None) -> bool:
    st = _init_state(conn, code)
    bu = st.get("backoff_until")
    return bool(bu and bu > _fmt(now or datetime.now()))


# -------------------------------------------------------------
# 采集核心
# -------------------------------------------------------------
def collect_sources(sources: list[str] | None = None, verbose: bool = True) -> dict:
    """采集指定来源并跑完整管道。并发调用时复用正在跑的批次（不新开）。"""
    global _current_batch
    codes = [s for s in (sources or enabled_sources()) if s in SOURCES]

    if not _lock.acquire(blocking=False):
        return {"ok": True, "reused": True, "batch_id": _current_batch,
                "collected": 0, "by_source": {}, "failed": [],
                "degraded": False, "elapsed_ms": 0}

    t0 = datetime.now()
    conn = get_conn()
    batch_id = _unique_batch_id(conn, t0)
    _current_batch = batch_id
    by_source: dict[str, int] = {}
    failed: list[dict] = []
    skipped: list[str] = []
    total = 0
    try:
        now = datetime.now()
        runnable = []
        for code in codes:
            if in_backoff(conn, code, now):
                skipped.append(code)
                continue
            runnable.append(code)

        results = fetch_many(runnable) if runnable else {}
        for code, res in results.items():
            items, err, ms = res["items"], res["error"], res["elapsed_ms"]
            st = _init_state(conn, code)
            # created_at 必须显式传本地时间：表默认值是 sqlite 的 datetime('now')（UTC），
            # 而频控与 7 天成功率窗口都按本地 datetime.now() 计算，混用会让"上次成功"
            # 永远比现在早 8 小时，最小刷新间隔形同虚设（实测可无限连刷）。
            conn.execute(
                "INSERT INTO hot_collect_log(batch_id,source_code,ok,item_count,elapsed_ms,error,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (batch_id, code, 0 if err else 1, len(items), ms, (err or "")[:500], _fmt(now)),
            )
            if err:
                streak = int(st.get("fail_streak") or 0) + 1
                backoff = None
                if streak >= FAIL_STREAK_THRESHOLD:
                    delay = min(BACKOFF_MAX_SECONDS,
                                BACKOFF_BASE_SECONDS * (2 ** (streak - FAIL_STREAK_THRESHOLD)))
                    backoff = _fmt(now + timedelta(seconds=delay))
                conn.execute(
                    "UPDATE hot_source_state SET fail_streak=?,last_error=?,backoff_until=? "
                    "WHERE source_code=?",
                    (streak, (err or "")[:500], backoff, code),
                )
                failed.append({"source": code, "error": (err or "")[:200]})
                continue
            n = insert_raw(conn, items, batch_id)
            conn.execute(
                "UPDATE hot_source_state SET last_ok_at=?,last_error=NULL,fail_streak=0,"
                "backoff_until=NULL,total_fetched=total_fetched+? WHERE source_code=?",
                (_fmt(now), n, code),
            )
            by_source[code] = n
            total += n
        conn.commit()

        # 聚合走「各源最新快照」，不是本批次——否则分源调度下 source_count 恒为 1
        signals = aggregate(conn, None)
        mat = materialize(conn)
        degraded = bool(runnable) and not by_source
        elapsed = int((datetime.now() - t0).total_seconds() * 1000)
        if verbose:
            print(f"  [热点采集] 批次 {batch_id}：{total} 条 / {signals} signals"
                  f"（在榜 {mat['active']}，退榜 {mat['retired']}）"
                  f"{' | 跳过(退避)=' + ','.join(skipped) if skipped else ''}"
                  f"{' | 失败=' + ','.join(f['source'] for f in failed) if failed else ''}")
        return {"ok": True, "reused": False, "batch_id": batch_id,
                "collected": total, "by_source": by_source, "failed": failed,
                "skipped": skipped, "signals": signals, "active": mat["active"],
                "degraded": degraded, "elapsed_ms": elapsed}
    finally:
        conn.close()
        _current_batch = None
        _lock.release()


# -------------------------------------------------------------
# 手动刷新（供 POST /api/hotspots/refresh）
# -------------------------------------------------------------
def last_success_at(conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(created_at) FROM hot_collect_log WHERE ok=1"
    ).fetchone()
    return row[0] if row else None


def refresh(sources: list[str] | None = None, force: bool = False) -> tuple[dict, int]:
    """返回 (payload, http_status)。

    频控基准是「任意来源的上次成功采集（含自动调度）」——只算手动触发会让
    用户点一下就把自动调度的成果判成"太频繁"。
    """
    codes = [s for s in (sources or enabled_sources())]
    unknown = [c for c in codes if c not in SOURCES]
    if unknown:
        return {"ok": False, "msg": f"未知来源：{unknown}", "allowed": list(SOURCES)}, 400
    if len(codes) > HOTSPOT_MAX_SOURCES_PER_REFRESH:
        return {"ok": False,
                "msg": f"单次最多采集 {HOTSPOT_MAX_SOURCES_PER_REFRESH} 个来源，实际 {len(codes)}",
                "allowed": list(SOURCES)}, 400
    if not codes:
        return {"ok": False, "msg": "没有启用任何来源"}, 400

    conn = get_conn()
    try:
        if not force:
            last = last_success_at(conn)
            if last:
                delta = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                if delta < HOTSPOT_MIN_REFRESH_INTERVAL:
                    # 秒数直接写进 msg：前端只消费 msg（client 层不透传额外字段），
                    # 不含秒数的话用户看到的就是一句无法行动的「稍后再试」
                    retry = max(1, int(HOTSPOT_MIN_REFRESH_INTERVAL - delta))
                    return {"ok": False, "msg": f"刷新过于频繁，请 {retry} 秒后再试",
                            "retry_after_seconds": retry}, 429
    finally:
        conn.close()

    result = collect_sources(codes)
    return result, 200


# -------------------------------------------------------------
# 兜底种子
# -------------------------------------------------------------
def seed_if_empty(verbose: bool = True) -> int:
    """库为空时的最后一级兜底：写 raw → 聚合 → 物化（与真实采集同一条链路）。

    不直接 INSERT hotspots ——否则种子数据没有 signal_id / first_seen_at，
    默认视图与真实数据行为不一致，是最难排查的一类分裂。
    """
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM hotspots WHERE is_active=1").fetchone()[0]
        if n > 0:
            return 0
        r = run_pipeline(conn, SEED_ITEMS, SEED_BATCH)
        if verbose:
            print(f"  [热点采集] 库为空，注入内置种子 {r['inserted']} 条"
                  f"（signals {r['signals']}，在榜 {r['active']}）")
        return r["inserted"]
    finally:
        conn.close()


# -------------------------------------------------------------
# 新鲜度 / 健康状态
# -------------------------------------------------------------
def freshness(conn=None) -> dict:
    own = conn is None
    conn = conn or get_conn()
    try:
        now = datetime.now()
        row = conn.execute(
            "SELECT MAX(collected_at) AS ts FROM hotspots WHERE is_active=1"
        ).fetchone()
        updated_at = row["ts"] if row else None
        age_minutes = None
        if updated_at:
            try:
                age_minutes = int(
                    (now - datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                )
            except ValueError:
                age_minutes = None

        stale_cutoff = _fmt(now - timedelta(minutes=HOTSPOT_SOURCE_STALE_MINUTES))
        states = conn.execute(
            "SELECT source_code,last_ok_at,fail_streak,backoff_until,total_fetched "
            "FROM hot_source_state"
        ).fetchall()
        state_map = {r["source_code"]: dict(r) for r in states}
        enabled = enabled_sources()
        sources_ok = sum(
            1 for c in enabled
            if (state_map.get(c, {}).get("last_ok_at") or "") >= stale_cutoff
        )
        degraded = sources_ok == 0 or (
            age_minutes is not None and age_minutes > HOTSPOT_SOURCE_STALE_MINUTES
        )
        return {
            "updated_at": updated_at,
            "age_minutes": age_minutes,
            "degraded": degraded,
            "sources_ok": sources_ok,
            "sources_total": len(enabled),
        }
    finally:
        if own:
            conn.close()


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return int(ordered[idx])


def status() -> dict:
    """采集健康状态 + 近 7 天成功率与 P95（T-14 的接口层验收入口）。"""
    conn = get_conn()
    try:
        fresh = freshness(conn)
        states = [dict(r) for r in conn.execute(
            "SELECT source_code,last_ok_at,last_error,fail_streak,backoff_until,total_fetched "
            "FROM hot_source_state ORDER BY source_code"
        ).fetchall()]
        since = _fmt(datetime.now() - timedelta(days=7))
        logs = conn.execute(
            "SELECT ok,elapsed_ms FROM hot_collect_log WHERE created_at >= ?", (since,)
        ).fetchall()
        total = len(logs)
        ok_count = sum(1 for r in logs if r["ok"] == 1)
        elapsed = [int(r["elapsed_ms"]) for r in logs
                   if r["elapsed_ms"] is not None and r["ok"] == 1]
        return {
            "ok": True,
            "freshness": fresh,
            "sources": states,
            "window_days": 7,
            "total_runs": total,
            "success_rate": round(ok_count / total, 4) if total else None,
            "p95_elapsed_ms": _p95(elapsed),
        }
    finally:
        conn.close()


# -------------------------------------------------------------
# 清理（P1-13）
# -------------------------------------------------------------
def cleanup_old_data(verbose: bool = True, conn=None) -> dict:
    """清 raw / 采集日志 / 推荐流事件；signals/hotspots 由 TTL 退榜机制处理，不动。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        now = datetime.now()
        raw_cutoff = _fmt(now - timedelta(days=HOTSPOT_RAW_RETENTION_DAYS))
        log_cutoff = _fmt(now - timedelta(days=HOTSPOT_LOG_RETENTION_DAYS))
        evt_cutoff = _fmt(now - timedelta(days=HOTSPOT_EVENT_RETENTION_DAYS))
        raw_del = conn.execute(
            "DELETE FROM hot_raw_items WHERE fetched_at < ?", (raw_cutoff,)
        ).rowcount
        log_del = conn.execute(
            "DELETE FROM hot_collect_log WHERE created_at < ?", (log_cutoff,)
        ).rowcount
        evt_del = conn.execute(
            "DELETE FROM hot_feed_event WHERE created_at < ?", (evt_cutoff,)
        ).rowcount
        conn.commit()
        if verbose:
            print(f"  [热点清理] raw -{max(0, raw_del)} 行（保留 {HOTSPOT_RAW_RETENTION_DAYS} 天），"
                  f"日志 -{max(0, log_del)} 行（保留 {HOTSPOT_LOG_RETENTION_DAYS} 天），"
                  f"事件 -{max(0, evt_del)} 行（保留 {HOTSPOT_EVENT_RETENTION_DAYS} 天）")
        return {"raw_deleted": max(0, raw_del), "log_deleted": max(0, log_del),
                "event_deleted": max(0, evt_del)}
    finally:
        if own:
            conn.close()


# -------------------------------------------------------------
# 调度
# -------------------------------------------------------------
_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _job_collect(code: str):
    try:
        collect_sources([code])
    except Exception as e:  # noqa: BLE001 —— job 抛异常会让后续触发停摆
        print(f"  [热点采集] 源 {code} 调度异常：{type(e).__name__}: {e}")


def _job_cleanup():
    try:
        cleanup_old_data()
    except Exception as e:  # noqa: BLE001
        print(f"  [热点清理] 异常：{type(e).__name__}: {e}")


def _job_initial_refresh():
    """启动后补采一次：昨天关掉的机器，今天打开应该已经有数据。"""
    try:
        collect_sources()
    except Exception as e:  # noqa: BLE001
        print(f"  [热点采集] 启动补采异常：{type(e).__name__}: {e}")


def start_scheduler():
    if _scheduler.running:
        return
    seed_if_empty()
    codes = enabled_sources()
    for code in codes:
        minutes = max(1, int(SOURCES[code].get("interval_min", 15)))
        _scheduler.add_job(
            _job_collect, "interval", minutes=minutes, args=[code],
            id=f"hot_collect_{code}", replace_existing=True,
            max_instances=1, coalesce=True,
        )
    _scheduler.add_job(_job_cleanup, "cron", hour=4, minute=10,
                       id="hot_cleanup", replace_existing=True)
    _scheduler.add_job(_job_initial_refresh, "date",
                       run_date=datetime.now() + timedelta(seconds=3),
                       id="hot_initial_refresh", replace_existing=True)
    _scheduler.start()
    print("  [调度] 热点采集已启动（分源节奏）："
          + "，".join(f"{c}/{SOURCES[c]['interval_min']}min" for c in codes))


def shutdown_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
