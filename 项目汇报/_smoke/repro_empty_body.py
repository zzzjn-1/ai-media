# -*- coding: utf-8 -*-
"""复现：多版本生成里是否出现空正文 / 截断版本。"""
import json
import os

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")


def main():
    with httpx.Client(base_url=BASE, timeout=300) as c:
        tok = c.post("/api/auth/login", json={"username": "demo_seed", "password": "Demo@2026"}
                     ).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        with c.stream("POST", "/api/copy/generate",
                      json={"topic_title": "折叠屏维修成本为什么这么高",
                            "target_platforms": ["weixin", "xhs"],
                            "tone": "专业", "length": "中", "n_versions": 3},
                      headers=H) as r:
            task_id = None
            rows = []
            for line in r.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    if ev["type"] == "start":
                        task_id = ev["task_id"]
                    if ev["type"] == "version":
                        rows.append((ev["version_no"], len(ev.get("optimized_body") or ""),
                                     (ev.get("title") or "")[:26],
                                     len(ev.get("style_consistency") or "")))
                    if ev["type"] == "done":
                        print("done:", ev["generation_seconds"], "s | task", task_id)
                    if ev["type"] == "error":
                        print("ERROR:", ev)
            print("task", task_id)
            for r_ in rows:
                flag = "  <== 空正文" if r_[1] == 0 else ("" if r_[1] > 60 else "  <== 过短")
                print("  v{} body_len={:<5} title={}{}".format(r_[0], r_[1], r_[2], flag))


if __name__ == "__main__":
    main()
