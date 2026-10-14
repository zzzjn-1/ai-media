# -*- coding: utf-8 -*-
"""M3 后端端到端冒烟测试（离线 Mock 模式）"""
import json
import os

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")


def main():
    with httpx.Client(base_url=BASE, timeout=30) as c:
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

        # 4) 选题生成
        r = c.post("/api/topics/generate", json={"domain": "科技", "count": 3}, headers=H)
        topics = r.json()["topics"]
        print("[4] topics generated:", len(topics), "| 首条:", topics[0]["title"][:24])
        tid = topics[0]["id"]
        c.post(f"/api/topics/{tid}/select", headers=H)

        # 5) 文案生成（SSE）
        with c.stream("POST", "/api/copy/generate",
                      json={"topic_id": tid, "topic_title": topics[0]["title"],
                            "target_platforms": ["weixin", "xhs"], "intent": "干货科普",
                            "tone": "专业", "length": "中"}, headers=H) as resp:
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

        # 10) 人设配置
        r = c.get("/api/persona", headers=H)
        print("[10] persona GET:", r.json()["persona"])
        r = c.post("/api/persona", json={"tone": "犀利"}, headers=H)
        print("     persona POST:", r.json()["persona"])

        # 11) 素材导入 + 向量化
        r = c.post("/api/materials/import", json={"title": "测试素材A", "content": "大模型正在改变创作方式。\n\n端侧推理降低成本。\n\n向量数据库支撑检索增强。"},
                   headers=H)
        mid = r.json()["material_id"]
        print("[11] import material:", mid, "| chunks:", r.json()["chunk_count"])
        r = c.post(f"/api/materials/{mid}/embed", headers=H)
        print("     embed rebuild vectors:", r.json().get("rebuilt_vectors"))

        print("\n✅ E2E 冒烟测试完成")


if __name__ == "__main__":
    main()
