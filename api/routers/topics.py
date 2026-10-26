# -*- coding: utf-8 -*-
"""api/routers/topics.py — 选题策划与一键选用（F2，计划书 2.1.2 / 4.3）

P2-2：`/topics/generate` 支持 `signal_id`（热点看板「用它做选题」直传）。
传了就以该热点为主输入：标题进 prompt、主领域覆盖请求领域、并把 `topics.hotspot_id` 落地
（这张表的外键此前一直是 None，等于「选题→热点」的链路只存在于界面上）。
不传时行为与改造前完全一致——向后兼容是硬约束，`_e2e_test.py` 的 T-13 就守着这条。
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..database import get_conn
from ..deps import get_current_user
from ..core.hot_metrics import record_generate
from ..core.rag_pipeline import generate_topics

router = APIRouter(prefix="/topics", tags=["topics"])

# 未命中 5 个垂类时的领域值；它不能拿来当过滤条件（否则只能取到「综合」池）
MISC_DOMAIN = "综合"

_FOCUS_COLS = (
    "id,signal_id,title,domain,summary,suggestion,norm_heat,coalesce(heat,0) AS heat"
)


class GenerateReq(BaseModel):
    domain: str | None = None
    count: int = 3
    # 热点直传：建议传 hot_signals.id；为兼容前端也接受 hotspots.id（见 _load_focus）
    signal_id: int | None = None


def _load_focus(conn, signal_id: int | None) -> dict | None:
    """取用户点的那条热点；两种 id 都认，避免前端传错一个字段就整条链路断掉。"""
    if not signal_id:
        return None
    row = conn.execute(
        f"SELECT {_FOCUS_COLS} FROM hotspots "
        "WHERE signal_id=? AND is_active=1 ORDER BY id DESC LIMIT 1",
        (signal_id,),
    ).fetchone()
    if not row:
        # 退榜项也允许（用户可能在刷新前就点了），只是不再要求 is_active=1
        row = conn.execute(
            f"SELECT {_FOCUS_COLS} FROM hotspots WHERE id=?", (signal_id,)
        ).fetchone()
    return dict(row) if row else None


@router.post("/generate")
def generate(req: GenerateReq, user=Depends(get_current_user)):
    conn = get_conn()
    try:
        focus = _load_focus(conn, req.signal_id)
        if req.signal_id and not focus:
            return JSONResponse(
                {"ok": False, "msg": "该热点已下榜，请刷新热点看板后重试"}, status_code=404)

        # 热点主领域优先于请求领域：否则看板上点的是美妆、请求里残留科技，
        # 就会在科技热点池里出题——闭环看着通了，领域却是错的
        focus_domain = (focus or {}).get("domain")
        domain = focus_domain if focus_domain and focus_domain != MISC_DOMAIN else req.domain

        # 排序用 norm_heat（跨源可比），不再用 heat（跨源不可比的原始热度）
        sql = "SELECT id,title,domain,heat,norm_heat,suggestion FROM hotspots WHERE is_active=1 "
        args: tuple = ()
        if domain:
            sql += "AND domain=? "
            args = (domain,)
        sql += "ORDER BY norm_heat DESC, id ASC LIMIT 15"
        hotspots = [dict(r) for r in conn.execute(sql, args).fetchall()]

        if focus:
            hotspots = [h for h in hotspots if h["id"] != focus["id"]]
            hotspots.insert(0, {k: focus.get(k) for k in
                                ("id", "title", "domain", "heat", "norm_heat", "suggestion")})

        persona = conn.execute(
            "SELECT positioning, tone, domains FROM users WHERE id=?", (user["id"],)
        ).fetchone()
    finally:
        conn.close()

    persona_d = dict(persona) if persona else {}
    topics = generate_topics(hotspots, persona_d, domain, req.count, focus=focus)

    conn = get_conn()
    created = []
    try:
        for t in topics:
            cur = conn.execute(
                "INSERT INTO topics(user_id,hotspot_id,title,angle,reason,target_audience,status) "
                "VALUES(?,?,?,?,?,?,0)",
                (user["id"], focus["id"] if focus else None,
                 t.get("title", ""), t.get("angle", ""),
                 t.get("reason", ""), t.get("target_audience", "")),
            )
            created.append({"id": cur.lastrowid, **t})
        # 采纳漏斗的第三级：真的出了选题（服务端写，客户端伪造不了）
        if focus and created:
            record_generate(conn, user["id"], focus.get("signal_id") or req.signal_id,
                            hotspot_id=focus["id"], domain=domain)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "domain": domain, "signal_id": req.signal_id, "topics": created}


_LIST_COLS = """
    t.id, t.hotspot_id, t.title, t.angle, t.reason, t.target_audience,
    t.status,
    datetime(t.created_at, 'localtime') AS created_at,
    h.domain AS hotspot_domain,
    h.signal_id AS hotspot_signal_id,
    (SELECT MAX(c.id) FROM copy_tasks c WHERE c.topic_id = t.id) AS copy_task_id
"""


@router.get("")
def list_topics(status: int | None = None, q: str | None = None,
                limit: int = 12, offset: int = 0,
                user=Depends(get_current_user)):
    """历史选题列表（F2 补齐）。

    改造前只有 `POST /topics/generate`，页面刷新即空 —— 库里几十上百条历史选题
    没有任何入口，「一键选用」也只是一次性的。这里按创建时间倒序分页返回。

    - `status`：0=待选用 / 1=已选用；不传=全部
    - `q`：标题或切入角度关键词（LIKE，与素材列表同口径）
    - `created_at` 统一转本地时间：库里 `datetime('now')` 存的是 **UTC**，
      直接回给前端会比本地时间早 8 小时（刚生成的选题显示成 8 小时前）
    """
    limit = max(1, min(int(limit or 12), 100))
    offset = max(0, int(offset or 0))

    where = ["t.user_id = ?"]
    args: list = [user["id"]]
    if status in (0, 1):
        where.append("t.status = ?")
        args.append(status)
    kw = (q or "").strip()
    if kw:
        where.append("(t.title LIKE ? OR t.angle LIKE ?)")
        args += [f"%{kw}%", f"%{kw}%"]

    w = " AND ".join(where)
    conn = get_conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM topics t WHERE {w}", tuple(args)
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT {_LIST_COLS} FROM topics t "
            f"LEFT JOIN hotspots h ON h.id = t.hotspot_id "
            f"WHERE {w} ORDER BY t.created_at DESC, t.id ASC LIMIT ? OFFSET ?",
            tuple(args) + (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return {"ok": True, "total": total, "limit": limit, "offset": offset,
            "results": [dict(r) for r in rows]}


@router.post("/{topic_id}/select")
def select_topic(topic_id: int, user=Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT id,title,angle,reason,target_audience FROM topics "
                       "WHERE id=? AND user_id=?", (topic_id, user["id"])).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "选题不存在或无权限"}
    conn.execute("UPDATE topics SET status=1 WHERE id=?", (topic_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "topic": dict(row)}
