# -*- coding: utf-8 -*-
"""
M2 · T2.3 评测集构建

从 db/app.db 已标注素材中构建创作评测任务集：
- 维度：平台(weixin/xhs/douyin) × 领域(科技/生活/财经/美妆/教育) × 意图(种草测评/干货科普/观点评论/情感故事) × 风格(专业/轻松/犀利)
- 每条任务含 gold 关联素材（优先同领域同风格，回退同领域，供 M3 Top-5 召回评测）
- 按 8:1:1 确定性分层划分 开发调优集 / 验证集 / 测试集（计划书 6.1）
- 输出 data/eval_set.json + 覆盖矩阵 / 风格分布统计

运行：cd data && python build_eval_set.py
"""
import json
import random
import sqlite3
from collections import defaultdict
from datetime import datetime
from itertools import product
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent / "db" / "app.db"
OUT = HERE / "eval_set.json"
SEED_USER_ID = 1
SPLIT_SEED = 20261010
VERSION = "V1.2.0"  # V1.2.0：领域词表扩展为 5 个（新增美妆/教育）

PLATFORMS = ["weixin", "xhs", "douyin"]
PLATFORM_CN = {"weixin": "公众号", "xhs": "小红书", "douyin": "抖音"}
DOMAINS = ["科技", "生活", "财经", "美妆", "教育"]
INTENTS = ["种草测评", "干货科普", "观点评论", "情感故事"]
STYLES = ["专业", "轻松", "犀利"]
AUDIENCE = {
    "科技": "关注效率的职场与极客人群",
    "生活": "注重生活品质的都市年轻群体",
    "财经": "有资产配置需求的中产投资者",
    "美妆": "爱尝鲜的成分党与化妆新手",
    "教育": "有学习提升需求的职场人与学生家长",
}


def load_materials(conn):
    rows = conn.execute(
        "SELECT id, platforms, domains, styles FROM materials WHERE user_id=?",
        (SEED_USER_ID,),
    ).fetchall()
    by_domain = defaultdict(list)
    by_domain_style = defaultdict(list)
    for mid, _plat, dom, style in rows:
        by_domain[dom].append(mid)
        by_domain_style[(dom, style)].append(mid)
    return by_domain, by_domain_style


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"未找到 {DB_PATH}，请先运行 clean_chunk.py")
    conn = sqlite3.connect(str(DB_PATH))
    by_domain, by_domain_style = load_materials(conn)
    conn.close()

    missing = [d for d in DOMAINS if d not in by_domain]
    if missing:
        raise SystemExit(f"以下领域无素材，无法构建评测集：{missing}（先补齐语料）")

    tasks = []
    tid = 0
    for plat, dom, intent, style in product(PLATFORMS, DOMAINS, INTENTS, STYLES):
        tid += 1
        # gold：优先同领域同风格素材，回退同领域（更贴近真实人设一致性）
        gold = by_domain_style.get((dom, style)) or by_domain[dom]
        tasks.append({
            "id": f"E{tid:03d}",
            "platform": plat,
            "platform_cn": PLATFORM_CN[plat],
            "domain": dom,
            "style": style,
            "intent": intent,
            "audience": AUDIENCE[dom],
            "query": (f"为{dom}领域账号在{PLATFORM_CN[plat]}渠道，"
                      f"以「{style}」口吻产出一篇关于「{intent}」的文案"),
            "expected_domain": dom,
            "gold_material_ids": gold,
        })

    # 8:1:1 确定性划分（全局固定种子打乱，保证可复现）
    rng = random.Random(SPLIT_SEED)
    order = tasks[:]
    rng.shuffle(order)
    n = len(order)
    n_train = int(n * 0.8)
    n_val = int(n * 0.1)
    n_test = n - n_train - n_val
    split_of = {}
    for t in order[:n_train]:
        split_of[t["id"]] = "train"
    for t in order[n_train:n_train + n_val]:
        split_of[t["id"]] = "val"
    for t in order[n_train + n_val:]:
        split_of[t["id"]] = "test"
    for t in tasks:
        t["split"] = split_of[t["id"]]

    # 覆盖矩阵（平台×领域）+ 风格分布
    matrix = defaultdict(lambda: defaultdict(int))
    style_count = defaultdict(int)
    for t in tasks:
        matrix[t["platform"]][t["domain"]] += 1
        style_count[t["style"]] += 1

    out = {
        "meta": {
            "version": VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source_db": str(DB_PATH),
            "total": n,
            "split": {"train": n_train, "val": n_val, "test": n_test},
            "split_method": f"global shuffle (seed={SPLIT_SEED}), 8:1:1",
            "dimensions": {"platforms": PLATFORMS, "domains": DOMAINS,
                           "intents": INTENTS, "styles": STYLES},
            "metric_baseline": {
                "一次可用率": ">=80%",
                "平台规范适配符合率": ">=90%",
                "素材检索Top-5命中率": ">=90%",
                "单篇平均生成时间": "<=15s",
            },
        },
        "coverage_matrix": {p: dict(matrix[p]) for p in PLATFORMS},
        "style_distribution": dict(style_count),
        "tasks": tasks,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"评测集生成：总 {n} 条  train={n_train} val={n_val} test={n_test}")
    print("覆盖矩阵（平台×领域）：")
    hdr = "platform".ljust(8) + "".join(d.ljust(6) for d in DOMAINS)
    print(hdr)
    for p in PLATFORMS:
        print(p.ljust(8) + "".join(str(matrix[p][d]).ljust(6) for d in DOMAINS))
    print("风格分布：", dict(style_count))
    print(f"输出：{OUT}")


if __name__ == "__main__":
    main()
