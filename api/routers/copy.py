# -*- coding: utf-8 -*-
"""api/routers/copy.py — 文案生成(SSE) / 风格优化 / 平台适配（F3/F4/F5，计划书 4.3）"""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..database import get_conn
from ..deps import get_current_user
from ..core.rag_pipeline import generate, optimize_text
from ..core.platform_adapter import get_adapter

router = APIRouter(prefix="/copy", tags=["copy"])


class GenerateReq(BaseModel):
    topic_id: int | None = None
    topic_title: str
    target_platforms: list[str] | None = None
    platform: str | None = None          # 兼容单平台写法
    audience: str | None = None
    intent: str | None = None
    tone: str | None = None
    length: str | None = None
    persona_voice: str | None = None
    n_versions: int | None = None


class OptimizeReq(BaseModel):
    tone: str | None = None
    length: str | None = None
    persona_voice: str | None = None


class AdaptReq(BaseModel):
    platform: str


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _persist_versions(conn, task_id: int, versions: list, target_platforms: list):
    for v in versions:
        for p in target_platforms:
            ad = v["adapted"].get(p, {})
            conn.execute(
                """INSERT INTO copy_versions
                   (copy_task_id,version_no,platform,title,body,hashtags,tone,
                    style_consistency,replacement_suggestions,status)
                   VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (task_id, v["version_no"], p, ad.get("title", v["title"]),
                 ad.get("body", v["optimized_body"]),
                 ",".join(ad.get("tags", v["tags"])),
                 v.get("tone") or "", v["style_consistency"],
                 json.dumps(v["replacement_suggestions"], ensure_ascii=False)),
            )


@router.post("/generate")
def generate_copy(req: GenerateReq, user=Depends(get_current_user)):
    target_platforms = req.target_platforms or ([req.platform] if req.platform else ["weixin"])

    conn = get_conn()
    persona = conn.execute(
        "SELECT positioning, tone, domains, display_name FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    conn.close()
    persona_d = dict(persona) if persona else {}

    spec = {
        "topic_title": req.topic_title,
        "domain": persona_d.get("domains", "科技").split(",")[0],
        "platform": target_platforms[0],
        "target_platforms": target_platforms,
        "style": req.tone or persona_d.get("tone") or "专业",
        "tone": req.tone or persona_d.get("tone"),
        "length": req.length or "中",
        "intent": req.intent or "干货科普",
        "audience": req.audience or "关注效率的人群",
        "persona_voice": req.persona_voice or persona_d.get("positioning"),
        "persona_positioning": persona_d.get("positioning"),
        "n_versions": req.n_versions or 3,
    }

    # 创建任务（running）
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO copy_tasks(user_id,topic_id,target_platforms,target_audience,
           creation_intent,tone,length,persona_voice,status)
           VALUES(?,?,?,?,?,?,?,?, 'running')""",
        (user["id"], req.topic_id, ",".join(target_platforms), req.audience,
         req.intent, spec["tone"], spec["length"], spec["persona_voice"]),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()

    start = time.time()

    def event_stream():
        try:
            result = generate(spec)
            versions = result["versions"]
            elapsed = round(time.time() - start, 2)

            conn = get_conn()
            _persist_versions(conn, task_id, versions, target_platforms)
            conn.execute(
                "UPDATE copy_tasks SET status='done', finished_at=datetime('now') WHERE id=?",
                (task_id,),
            )
            conn.commit()
            conn.close()

            yield _sse({"type": "start", "task_id": task_id})
            yield _sse({"type": "retrieved", "chunk_count": len(result.get("chunks", [])),
                        "snippets": [c["content"][:60] for c in result.get("chunks", [])][:3]})
            for v in versions:
                yield _sse({
                    "type": "version",
                    "version_no": v["version_no"],
                    "title": v["title"],
                    "optimized_body": v["optimized_body"],
                    "tags": v["tags"],
                    "adapted": {p: ad.get("title") for p, ad in v["adapted"].items()},
                    "style_consistency": v["style_consistency"],
                    "replacement_suggestions": v["replacement_suggestions"],
                })
            yield _sse({"type": "done", "task_id": task_id,
                        "versions_count": len(versions),
                        "generation_seconds": elapsed,
                        "target_platforms": target_platforms})
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            conn = get_conn()
            conn.rollback()
            conn.execute(
                "UPDATE copy_tasks SET status='failed', error_msg=? WHERE id=?",
                (str(e)[:500], task_id),
            )
            conn.commit()
            conn.close()
            yield _sse({"type": "error", "task_id": task_id, "msg": str(e)[:300]})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{task_id}/optimize")
def optimize_copy(task_id: int, req: OptimizeReq, user=Depends(get_current_user)):
    conn = get_conn()
    task = conn.execute(
        "SELECT id,tone,length,persona_voice FROM copy_tasks WHERE id=? AND user_id=?",
        (task_id, user["id"]),
    ).fetchone()
    if not task:
        conn.close()
        return {"ok": False, "msg": "任务不存在或无权限"}
    rows = conn.execute(
        "SELECT id,version_no,title,body FROM copy_versions WHERE copy_task_id=? ORDER BY version_no",
        (task_id,),
    ).fetchall()
    spec = {
        "tone": req.tone or task["tone"],
        "length": req.length or task["length"],
        "persona_voice": req.persona_voice or task["persona_voice"],
    }
    updated = []
    for r in rows:
        opt_body, consistency, suggestions = optimize_text(
            {"title": r["title"], "body": r["body"]}, spec)
        conn.execute(
            "UPDATE copy_versions SET body=?, style_consistency=?, replacement_suggestions=? WHERE id=?",
            (opt_body, consistency, json.dumps(suggestions, ensure_ascii=False), r["id"]),
        )
        updated.append({"version_no": r["version_no"], "title": r["title"],
                        "body": opt_body, "style_consistency": consistency,
                        "replacement_suggestions": suggestions})
    conn.commit()
    conn.close()
    return {"ok": True, "task_id": task_id, "versions": updated}


@router.post("/{task_id}/adapt")
def adapt_copy(task_id: int, req: AdaptReq, user=Depends(get_current_user)):
    conn = get_conn()
    task = conn.execute("SELECT id FROM copy_tasks WHERE id=? AND user_id=?",
                        (task_id, user["id"])).fetchone()
    if not task:
        conn.close()
        return {"ok": False, "msg": "任务不存在或无权限"}
    rows = conn.execute(
        "SELECT id,version_no,title,body,hashtags,platform FROM copy_versions WHERE copy_task_id=? ORDER BY version_no",
        (task_id,),
    ).fetchall()
    adapter = get_adapter()
    updated = []
    target = [r for r in rows if r["platform"] == req.platform]
    # 若该平台尚未有适配行，则从已有版本补建（支持生成后追加新平台分发）
    if not target:
        seen = set()
        for r in rows:
            if r["version_no"] in seen:
                continue
            seen.add(r["version_no"])
            tags = [t.strip() for t in (r["hashtags"] or "").split(",") if t.strip()]
            ad = adapter.adapt(req.platform, r["title"], r["body"], tags)
            ad["note"] = adapter.publish_note(req.platform, ad["title"], ad["body"], ad["tags"])
            cur = conn.execute(
                """INSERT INTO copy_versions
                   (copy_task_id,version_no,platform,title,body,hashtags,status)
                   VALUES(?,?,?,?,?,?,1)""",
                (task_id, r["version_no"], req.platform, ad["title"], ad["body"],
                 ",".join(ad["tags"])),
            )
            updated.append({"version_no": r["version_no"], "id": cur.lastrowid, **ad})
    else:
        for r in target:
            tags = [t.strip() for t in (r["hashtags"] or "").split(",") if t.strip()]
            ad = adapter.adapt(req.platform, r["title"], r["body"], tags)
            ad["note"] = adapter.publish_note(req.platform, ad["title"], ad["body"], ad["tags"])
            conn.execute(
                "UPDATE copy_versions SET title=?, body=?, hashtags=? WHERE id=?",
                (ad["title"], ad["body"], ",".join(ad["tags"]), r["id"]),
            )
            updated.append({"version_no": r["version_no"], "id": r["id"], **ad})
    conn.commit()
    conn.close()
    return {"ok": True, "task_id": task_id, "platform": req.platform, "versions": updated}
