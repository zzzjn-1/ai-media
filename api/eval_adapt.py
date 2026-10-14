# -*- coding: utf-8 -*-
"""
M3 · 平台适配一致性自检（落实计划书 6.2「平台规范适配符合率≥90%」的硬性约束部分）

用法（需后端已启动于 :8000）:
  python api/eval_adapt.py
  python api/eval_adapt.py --samples 5

逻辑:
  - 登录演示账号 → 生成一篇覆盖三端(weixin/xhs/douyin)的文案 → 对每端调用适配接口。
  - 对照 platform_rules 的硬性约束（标题长度、正文字数、标签格式 #话题#、标签数量上限）
    逐条校验适配后输出是否符合。
  - 报告整体符合率（规则引擎确定性落地，应≈100%，作为回归护栏）。

说明:
  - 本脚本校验的是「格式/硬性规范」层面；计划书「平台规范适配符合率≥90%」的语义层
    （平台调性契合度、钩子质量等）需在接入真实 LLM 后以人工/模型评判，离线 Mock 不主张该指标。
"""
import argparse
import json
import os

import httpx

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")
DEMO = ("demo_seed", "Demo@2026")


def login(c):
    r = c.post("/api/auth/login", json={"username": DEMO[0], "password": DEMO[1]})
    return r.json()["access_token"]


def fetch_rules(c, H):
    # 复用平台规则：直接从库读取不便（内部接口未暴露），这里通过适配响应中的 display_name
    # 与已知约束表做校验。约束表与 db/schema/platform_rules 初始化一致。
    return {
        "weixin": {"title_max_len": 64, "body_max_len": None, "hashtag_count": None, "fmt": "#{}#"},
        "xhs":    {"title_max_len": 20, "body_max_len": 1000, "hashtag_count": 10,  "fmt": "#{}#"},
        "douyin": {"title_max_len": 30, "body_max_len": 200,  "hashtag_count": 5,   "fmt": "#{}#"},
    }


def check_one(platform, v, rule):
    title = v.get("title", "")
    body = v.get("body", "")
    tags = v.get("tags", [])
    errors = []
    if rule["title_max_len"] and len(title) > rule["title_max_len"]:
        errors.append(f"标题超长 {len(title)}>{rule['title_max_len']}")
    if rule["body_max_len"] and len(body) > rule["body_max_len"]:
        errors.append(f"正文超长 {len(body)}>{rule['body_max_len']}")
    if rule["hashtag_count"] and len(tags) > rule["hashtag_count"]:
        errors.append(f"标签超量 {len(tags)}>{rule['hashtag_count']}")
    for t in tags:
        if not (t.startswith("#") and t.endswith("#") and len(t) > 1):
            errors.append(f"标签格式非法: {t}")
            break
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    rules = fetch_rules(None, None)
    platforms = ["weixin", "xhs", "douyin"]

    with httpx.Client(base_url=BASE, timeout=30) as c:
        tok = login(c)
        H = {"Authorization": f"Bearer {tok}"}
        total = 0
        ok = 0
        fails = []
        for s in range(args.samples):
            r = c.post("/api/topics/generate", json={"domain": "科技", "count": 3}, headers=H)
            tid_topic = r.json()["topics"][0]["id"]
            c.post(f"/api/topics/{tid_topic}/select", headers=H)
            with c.stream("POST", "/api/copy/generate",
                          json={"topic_id": tid_topic,
                                "topic_title": r.json()["topics"][0]["title"],
                                "target_platforms": platforms, "intent": "干货科普",
                                "tone": "专业", "length": "中"}, headers=H) as resp:
                task_id = None
                for line in resp.iter_lines():
                    if line and line.startswith("data: "):
                        ev = json.loads(line[6:])
                        if ev["type"] == "done":
                            task_id = ev["task_id"]
            for p in platforms:
                ad = c.post(f"/api/copy/{task_id}/adapt", json={"platform": p}, headers=H)
                for v in ad.json()["versions"]:
                    total += 1
                    errs = check_one(p, v, rules[p])
                    if errs:
                        fails.append((s, p, v["version_no"], errs))
                    else:
                        ok += 1

    rate = ok / total if total else 0
    print(f"\n=== 平台适配硬性规范符合率（samples={args.samples}）===")
    print(f"  校验项: {total} | 符合: {ok} | 符合率: {rate*100:.1f}%")
    if fails:
        print("  不合规样本:")
        for s, p, vn, errs in fails[:10]:
            print(f"    sample{s} {p} v{vn}: {errs}")
    else:
        print("  全部符合 platform_rules 硬性约束（标题长度/标签格式/数量上限）。")
    print("  注: 语义层(平台调性契合度)需接入真实 LLM 后人工/模型评判。")


if __name__ == "__main__":
    main()
