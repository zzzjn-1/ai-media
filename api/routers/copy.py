# -*- coding: utf-8 -*-
"""api/routers/copy.py — 文案生成(SSE) / 风格优化 / 平台适配（F3/F4/F5，计划书 4.3）"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
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


def _loads(raw):
    """把落库的 JSON 字符串还原；坏数据当纯文本返回，不让整页读取失败。"""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _persist_versions(conn, task_id: int, versions: list, target_platforms: list):
    for v in versions:
        for p in target_platforms:
            ad = v["adapted"].get(p, {})
            # ⚠️ 不能用 ad.get("body", 默认值)——适配器返回空串时 .get 照样取空串，
            # 于是库里落下空白正文（2026-10-26 实测 7 条）。这里按"逐级回落 + 空则跳过"处理。
            title = (ad.get("title") or v.get("title") or "").strip() or "（无标题）"
            body = (ad.get("body") or v.get("optimized_body") or v.get("body") or "").strip()
            if not body:
                print(f"[生成] 版本{v['version_no']}/{p} 正文为空，跳过入库（避免空白卡片）")
                continue
            conn.execute(
                """INSERT INTO copy_versions
                   (copy_task_id,version_no,platform,title,body,hashtags,tone,
                    style_consistency,replacement_suggestions,status)
                   VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (task_id, v["version_no"], p, title, body,
                 ",".join(ad.get("tags") or v.get("tags") or []),
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
        """真流式：生成跑在工作线程，事件经队列边产边发。

        改造前是"假流式"——等 generate() 全部跑完（实测 40–70s）才一次性
        yield 出去，前端 70 秒只转圈，用户会以为卡住并重复点击（白烧一次 LLM）。
        """
        import queue
        import threading

        q: "queue.Queue[dict]" = queue.Queue()

        def worker():
            try:
                result = generate(spec, on_event=q.put)
                q.put({"type": "__result", "result": result})
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                q.put({"type": "__error", "err": str(e)})

        threading.Thread(target=worker, daemon=True).start()

        # task_id 一进来就发，前端立刻拿到状态，不必等模型
        yield _sse({"type": "start", "task_id": task_id})

        result, err = None, None
        while True:
            ev = q.get()
            kind = ev.get("type")
            if kind == "__result":
                result = ev["result"]
                break
            if kind == "__error":
                err = ev["err"]
                break
            yield _sse(ev)

        elapsed = round(time.time() - start, 2)

        if err:
            conn = get_conn()
            conn.rollback()
            conn.execute(
                "UPDATE copy_tasks SET status='failed', error_msg=? WHERE id=?",
                (err[:500], task_id),
            )
            conn.commit()
            conn.close()
            yield _sse({"type": "error", "task_id": task_id, "msg": err[:300]})
            return

        versions = result["versions"]
        conn = get_conn()
        _persist_versions(conn, task_id, versions, target_platforms)
        conn.execute(
            "UPDATE copy_tasks SET status='done', finished_at=datetime('now') WHERE id=?",
            (task_id,),
        )
        conn.commit()
        conn.close()

        yield _sse({"type": "done", "task_id": task_id,
                    "versions_count": len(versions),
                    "generation_seconds": elapsed,
                    "target_platforms": target_platforms})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{task_id}")
def get_copy_task(task_id: int, user=Depends(get_current_user)):
    """读取一次历史创作任务（含各平台已落库版本）。

    存在意义：FlowContext 原先只活在内存里，刷新即丢 taskId，适配页直接回空态。
    有了这个只读接口，前端能把「上次做到哪」从本地指针变成可校验的真数据：
    任务被删 / 换了账号 → 404，前端据此清指针，而不是给用户一个空白的适配页。
    只读不重算——不调优化、不调适配，避免刷新一次就白烧一次 LLM。
    """
    conn = get_conn()
    task = conn.execute(
        """SELECT t.id, t.status, t.topic_id, t.target_platforms, t.target_audience,
                  t.creation_intent, t.tone, t.length, t.persona_voice,
                  datetime(t.created_at,'localtime') AS created_at,
                  tp.title AS topic_title
             FROM copy_tasks t
             LEFT JOIN topics tp ON tp.id = t.topic_id
            WHERE t.id=? AND t.user_id=?""",
        (task_id, user["id"]),
    ).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    rows = conn.execute(
        """SELECT id,version_no,platform,title,body,hashtags,
                  style_consistency,replacement_suggestions
             FROM copy_versions WHERE copy_task_id=?
            ORDER BY version_no, platform""",
        (task_id,),
    ).fetchall()
    conn.close()

    adapter = get_adapter()
    target_platforms = [p for p in (task["target_platforms"] or "").split(",") if p]

    by_platform: dict[str, list] = {}
    for r in rows:
        p = r["platform"] or ""
        body = (r["body"] or "").strip()
        # 历史空正文脏行（2026-10-26 修 max_tokens 之前留下的）不返回，
        # 否则恢复出来的仍是"只有标题"的空白卡片
        if not p or not body:
            continue
        tags = [t.strip() for t in (r["hashtags"] or "").split(",") if t.strip()]
        rule = adapter.get_rule(p) or {}
        t_max, b_max = rule.get("title_max_len"), rule.get("body_max_len")
        by_platform.setdefault(p, []).append({
            "id": r["id"],
            "version_no": r["version_no"],
            "title": r["title"],
            "body": body,
            "tags": tags,
            "style_consistency": r["style_consistency"],
            "replacement_suggestions": _loads(r["replacement_suggestions"]),
            # 下面三项没落库（copy_versions 无对应列），但前端要展示：
            # 不补的话刷新后同一屏会从「排版：短句+emoji+分段」变成「排版：默认」，
            # 用户会以为恢复的数据不全。全部用平台规则现算，纯本地、无 LLM。
            "layout_style": rule.get("layout_style") or "",
            "export_format": rule.get("export_format") or "txt",
            # 适配器截断后长度正好等于上限，故用 >= 反推（正文或标题任一贴顶即视为截断）
            "truncated": bool(
                (b_max and len(body) >= int(b_max))
                or (t_max and len(r["title"] or "") >= int(t_max))
            ),
            # 发布备注同理：adapt 时实时算、未落库，这里现算保持一致
            "note": adapter.publish_note(p, r["title"] or "", body, tags),
        })

    # 生成页展示的是优化后正文；落库时最接近它的是目标首平台的正文。
    # 若首平台一行都没有（比如全是空正文被跳过），退化为版本数最多的平台，避免整页空。
    main = target_platforms[0] if target_platforms else ""
    if main not in by_platform:
        main = max(by_platform, key=lambda k: len(by_platform[k])) if by_platform else ""

    versions = []
    for v in by_platform.get(main, []):
        adapted = {}
        for p, lst in by_platform.items():
            hit = next((x for x in lst if x["version_no"] == v["version_no"]), None)
            if hit:
                adapted[p] = hit["title"]
        versions.append({
            "version_no": v["version_no"],
            "title": v["title"],
            "body": v["body"],
            "tags": v["tags"],
            "style_consistency": v["style_consistency"],
            "replacement_suggestions": v["replacement_suggestions"],
            "adapted": adapted,
        })

    # 平台顺序按任务的 target_platforms（用户勾选的顺序），未知平台追加在后。
    # 直接 dict.keys() 会给出 SQL 的字母序（douyin/weixin/xhs），与用户在页面上
    # 看到的标签顺序不一致，前端一旦按 platforms 渲染就会「顺序自己变了」。
    ordered = [p for p in target_platforms if p in by_platform]
    ordered += [p for p in by_platform if p not in ordered]

    return {
        "ok": True,
        "task": {
            "id": task["id"],
            "status": task["status"],
            "topic_id": task["topic_id"],
            "topic_title": task["topic_title"],
            "target_platforms": target_platforms,
            "target_audience": task["target_audience"],
            "creation_intent": task["creation_intent"],
            "tone": task["tone"],
            "length": task["length"],
            "persona_voice": task["persona_voice"],
            "created_at": task["created_at"],
        },
        "platforms": ordered,
        "versions": versions,
        "by_platform": {p: by_platform[p] for p in ordered},
    }


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
    # 空正文的行不能送去适配：模型会回一句"你正文是空的，请粘贴正文"，
    # 那句话会被当成本平台正文存下来（2026-10-26 实测踩到），比报错更糟。
    rows = [r for r in rows if (r["body"] or "").strip()]
    if not rows:
        conn.close()
        return {"ok": False, "msg": "该任务没有可适配的正文（生成时可能被截断），请重新生成文案"}
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
            new_body = (ad.get("body") or "").strip() or r["body"]
            ad["body"] = new_body
            cur = conn.execute(
                """INSERT INTO copy_versions
                   (copy_task_id,version_no,platform,title,body,hashtags,status)
                   VALUES(?,?,?,?,?,?,1)""",
                (task_id, r["version_no"], req.platform, ad["title"], new_body,
                 ",".join(ad["tags"])),
            )
            updated.append({"version_no": r["version_no"], "id": cur.lastrowid, **ad})
    else:
        for r in target:
            tags = [t.strip() for t in (r["hashtags"] or "").split(",") if t.strip()]
            ad = adapter.adapt(req.platform, r["title"], r["body"], tags)
            ad["note"] = adapter.publish_note(req.platform, ad["title"], ad["body"], ad["tags"])
            new_body = (ad.get("body") or "").strip() or r["body"]
            ad["body"] = new_body
            conn.execute(
                "UPDATE copy_versions SET title=?, body=?, hashtags=? WHERE id=?",
                (ad["title"], new_body, ",".join(ad["tags"]), r["id"]),
            )
            updated.append({"version_no": r["version_no"], "id": r["id"], **ad})
    if not updated:
        conn.close()
        return {"ok": False, "msg": f"{req.platform} 适配未产出任何版本，请稍后重试"}
    conn.commit()
    conn.close()
    return {"ok": True, "task_id": task_id, "platform": req.platform, "versions": updated}
