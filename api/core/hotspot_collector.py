# -*- coding: utf-8 -*-
"""
api/core/hotspot_collector.py — 热点定时采集与去重（落实 T3.2 / F1.4 / 计划书 4.6）

- APScheduler 周期触发（默认 60 分钟），失败重试、不阻塞用户请求。
- dedup_hash 唯一约束去重（对齐 schema.hotspots.dedup_hash）。
- 默认内置 mock 热点源（离线可演示）；接入真实源时替换 _fetch_sources() 即可，
  外部采集源 URL 通过配置外置（计划书 2.2.5）。
"""
import hashlib
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import HOTSPOT_INTERVAL_MINUTES
from ..database import get_conn

# 内置离线热点源（演示用；真实接入替换为 RSS/API 拉取并保留 dedup_hash 去重）
_MOCK_SOURCES = [
    ("科技", "端侧大模型推理加速，终端 AI 迎来拐点", "示例科技媒体", "https://example.com/t1", 92, "上升"),
    ("科技", "开源向量数据库 RAG 检索增强成标配", "示例科技媒体", "https://example.com/t2", 85, "平稳"),
    ("生活", "一人食极简备餐法走红", "示例生活社区", "https://example.com/t3", 78, "上升"),
    ("生活", "通勤碎片化时间管理实操", "示例生活社区", "https://example.com/t4", 70, "平稳"),
    ("财经", "美联储加息预期扰动权益资产", "示例财经资讯", "https://example.com/t5", 95, "上升"),
    ("财经", "复利效应下的现金仓位再平衡", "示例财经资讯", "https://example.com/t6", 80, "平稳"),
]


def _dedup_hash(title: str, source: str) -> str:
    return hashlib.sha256(f"{title}|{source}".encode("utf-8")).hexdigest()


def collect_once(conn=None, verbose: bool = True) -> int:
    """执行一次采集：去重写入 hotspots；返回新增条数。"""
    own = conn is None
    conn = conn or get_conn()
    added = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for domain, title, source, url, heat, trend in _MOCK_SOURCES:
        dh = _dedup_hash(title, source)
        try:
            conn.execute(
                "INSERT INTO hotspots(title,summary,source,url,domain,heat,trend,dedup_hash,collected_at,is_active) "
                "VALUES(?,?,?,?,?,?,?,?,?,1)",
                (title, f"来自{source}的热点", source, url, domain, heat, trend, dh, now),
            )
            added += 1
        except Exception:
            conn.rollback()  # dedup_hash 唯一约束命中，跳过
    conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"  [热点采集] 本次新增 {added} 条（去重后）")
    return added


def seed_if_empty(verbose: bool = True) -> int:
    """首次启动若热点表为空，先灌入一批，保证看板可用。"""
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    if n == 0:
        added = collect_once(conn=conn, verbose=False)
        if verbose:
            print(f"  [热点采集] 初始种子热点 {added} 条")
        conn.close()
        return added
    conn.close()
    return 0


_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start_scheduler():
    if not _scheduler.running:
        seed_if_empty()
        _scheduler.add_job(
            collect_once, "interval",
            minutes=HOTSPOT_INTERVAL_MINUTES,
            id="hotspot_collect", replace_existing=True,
        )
        _scheduler.start()
        print(f"  [调度] 热点采集已启动，周期 {HOTSPOT_INTERVAL_MINUTES} 分钟")


def shutdown_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
