# -*- coding: utf-8 -*-
"""api/routers/export.py — 导出 txt/docx 与发布备注（F5.2，计划书 2.1.5 / 4.3）

docx 由标准库 zipfile 生成最小合规文档，无需 python-docx 依赖。
"""
import io
import zipfile
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..database import get_conn
from ..deps import get_current_user
from ..core.platform_adapter import get_adapter

router = APIRouter(prefix="/export", tags=["export"])


class ExportReq(BaseModel):
    task_id: int
    platform: str | None = None
    format: str = "txt"   # txt | docx


def _collect_rows(conn, task_id: int, platform: str | None):
    if platform:
        rows = conn.execute(
            "SELECT version_no,title,body,hashtags,platform FROM copy_versions "
            "WHERE copy_task_id=? AND platform=? ORDER BY version_no",
            (task_id, platform),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT version_no,title,body,hashtags,platform FROM copy_versions "
            "WHERE copy_task_id=? ORDER BY version_no,id", (task_id,),
        ).fetchall()
    # 同 version_no 去重（取首个平台）
    seen, out = set(), []
    for r in rows:
        if r["version_no"] in seen:
            continue
        seen.add(r["version_no"])
        out.append(dict(r))
    return out


def _xml_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_docx(items: list) -> bytes:
    paras = []
    for it in items:
        paras.append({"text": f"【{it['platform']}】{it['title']}", "bold": True})
        for line in (it["body"] or "").split("\n"):
            paras.append({"text": line, "bold": False})
        paras.append({"text": "标签：" + (it["tags"] or ""), "bold": False})
        paras.append({"text": "", "bold": False})
    body_xml = "".join(
        f'<w:p><w:r><w:rPr>{"<w:b/>" if p["bold"] else ""}</w:rPr>'
        f'<w:t xml:space="preserve">{_xml_escape(p["text"])}</w:t></w:r></w:p>'
        for p in paras
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body_xml}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


@router.post("")
def export(req: ExportReq, user=Depends(get_current_user)):
    conn = get_conn()
    task = conn.execute("SELECT id FROM copy_tasks WHERE id=? AND user_id=?",
                        (req.task_id, user["id"])).fetchone()
    if not task:
        conn.close()
        return {"ok": False, "msg": "任务不存在或无权限"}
    rows = _collect_rows(conn, req.task_id, req.platform)
    adapter = get_adapter()
    items = []
    for r in rows:
        tags = [t.strip() for t in (r["hashtags"] or "").split(",") if t.strip()]
        note = adapter.publish_note(r["platform"], r["title"], r["body"], tags)
        items.append({
            "version_no": r["version_no"], "platform": r["platform"],
            "title": r["title"], "body": r["body"], "tags": r["hashtags"], "note": note,
        })
    conn.close()

    if req.format == "docx":
        doc_bytes = _make_docx(items)
        return StreamingResponse(
            io.BytesIO(doc_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="copy_task_{req.task_id}.docx"'},
        )
    # txt
    text = ""
    for it in items:
        text += f"===== 版本{it['version_no']}（{it['platform']}）=====\n"
        text += f"标题：{it['title']}\n\n{it['body']}\n\n标签：{it['tags']}\n\n"
        text += it["note"] + "\n\n"
    return StreamingResponse(
        io.StringIO(text),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="copy_task_{req.task_id}.txt"'},
    )
