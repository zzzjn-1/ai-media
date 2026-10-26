# -*- coding: utf-8 -*-
"""验证 SSE 是否真流式：记录每个事件的到达时刻与正文长度。"""
import json
import os
import time

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")


def main():
    t0 = time.time()
    with httpx.Client(base_url=BASE, timeout=300) as c:
        tok = c.post("/api/auth/login", json={"username": "demo_seed", "password": "Demo@2026"}
                     ).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        with c.stream("POST", "/api/copy/generate",
                      json={"topic_title": "折叠屏维修成本为什么这么高",
                            "target_platforms": ["weixin", "xhs"],
                            "tone": "专业", "length": "中", "n_versions": 3},
                      headers=H) as r:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                t = time.time() - t0
                extra = ""
                if ev["type"] == "version":
                    extra = f"v{ev['version_no']} body={len(ev.get('optimized_body') or '')}"
                elif ev["type"] == "retrieved":
                    extra = f"chunks={ev.get('chunk_count')}"
                elif ev["type"] == "done":
                    extra = f"count={ev.get('versions_count')} gen={ev.get('generation_seconds')}s"
                print(f"  +{t:6.1f}s  {ev['type']:<9} {extra}")


if __name__ == "__main__":
    main()
