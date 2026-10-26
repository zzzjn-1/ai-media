# -*- coding: utf-8 -*-
"""M3 后端端到端冒烟测试（离线 Mock 模式）"""
import json
import os

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")


def main():
    # 超时放宽：接真实 LLM 后单次选题/生成可能到几十秒，30s 会把"慢"误判成"挂"
    with httpx.Client(base_url=BASE, timeout=120) as c:
        # 1) health
        r = c.get("/api/health")
        h = r.json()
        print("[1] health:", h["ok"], "| llm=", h["llm_backend"], "emb=", h["embedding_backend"],
              "| materials=", h["counts"]["materials"], "chunks=", h["counts"]["material_chunks"])

        # 2) 登录（优先 demo_seed；失败则注册）
        token = None
        r = c.post("/api/auth/login", json={"username": "demo_seed", "password": "Demo@2026"})
        if r.json().get("ok"):
            token = r.json()["access_token"]
            print("[2] login demo_seed OK")
        else:
            r = c.post("/api/auth/register", json={"username": "tester", "password": "Test@1234"})
            token = r.json().get("access_token")
            print("[2] register tester ->", r.json().get("ok"))
        H = {"Authorization": f"Bearer {token}"}

        # 3) 热点看板
        r = c.get("/api/hotspots")
        hs = r.json()
        print("[3] hotspots:", hs["count"], "条 | 示例:", hs["hotspots"][0]["title"] if hs["hotspots"] else "无")

        # 3b) 二期字段：归一化热度 / 榜内名次 / 建议角度（P2-1）
        sample = hs["hotspots"][0] if hs["hotspots"] else {}
        print("[3b] 扩展字段:", {k: sample.get(k) for k in
                                ("signal_id", "norm_heat", "best_rank", "source_count", "is_new")})
        assert sample.get("norm_heat") is not None, "缺 norm_heat（展示口径未切换）"
        r = c.get("/api/hotspots", params={"view": "feed", "limit": 5}, headers=H)
        page = r.json()
        sug = [h for h in page["hotspots"] if h.get("suggestion")]
        print("     建议角度:", f"{len(sug)}/{page['count']}",
              "| 示例:", (sug[0]["suggestion"] if sug else "无"))
        assert sug, "建议角度未生成（HOTSPOT_SUGGESTION_ENABLED 是否关闭？）"
        assert all(len(h["suggestion"]) <= 40 for h in sug), "建议角度超过 40 字"

        # 3c) 采纳漏斗（P2-7）：曝光 → 采纳 → 标记已用过 → 状态可见
        # 优先挑一条"还没用过"的：短窗去重会把重复采纳吞掉，用已采过的会让断言假失败
        target = next((h for h in page["hotspots"] if not h.get("adopted")),
                      page["hotspots"][0])
        assert "adopted" in target, "缺 adopted 字段（未登录也该是 false，不能缺）"
        r = c.post("/api/hotspots/events",
                   json={"action": "expose", "surface": "feed", "count": page["count"]}, headers=H)
        assert r.status_code == 200 and r.json()["recorded"] == 1, "曝光上报失败"
        r = c.post("/api/hotspots/events",
                   json={"action": "adopt", "hotspot_id": target["id"],
                         "surface": "feed", "position": 1}, headers=H)
        r2 = c.post("/api/hotspots/events",
                    json={"action": "adopt", "hotspot_id": target["id"],
                          "surface": "feed", "position": 1}, headers=H)
        assert r.json()["recorded"] == 1, f"采纳未记录：{r.text}"
        assert r2.json().get("deduped") is True, "同一热点短窗内重复采纳未去重（会灌水分母）"
        r = c.post("/api/hotspots/events", json={"action": "generate"}, headers=H)
        assert r.status_code == 400, "generate 不应允许客户端直接写（最强信号必须服务端产生）"
        print("[3c] 采纳事件: 曝光 1 / 采纳 1 / 重复采纳已去重 / 客户端写 generate 被拒")

        # 3d) 采纳后回看板应带 adopted=true（"已用过"记忆）
        r = c.get("/api/hotspots", params={"view": "feed", "limit": 5}, headers=H)
        marked = {h["id"] for h in r.json()["hotspots"] if h.get("adopted")}
        assert target["id"] in marked, "采纳后未被标记为已用过"
        r = c.get("/api/hotspots/status", headers=H)
        ad = r.json()["adoption"]
        print("[3d] 采纳漏斗:", {k: ad[k] for k in
                                ("impressions", "adopts", "generates", "ctr", "adopt_to_generate")})
        assert ad["impressions"] >= 1 and ad["adopts"] >= 1, "漏斗计数未落库"

        # 4) 选题生成
        r = c.post("/api/topics/generate", json={"domain": "科技", "count": 3}, headers=H)
        topics = r.json()["topics"]
        print("[4] topics generated:", len(topics), "| 首条:", topics[0]["title"][:24])
        tid = topics[0]["id"]
        c.post(f"/api/topics/{tid}/select", headers=H)

        # 4a) 历史选题列表（GET /api/topics）：页面刷新后历史选题必须还能取到
        lst = c.get("/api/topics", params={"limit": 5}, headers=H).json()
        ids = [t["id"] for t in lst["results"]]
        print("[4a] topics list:", lst["total"], "条 | 首条 id:", ids[0] if ids else None,
              "| 含本次新建:", tid in ids)
        assert lst["total"] >= 3, "历史选题列表为空"
        assert ids and ids[0] == tid, "新生成的选题没排在最前（排序应为 created_at DESC, id ASC）"
        assert lst["results"][0]["status"] == 1, "select 之后 status 未变 1"
        sel = c.get("/api/topics", params={"status": 1, "limit": 50}, headers=H).json()
        assert all(t["status"] == 1 for t in sel["results"]), "status 过滤失效"
        kw = c.get("/api/topics", params={"q": topics[0]["title"][:6]}, headers=H).json()
        assert kw["total"] >= 1, "关键词搜索没命中刚生成的选题"
        pg1 = c.get("/api/topics", params={"limit": 2, "offset": 0}, headers=H).json()
        pg2 = c.get("/api/topics", params={"limit": 2, "offset": 2}, headers=H).json()
        assert not ({t["id"] for t in pg1["results"]} & {t["id"] for t in pg2["results"]}), \
            "分页 offset 失效（两页出现重复行）"
        # created_at 必须是本地时间：库里 datetime('now') 是 UTC，不转就会早 8 小时
        from datetime import datetime as _dt
        _row = pg1["results"][0]
        _diff = abs((_dt.now() - _dt.strptime(_row["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds())
        print("     created_at:", _row["created_at"], "| 与本地时间差 %ds" % _diff)
        assert _diff < 600, f"created_at 未转本地时间（差 {_diff / 3600:.1f} 小时）"

        # 4b) 热点直传选题（P2-2）：领域必须跟随热点，而不是沿用请求里的领域
        hot = next((h for h in page["hotspots"]
                    if h.get("signal_id") and h.get("domain") not in (None, "综合")), None)
        if hot:
            rr = c.post("/api/topics/generate",
                        json={"signal_id": hot["signal_id"], "count": 3}, headers=H)
            tp = rr.json()
            print("[4b] 直传 signal_id:", hot["signal_id"], "| 热点领域:", hot["domain"],
                  "-> 生成 domain:", tp["domain"])
            print("     选题:", [t["title"][:18] for t in tp["topics"]])
            assert tp["domain"] == hot["domain"], "热点主领域未覆盖请求领域"
            import sqlite3
            db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "app.db")
            con = sqlite3.connect(db)
            linked = con.execute(
                "SELECT COUNT(*) FROM topics WHERE hotspot_id IS NOT NULL").fetchone()[0]
            con.close()
            print("     已关联热点的选题数:", linked)
            assert linked > 0, "topics.hotspot_id 未落地（选题→热点外键仍是空）"
            # 强信号：带 signal_id 出题成功，服务端应自动写一条 generate 事件
            st = c.get("/api/hotspots/status", headers=H).json()["adoption"]
            print("     出题事件 generates =", st["generates"])
            assert st["generates"] >= 1, "出题（generate）事件未记录，漏斗第三级断了"
        else:
            print("[4b] 跳过：无带 signal_id 的垂类热点")

        # 5) 文案生成（SSE）
        with c.stream("POST", "/api/copy/generate",
                      json={"topic_id": tid, "topic_title": topics[0]["title"],
                            "target_platforms": ["weixin", "xhs"], "intent": "干货科普",
                            "tone": "专业", "length": "中"}, headers=H, timeout=300) as resp:
            events = []
            task_id = None
            versions = 0
            gen_sec = None
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev["type"])
                    if ev["type"] == "version":
                        versions += 1
                    if ev["type"] == "done":
                        task_id = ev["task_id"]
                        gen_sec = ev["generation_seconds"]
        print("[5] SSE events:", events)
        print("    版本数:", versions, "| task_id:", task_id, "| 生成耗时:", gen_sec, "s")

        # 6) 风格优化
        r = c.post(f"/api/copy/{task_id}/optimize", json={"tone": "轻松"}, headers=H)
        print("[6] optimize:", r.json()["ok"], "| 更新版本:", len(r.json()["versions"]))

        # 7) 平台适配（抖音）
        r = c.post(f"/api/copy/{task_id}/adapt", json={"platform": "douyin"}, headers=H)
        ad = r.json()
        print("[7] adapt douyin:", ad["ok"], "| 首版标题:", ad["versions"][0]["title"][:20],
              "| 截断?", ad["versions"][0]["truncated"])

        # 8) 素材检索
        r = c.get("/api/materials?q=科技", headers=H)
        print("[8] material search '科技':", r.json()["count" if "count" in r.json() else "ok"],
              "条 ->", [m["title"][:18] for m in r.json().get("results", [])][:3])

        # 9) 导出 txt
        r = c.post("/api/export", json={"task_id": task_id, "platform": "weixin", "format": "txt"}, headers=H)
        txt = r.text
        print("[9] export txt bytes:", len(txt), "| 含版本标记:", "===== 版本" in txt)

        # 10) 人设配置（改完必须还原：此前每跑一次就把 demo 账号口吻永久改成「犀利」，
        #     演示时人设与 clean_chunk 种子不一致，属测试污染）
        r = c.get("/api/persona", headers=H)
        persona_before = r.json()["persona"]
        print("[10] persona GET:", persona_before)
        r = c.post("/api/persona", json={"tone": "犀利"}, headers=H)
        print("     persona POST:", r.json()["persona"])
        restore_tone = persona_before.get("tone") or "专业"
        c.post("/api/persona", json={"tone": restore_tone}, headers=H)
        print("     persona 还原口吻 ->", restore_tone)

        # 11) 素材导入 + 向量化
        r = c.post("/api/materials/import", json={"title": "测试素材A", "content": "大模型正在改变创作方式。\n\n端侧推理降低成本。\n\n向量数据库支撑检索增强。"},
                   headers=H)
        mid = r.json()["material_id"]
        print("[11] import material:", mid, "| chunks:", r.json()["chunk_count"])
        r = c.post(f"/api/materials/{mid}/embed", headers=H)
        print("     embed rebuild vectors:", r.json().get("rebuilt_vectors"))

        # 12) 清理：删掉本次（及历史遗留）导入的测试素材
        # 以前每跑一次就永久往素材库丢一条「测试素材A」，四次跑完首屏四行垃圾。
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "app.db")
        con = sqlite3.connect(db)
        ids = [row[0] for row in con.execute(
            "SELECT id FROM materials WHERE title LIKE '测试素材%'")]
        if ids:
            qs = ",".join("?" * len(ids))
            con.execute(f"DELETE FROM material_chunks WHERE material_id IN ({qs})", ids)
            con.execute(f"DELETE FROM materials WHERE id IN ({qs})", ids)
            con.commit()
        left = con.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        con.close()
        print(f"[12] 清理测试素材：删除 {len(ids)} 条 | 剩余素材 {left} 条")

        # 删行后 FAISS 里还留着死向量，按现有分块重建一次
        r = c.get("/api/materials?limit=1", headers=H).json()
        if r.get("results"):
            other = r["results"][0]["id"]
            rb = c.post(f"/api/materials/{other}/embed", headers=H).json()
            print("     重建向量:", rb.get("rebuilt_vectors"))

        print("\n✅ E2E 冒烟测试完成")


if __name__ == "__main__":
    main()
