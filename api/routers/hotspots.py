# -*- coding: utf-8 -*-
"""api/routers/hotspots.py — 热点看板（F1.4，计划书 2.1.1 / 4.3）

四个端点：
  GET  /api/hotspots         公开可读；带 token 时按账号领域做个性化排序 + 标记已采纳
  POST /api/hotspots/refresh 需鉴权 + 最小间隔 + 来源数上限 + 批次复用
  GET  /api/hotspots/status  需鉴权；来源健康 + 近 7 天成功率/P95 + 采纳漏斗
  POST /api/hotspots/events  需鉴权；上报曝光/采纳（采纳率的分母与分子）

向后兼容：`title/summary/source/url/domain/heat/trend/collected_at` 全部保留，
api/_demo.py 与 api/_e2e_test.py 不需要改代码即可继续跑。
"""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from ..core import hotspot_collector
from ..core.hot_metrics import adoption_summary, record_adopt, record_expose
from ..core.hot_rank import get_user_domains, rank_feed
from ..core.hot_signal import materialize
from ..core.hot_suggestion import ensure as ensure_suggestions
from ..database import get_conn
from ..deps import get_current_user, get_current_user_optional

router = APIRouter(prefix="/hotspots", tags=["hotspots"])

_FIELDS = (
    "id,signal_id,title,summary,source,platform,url,domain,heat,norm_heat,"
    "source_count,trend,first_seen_at,is_new,suggestion,collected_at,"
    # 平台内原始排名：前端展示「微博榜第 3 名」用，跨源热度本身不可比，
    # 排名比热度值更能说明"这条在它自己那个榜上有多靠前"
    "(SELECT best_rank FROM hot_signals s WHERE s.id=hotspots.signal_id) AS best_rank"
)


