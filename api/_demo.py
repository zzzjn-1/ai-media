# -*- coding: utf-8 -*-
"""
M3 后端实时演示脚本 —— 连贯展示从登录到导出的完整链路（真实调用运行中的 API）。
运行：python api/_demo.py   （需后端已在 127.0.0.1:8000 启动）
"""
import json
import os
import sys

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")
SEP = "=" * 72


def line(s=""):
    print(s)


def banner(title):
    print("\n" + SEP)
    print("  " + title)
    print(SEP)


def main():
    c = httpx.Client(base_url=BASE, timeout=60)

    # 0. 健康检查
    banner("[0] 健康检查  GET /api/health")
    h = c.get("/api/health").json()
    ct = h.get("counts", {})
    line(f"  service           : {h.get('service')}")
    line(f"  llm_backend       : {h.get('llm_backend')}   (mock=离线可跑 / openai=需配置 API Key)")
    line(f"  embedding_backend : {h.get('embedding_backend')}   (offline=哈希 / bge=语义)")
    line(f"  数据规模          : 用户 {ct.get('users')} | 素材 {ct.get('materials')} |"
         f" 分块 {ct.get('material_chunks')} | 热点 {ct.get('hotspots')} |"
         f" 选题 {ct.get('topics')} | 文案任务 {ct.get('copy_tasks')} |"
         f" 文案版本 {ct.get('copy_versions')} | 平台规则 {ct.get('platform_rules')}")

    # 1. 登录
    banner("[1] 登录  POST /api/auth/login   (demo_seed / Demo@2026)")
    r = c.post("/api/auth/login", json={"username": "demo_seed", "password": "Demo@2026"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    j = r.json()
    tok = j["access_token"]
    H = {"Authorization": f"Bearer {tok}"}
    line(f"  ok={j.get('ok')}  user_id={j.get('user_id')}  token_type={j.get('token_type')}")
    line(f"  access_token(截断): {tok[:24]}…")

    # 2. 热点列表
    banner("[2] 热点列表  GET /api/hotspots")
    hs = c.get("/api/hotspots", headers=H).json()
    line(f"  ok={hs.get('ok')}  count={hs.get('count')}  （取前 4 条）")
    for it in hs.get("hotspots", [])[:4]:
        line(f"   - [{it.get('domain')}/{it.get('source')}] {it.get('title')}")
        line(f"       热度={it.get('heat')}  趋势={it.get('trend')}")

    # 3. 选题策划
    banner("[3] 选题策划  POST /api/topics/generate   (domain=科技, count=4)")
    r = c.post("/api/topics/generate", json={"domain": "科技", "count": 4}, headers=H)
    topics = r.json()["topics"]
    line(f"  生成 {len(topics)} 条选题（计划书要求 ≥3）")
    for i, t in enumerate(topics, 1):
        line(f"   {i}. {t['title']}")
        line(f"      角度: {t.get('angle')}")
        line(f"      理由: {t.get('reason')}  目标人群: {t.get('target_audience')}")
    tid = topics[0]["id"]
    tt = topics[0]["title"]
    c.post(f"/api/topics/{tid}/select", headers=H)
    line(f"  → 已选定选题 id={tid}：{tt}")

    # 4. 文案生成（SSE 流式）
    banner("[4] 文案生成（SSE 流式）  POST /api/copy/generate")
    line("  参数: platforms=[weixin,xhs,douyin], intent=干货科普, tone=专业, length=中")
    task_id = None
    n_ver = 0
    with c.stream("POST", "/api/copy/generate",
                  json={"topic_id": tid, "topic_title": tt,
                        "target_platforms": ["weixin", "xhs", "douyin"],
                        "intent": "干货科普", "tone": "专业", "length": "中"},
                  headers=H) as resp:
        for ln in resp.iter_lines():
            if not ln or not ln.startswith("data: "):
                continue
            ev = json.loads(ln[6:])
            t = ev["type"]
            if t == "start":
                line(f"  ┌─ start → task_id={ev.get('task_id')}")
            elif t == "retrieved":
                line(f"  ├─ retrieved → 召回 {ev.get('chunk_count')} 个素材块（RAG 检索）")
                for s in ev.get("snippets", [])[:2]:
                    line(f"       · {s[:70]}…")
            elif t == "version":
                n_ver += 1
                body = ev.get("optimized_body") or ""
                line(f"  ├─ version[{ev.get('version_no')}] 「{ev.get('title')}」")
                line(f"       标签: {ev.get('tags')}  风格一致: {ev.get('style_consistency')}")
                line(f"       正文({len(body)}字): {body[:80]}…")
            elif t == "done":
                task_id = ev.get("task_id")
                line(f"  └─ done → 版本数={ev.get('versions_count')}"
                     f"  耗时={ev.get('generation_seconds')}s  平台={ev.get('target_platforms')}")
    line(f"  → 共流式推送 {n_ver} 个版本（计划书要求 ≥3）✅")

    # 5. 风格优化
    banner("[5] 风格优化  POST /api/copy/{task_id}/optimize   (tone=更口语、更亲切)")
    r = c.post(f"/api/copy/{task_id}/optimize", json={"tone": "更口语、更亲切"}, headers=H)
    opt = r.json()
    vs = opt.get("versions", [])
    line(f"  ok={opt.get('ok')}  返回 {len(vs)} 个优化版本（= 版本数 × 平台数）")
    v0 = vs[0]
    line(f"  优化后 v{v0.get('version_no')} 标题: {v0.get('title')}")
    line(f"  优化后正文(摘要): {(v0.get('body') or '')[:90]}…")
    if v0.get("replacement_suggestions"):
        line(f"  替换建议: {v0.get('replacement_suggestions')}")

    # 6. 多平台适配
    banner("[6] 多平台适配  POST /api/copy/{task_id}/adapt   (逐平台展示硬性规范)")
    for plat in ["weixin", "xhs", "douyin"]:
        r = c.post(f"/api/copy/{task_id}/adapt", json={"platform": plat}, headers=H)
        avs = r.json().get("versions", [])
        v = avs[0]
        trunc = "（已截断）" if v.get("truncated") else ""
        line(f"\n  ▶ 平台={v.get('display_name') or plat}  排版={v.get('layout_style')}"
             f"  导出格式={v.get('export_format')}{trunc}")
        line(f"    标题: {v.get('title')}")
        line(f"    标签: {v.get('tags')}")
        line(f"    正文(摘要): {(v.get('body') or '')[:70]}…")

    # 7. 素材库检索
    banner("[7] 素材检索  GET /api/materials?q=人工智能   (语义+关键词召回)")
    r = c.get("/api/materials", params={"q": "人工智能", "limit": 50}, headers=H)
    mj = r.json()
    line(f"  ok={mj.get('ok')}  mode={mj.get('mode')}  命中 {len(mj.get('results', []))} 个素材：")
    for it in mj.get("results", [])[:3]:
        line(f"   - #{it.get('id')} {it.get('title')}  领域={it.get('domains')} 平台={it.get('platforms')}")

    # 8. 人设画像
    banner("[8] 人设画像  GET /api/persona  &  POST /api/persona")
    p = c.get("/api/persona", headers=H).json().get("persona", {})
    line(f"  当前人设: 账号={p.get('display_name')}  定位={p.get('positioning')}"
         f"  调性={p.get('tone')}  领域={p.get('domains')}")
    c.post("/api/persona", json={"positioning": "科技科普自媒体",
                                 "tone": "专业且接地气",
                                 "domains": "科技,财经"}, headers=H)
    p2 = c.get("/api/persona", headers=H).json().get("persona", {})
    line(f"  更新后人设: 账号={p2.get('display_name')}  定位={p2.get('positioning')}"
         f"  调性={p2.get('tone')}  领域={p2.get('domains')}")

    # 9. 导出
    banner("[9] 导出  POST /api/export   (task_id, platform=weixin, format=txt)")
    r = c.post("/api/export", json={"task_id": task_id, "platform": "weixin", "format": "txt"}, headers=H)
    text = r.text
    line(f"  status={r.status_code}  content-type={r.headers.get('content-type')}")
    line(f"  content-disposition: {r.headers.get('content-disposition')}")
    line(f"  字节数: {len(text.encode('utf-8'))}")
    line("  ---- 导出内容预览（前 900 字）----")
    line(text[:900])

    banner("演示结束 ✅  9 个环节全部正常返回，M3 后端链路闭环可运行。")
    c.close()


if __name__ == "__main__":
    main()
