# -*- coding: utf-8 -*-
"""
M5 · 生成链路评测（文案一次可用率 + 单篇平均生成耗时）— 落实计划书 6.2

用法（项目根目录，需后端已启动于 :8000）:
  python api/eval_generate.py                    # 测试集，默认 10 条
  python api/eval_generate.py --split test --limit 0   # 测试集全量
  python api/eval_generate.py --split all --limit 20   # 全量抽 20 条
  python api/eval_generate.py --n-versions 3

评测口径:
  - 一次可用率（规则口径，离线可跑）：逐版本校验「经轻微修改即可发布」的
    硬性特征——标题非空、正文≥80 字且具备段落结构、无占位/待补标记、
    含话题标签。逐版本计通过率，对照计划书阈值 ≥80%。
  - 一次可用率（LLM-judge 口径，可选）：设置环境变量 LLM_API_KEY（及
    LLM_BASE_URL/LLM_MODEL）后，对大模型产出按同一判分标准自动评判；
    未配置时自动跳过，不阻塞离线基线。
  - 单篇平均生成时间：从发起 SSE 请求到收到 done 事件的端到端墙钟耗时
    （含大模型 API 调用），取均值，对照阈值 ≤15s。

说明:
  - 离线环境（llm_backend=mock）下生成为模板文案，耗时与规则口径仅作
    回归基线；接真实 LLM 后重跑本脚本即为验收数据（脚本逻辑不变）。
  - 该脚本只读评测集、通过 API 触发生成，不修改任何业务数据。
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

import httpx

# 让项目根 .env（含 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL）在运行本脚本时也能被
# 加载，与 api/config.py 的离线 .env 解析保持一致（仅注入尚未导出的同名变量）。
_PROJ_ENV = Path(__file__).resolve().parent.parent / ".env"
if _PROJ_ENV.exists():
    for _line in _PROJ_ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8000")
DEMO = ("demo_seed", "Demo@2026")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVAL_PATH = ROOT / "data" / "eval_set.json"

USABLE_THRESHOLD = 0.80  # 计划书 2.2.1：文案一次可用率 ≥80%
TIME_THRESHOLD = 15.0    # 计划书 2.2.1：单篇平均生成时间 ≤15s

# 占位/待补标记（出现即判不可用）
PLACEHOLDER_RE = re.compile(r"TODO|待补充|待填充|待完善|占位|XXX|Lorem|【待", re.IGNORECASE)


def login(c: httpx.Client) -> str:
    r = c.post("/api/auth/login", json={"username": DEMO[0], "password": DEMO[1]})
    r.raise_for_status()
    return r.json()["access_token"]


def rule_score_version(v: dict):
    """规则口径：单版本是否『经轻微修改即可发布』。返回 (ok, [原因...])。"""
    errs = []
    title = (v.get("title") or "").strip()
    body = (v.get("optimized_body") or v.get("body") or "").strip()
    tags = v.get("tags") or []
    if not title:
        errs.append("标题为空")
    if len(body) < 80:
        errs.append(f"正文过短({len(body)}<80字)")
    if len(body) >= 80 and body.count("\n\n") < 1 and len(body) < 200:
        errs.append("缺少段落结构")
    if PLACEHOLDER_RE.search(body):
        errs.append("含占位/待补标记")
    if not tags:
        errs.append("无话题标签")
    return (not errs), errs


def llm_judge(v: dict) -> bool | None:
    """LLM-judge 口径：配置 LLM_API_KEY 时评判单版本可否直接发布；未配置返回 None。"""
    key = os.environ.get("LLM_API_KEY")
    if not key:
        return None
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    body = (v.get("optimized_body") or v.get("body") or "").strip()
    prompt = (
        "你是内容发布审校。判断下面文案是否『经轻微修改即可直接发布』"
        "（结构完整、无占位符、无明显事实错误风险提示缺失）。"
        "只回答 YES 或 NO。\n\n"
        f"标题：{v.get('title') or ''}\n正文：{body[:1200]}"
    )
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0, "max_tokens": 4},
            timeout=30,
        )
        ans = r.json()["choices"][0]["message"]["content"].strip().upper()
        return ans.startswith("YES")
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] LLM-judge 调用失败：{e}")
        return None


def run_task(c: httpx.Client, H: dict, task: dict, n_versions: int):
    """对单条评测任务跑生成链路，返回 (耗时秒, [version...], error)。"""
    req = {
        "topic_title": f"{task['domain']}领域{task['intent']}选题样例",
        "target_platforms": [task["platform"]],
        "intent": task["intent"],
        "tone": task["style"],
        "length": "中",
        "audience": task.get("audience") or "关注效率的人群",
        "n_versions": n_versions,
    }
    versions, err = [], None
    t0 = time.time()
    try:
        with c.stream("POST", "/api/copy/generate", json=req, headers=H, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                if ev.get("type") == "version":
                    versions.append(ev)
                elif ev.get("type") == "error":
                    err = ev.get("msg")
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return round(time.time() - t0, 2), versions, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["all", "train", "val", "test"])
    ap.add_argument("--limit", type=int, default=10, help="最多评测条数，0=全部")
    ap.add_argument("--n-versions", type=int, default=3)
    args = ap.parse_args()

    data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    tasks = [t for t in data["tasks"] if args.split == "all" or t["split"] == args.split]
    if args.limit and len(tasks) > args.limit:
        step = max(1, len(tasks) // args.limit)
        tasks = tasks[::step][:args.limit]
    print(f"评测集: {EVAL_PATH.name} ({data['meta']['version']}) | split={args.split} | 任务数 {len(tasks)}")

    use_llm = bool(os.environ.get("LLM_API_KEY"))
    print(f"一次可用率口径: {'规则 + LLM-judge' if use_llm else '规则（离线基线；设置 LLM_API_KEY 可启用 LLM-judge）'}")

    times, rule_ok, rule_total = [], 0, 0
    llm_ok, llm_total = 0, 0
    by_domain = {}
    bad_cases = []
    errors = []

    with httpx.Client(base_url=BASE, timeout=180) as c:
        H = {"Authorization": f"Bearer {login(c)}"}
        for i, t in enumerate(tasks, 1):
            sec, versions, err = run_task(c, H, t, args.n_versions)
            if err or not versions:
                errors.append((t["id"], err or "无版本输出"))
                print(f"  [{i}/{len(tasks)}] {t['id']} 失败: {err or '无版本输出'}")
                continue
            times.append(sec)
            task_rule_ok = 0
            for v in versions:
                rule_total += 1
                ok, reasons = rule_score_version(v)
                if ok:
                    rule_ok += 1
                    task_rule_ok += 1
                else:
                    bad_cases.append((t["id"], t["domain"], v.get("version_no"), reasons))
                if use_llm:
                    j = llm_judge(v)
                    if j is not None:
                        llm_total += 1
                        llm_ok += 1 if j else 0
            d = by_domain.setdefault(t["domain"], [0, 0, 0.0])
            d[0] += task_rule_ok
            d[1] += len(versions)
            d[2] += sec
            print(f"  [{i}/{len(tasks)}] {t['id']} {t['domain']}/{t['platform']} "
                  f"{sec:.1f}s 版本 {task_rule_ok}/{len(versions)} 通过")

    rule_rate = rule_ok / rule_total if rule_total else 0.0
    avg_time = sum(times) / len(times) if times else 0.0
    print("\n=== 文案一次可用率（规则口径，逐版本）===")
    print(f"  样本版本: {rule_total} | 通过: {rule_ok} | 一次可用率: {rule_rate*100:.1f}%")
    print(f"  阈值(计划书 6.2): >={USABLE_THRESHOLD*100:.0f}%  |  实测: {rule_rate*100:.1f}%  "
          f"→ {'达标' if rule_rate >= USABLE_THRESHOLD else '未达标'}")
    if use_llm and llm_total:
        llm_rate = llm_ok / llm_total
        print(f"=== 一次可用率（LLM-judge 口径）===")
        print(f"  样本版本: {llm_total} | 通过: {llm_ok} | 一次可用率: {llm_rate*100:.1f}%  "
              f"→ {'达标' if llm_rate >= USABLE_THRESHOLD else '未达标'}")

    print("\n=== 单篇平均生成时间 ===")
    print(f"  任务数: {len(times)} | 平均: {avg_time:.2f}s | 最大: {max(times) if times else 0:.2f}s")
    print(f"  阈值(计划书 6.2): <={TIME_THRESHOLD:.0f}s  |  实测: {avg_time:.2f}s  "
          f"→ {'达标' if avg_time <= TIME_THRESHOLD else '未达标'}")

    print("\n  分领域（规则口径通过版本/总版本，累计耗时）:")
    for d, (ok_, tot_, sec_) in sorted(by_domain.items()):
        print(f"    {d}: {ok_}/{tot_}  {sec_:.1f}s")

    if bad_cases:
        print(f"\n  bad case（{len(bad_cases)} 个版本未通过，前 10）:")
        for tid, dom, vn, reasons in bad_cases[:10]:
            print(f"    {tid} {dom} v{vn}: {reasons}")
    if errors:
        print(f"\n  失败任务（{len(errors)}）:")
        for tid, msg in errors[:10]:
            print(f"    {tid}: {msg}")

    print("\n注: 离线环境生成为 Mock 模板，以上为回归基线；接真实 LLM 后重跑本脚本即为验收数据。")

    # 机器可读结果（供测试报告汇总）
    out = ROOT / "data" / "eval_generate_result.json"
    out.write_text(json.dumps({
        "split": args.split,
        "task_count": len(times),
        "version_total": rule_total,
        "version_pass": rule_ok,
        "usable_rate_rule": round(rule_rate, 4),
        "usable_rate_llm": round(llm_ok / llm_total, 4) if llm_total else None,
        "avg_generation_seconds": round(avg_time, 2),
        "max_generation_seconds": max(times) if times else 0,
        "by_domain": {d: {"pass": v[0], "total": v[1], "seconds": round(v[2], 1)}
                      for d, v in by_domain.items()},
        "bad_cases": [{"task": t, "domain": d, "version": v, "reasons": r}
                      for t, d, v, r in bad_cases],
        "errors": [{"task": t, "msg": m} for t, m in errors],
        "thresholds": {"usable_rate": USABLE_THRESHOLD, "avg_seconds": TIME_THRESHOLD},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入: {out}")


if __name__ == "__main__":
    main()