def _query(conn, view: str, domain: str | None, platform: str | None,
           new_only: bool, limit: int, include_misc: bool = True,
           user_id: int | None = None) -> list[dict]:
    # 「我是否已经用这条出过选题」——把记录变成用户看得见的价值：
    # 懒人博主的真实痛点不是找不到热点，是同一个热点写了两遍。
    # 未登录传 -1（不可能命中的 user_id），避免为一处空白拆出两套 SELECT。
    uid = int(user_id) if user_id else -1
    fields = (
        f"{_FIELDS}, "
        "(SELECT 1 FROM hot_feed_event e WHERE e.user_id=? AND e.action='adopt' "
        " AND e.hotspot_id=hotspots.id LIMIT 1) AS adopted"
    )
    where = ["is_active=1"]
    args: list = []
    if domain:
        where.append("domain=?")
        args.append(domain)
    if platform:
        where.append("platform=?")
        args.append(platform)
    if new_only:
        where.append("is_new=1")
    # 推荐流默认剔除「综合」：真实热榜里 70%+ 是时政/体育/娱乐/社会新闻，
    # 对 5 个垂类的创作者不可用（且时政内容有合规风险）。
    # 想看全量走 view=all 或显式 include_misc=true。
    if not include_misc:
        where.append("coalesce(domain,'综合')<>'综合'")
    order = "collected_at DESC, id ASC" if view == "feed" else "norm_heat DESC, id ASC"
    rows = conn.execute(
        f"SELECT {fields} FROM hotspots WHERE {' AND '.join(where)} "
        f"ORDER BY {order} LIMIT ?",
        (uid, *args, max(1, min(limit, 100))),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["adopted"] = bool(d.get("adopted"))
        out.append(d)
    return out


@router.get("")
def list_hotspots(
    view: str = Query("feed", pattern="^(feed|all)$"),
    domain: str | None = None,
    platform: str | None = None,
    new_only: bool = False,
    limit: int = 20,
    include_misc: bool | None = Query(
        None, description="是否包含「综合」（未命中 5 领域的）条目；feed 默认 false，all 默认 true"),
    user: dict | None = Depends(get_current_user_optional),
):
    # include_misc 未显式传入时按视图取默认：推荐流只看能写的，全榜看全部
    if include_misc is None:
        include_misc = (view == "all")
    conn = get_conn()
    try:
        uid = user["id"] if user else None
        # feed 视图要先取足够宽的候选池再打分，否则"先按时间取 20 条再排序"
        # 会把老但高相关的条目永久挡在门外
        pool = 100 if view == "feed" else max(1, min(limit, 100))
        rows = _query(conn, view, domain, platform, new_only, pool, include_misc, uid)

        # 三级兜底：① 实时源（由调度器负责）→ ② DB 已有数据（上面已查）→ ③ 内置种子
        if not rows:
            hotspot_collector.seed_if_empty(verbose=False)
            # 已有 signal 但尚未物化（例如采集刚写入、物化被打断）时补一次
            materialize(conn)
            rows = _query(conn, view, domain, platform, new_only, pool, include_misc, uid)
            # 剔除「综合」后可能一条都不剩（新库 + 热点全为新闻）——此时放开再查一次，
            # 宁可展示不相关也不能空屏（空屏率=0 是一期硬指标）
            if not rows and not include_misc:
                rows = _query(conn, view, domain, platform, new_only, pool, True, uid)

        fresh = hotspot_collector.freshness(conn)

        # 注：曾有"命中垂类不足 N 条就用「综合」补足"的想法，实测后否决——
        # 真实热榜的「综合」几乎全是时政/体育/娱乐，补进来等于把刚过滤掉的东西
        # 换个位置塞回去，且时政内容有合规风险。条数少时由前端明确说明并给「全部」入口，
        # 让用户自己决定要不要看，比替他做决定更诚实。

        if view == "feed":
            domains, _positioning = get_user_domains(conn, user["id"] if user else None)
            rows = rank_feed(rows, domains, now=datetime.now())[:max(1, min(limit, 100))]
        else:
            rows = rows[:max(1, min(limit, 100))]
            # 全榜不做个性化，避免用户误以为全榜也按账号排过序
            for r in rows:
                r.pop("relevance", None)
                r.pop("reason", None)

        # 建议角度：惰性补齐（只补这次真的要展示、且还没有建议的那几条）
        ensure_suggestions(conn, rows)

        return {
            "ok": True,
            "count": len(rows),
            "view": view,
            "personalized": bool(view == "feed" and user),
            "include_misc": include_misc,
            "freshness": fresh,
            "hotspots": rows,
        }
    finally:
        conn.close()


@router.post("/refresh")
def refresh_hotspots(
    payload: dict = Body(default={}),
    user=Depends(get_current_user),
):
    sources = payload.get("sources") or None
    if sources is not None and not isinstance(sources, list):
        return JSONResponse({"ok": False, "msg": "sources 必须是数组"}, status_code=400)
    force = bool(payload.get("force", False))
    result, status_code = hotspot_collector.refresh(sources, force=force)
    if status_code != 200:
        return JSONResponse(result, status_code=status_code)
    return {
        "ok": True,
        "batch_id": result.get("batch_id"),
        "reused": result.get("reused", False),
        "collected": result.get("collected", 0),
        "by_source": result.get("by_source", {}),
        "failed": result.get("failed", []),
        "skipped": result.get("skipped", []),
        "degraded": result.get("degraded", False),
        "elapsed_ms": result.get("elapsed_ms", 0),
    }


@router.post("/events")
def track_event(payload: dict = Body(default={}), user=Depends(get_current_user)):
    """上报推荐流事件（P2-7）。只收 expose / adopt。

    `generate` 不开放给客户端：那是最强的信号（真的出了选题），
    必须由 `/topics/generate` 在服务端写，否则前端一改代码就能把采纳率刷上去。
    """
    action = str(payload.get("action") or "").strip().lower()
    if action not in ("expose", "adopt"):
        return JSONResponse(
            {"ok": False, "msg": "action 只支持 expose / adopt（generate 由服务端出题时记录）"},
            status_code=400)

    conn = get_conn()
    try:
        if action == "expose":
            n = record_expose(conn, user["id"], payload.get("surface") or "feed",
                              payload.get("count") or 0, payload.get("domain"))
            return {"ok": True, "action": action, "recorded": n}

        try:
            hotspot_id = int(payload.get("hotspot_id"))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "msg": "adopt 需要 hotspot_id"},
                                status_code=400)
        res = record_adopt(conn, user["id"], hotspot_id,
                           payload.get("surface"), payload.get("position"))
        if res.get("miss"):
            return JSONResponse({"ok": False, "msg": "热点不存在"}, status_code=404)
        # recorded=0 且 deduped=true 时仍然 ok：前端不该因为重复点击弹错误
        return {"ok": True, "action": action, **res}
    finally:
        conn.close()


@router.get("/status")
def collect_status(user=Depends(get_current_user)):
    data = hotspot_collector.status()
    conn = get_conn()
    try:
        # 采集健康（"数据新不新"）和采纳漏斗（"推荐准不准"）放同一个入口，
        # 因为这两件事必须一起看：源全挂时 CTR 暴跌不代表推荐变差了
        data["adoption"] = adoption_summary(conn)
    finally:
        conn.close()
    return data
