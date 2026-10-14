# -*- coding: utf-8 -*-
"""
M2 · T2.2 语料清洗 / 段落语义分块 / 标注入库

功能：
1. 读取 data/corpus/ 下 {platform}_{domain}_{style}_{seq}.txt 命名的原始语料
2. 清洗：去除控制字符、归一化空白、剥离合成数据标记行、跨素材内容去重
3. 分块：以空行分隔的「语义段落」为单元；超长段落按句边界切分（<=300 字）
4. 标注：文件名元数据写入 materials.platforms/domains/styles
5. 入库：写入 db/app.db 的 materials + material_chunks（对齐 M1 Schema）
   ⚠️ 安全：materials.content / material_chunks.content 落库前经字段级加密
           （db/security.py，密钥来自环境变量 DB_FIELD_KEY）；种子账号口令经
           pbkdf2 加盐哈希，落实计划书 2.2.4 / 4.2。
6. 向量化钩子：若环境可加载 BGE 则写入 embedding，否则留 NULL（FAISS 主存储在 M3 构建）

运行：
  cd data && python clean_chunk.py            # 入库到 ../db/app.db
  cd data && python clean_chunk.py --reset     # 先清空已入库素材再重建
依赖：仅标准库 + sqlite3 + ../db/security.py（cryptography）
"""
import argparse
import hashlib
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_DIR = HERE / "corpus"
DB_PATH = HERE.parent / "db" / "app.db"
# 引入 M1 数据库安全模块（字段级加密 + 口令哈希，落实计划书 2.2.4 / 4.2）
sys.path.insert(0, str(HERE.parent / "db"))
import security  # noqa: E402

SEED_USER_ID = 1
# 注意：ensure_seed_user 为 INSERT OR IGNORE —— 该口令仅对【全新库】生效；
# 已存在 demo_seed 行的库（含现有 db/app.db）保留原口令 Demo@2026，与文档/Login.tsx 一致。
SEED_USER_PASSWORD = "Demo@2026"  # 示例素材归属账号口令（仅演示，生产请改）
# 官方领域词表（与 frontend/src/api/types.ts 的 DOMAINS 保持一致；新增领域需同步此处）
SEED_DOMAINS = ["科技", "生活", "财经", "美妆", "教育"]
# 美妆/教育热点种子（与既有科技/生活/财经种子同风格；INSERT OR IGNORE 幂等）
HOTSPOT_SEEDS = [
    ("早C晚A护肤翻车，敏感肌避雷清单", "成分党热度下，新手盲目叠加酸类与维 A 导致屏障受损的案例增多，避雷清单类内容高收藏。", "美妆", 88, "上升"),
    ("国货彩妆性价比测评热度走高", "平价彩妆测评与平替对比成为小红书热门选题，真实上脸内容更易获得收藏。", "美妆", 75, "平稳"),
    ("AI 编程课真假难辨，成人自学路径图走红", "成人再就业需求下，零基础自学编程的路径规划与资源筛选内容搜索走高。", "教育", 82, "上升"),
    ("少儿编程到底该不该学引热议", "家长群体对少儿编程的价值与焦虑并存，逻辑思维培养类科普内容稳定输出。", "教育", 70, "平稳"),
]

NAME_RE = re.compile(r"^(?P<platform>[^_]+)_(?P<domain>[^_]+)_(?P<style>[^_]+)_(?P<seq>\d+)\.txt$")
MAX_CHUNK_CHARS = 300
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def clean_text(raw: str) -> str:
    """清洗单篇语料：去控制字符、去标记行、归一化空白。"""
    lines = []
    for line in raw.splitlines():
        # 剥离合成数据标记行
        if "示例语料" in line and "合成数据" in line:
            continue
        # 去掉控制字符（保留正常标点与中文）
        line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", line)
        lines.append(line)
    text = "\n".join(lines)
    # 归一化全/半角空格、折叠多余空行
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_chunks(text: str):
    """段落语义分块：空行分段；超长段落按句边界再切。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    for para in paras:
        if len(para) <= MAX_CHUNK_CHARS:
            chunks.append(para)
            continue
        buf = ""
        for sent in SENT_SPLIT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if buf and len(buf) + len(sent) > MAX_CHUNK_CHARS:
                chunks.append(buf.strip())
                buf = ""
            buf = (buf + sent).strip()
        if buf:
            chunks.append(buf.strip())
    # 二次合并过碎的短块（<15 字且能并入上一块）
    merged = []
    for c in chunks:
        if merged and len(c) < 15 and len(merged[-1]) + len(c) <= MAX_CHUNK_CHARS:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged


def ensure_seed_user(conn: sqlite3.Connection):
    conn.execute(
        """INSERT OR IGNORE INTO users
           (id, username, password_hash, display_name, positioning, tone, domains, status)
           VALUES (?,?,?,?,?,?,?,?)""",
        (SEED_USER_ID, "demo_seed", security.hash_password(SEED_USER_PASSWORD),
         "示例素材归属账号", "综合自媒体", "专业", ",".join(SEED_DOMAINS), 1),
    )
    # 账号可能已存在（INSERT OR IGNORE 不更新），此处回填最新领域词表
    conn.execute(
        "UPDATE users SET domains=? WHERE id=?", (",".join(SEED_DOMAINS), SEED_USER_ID)
    )


def ensure_hotspot_seeds(conn: sqlite3.Connection):
    """补充美妆/教育热点种子（INSERT OR IGNORE 幂等，不覆盖既有数据）。"""
    added = 0
    for title, summary, domain, heat, trend in HOTSPOT_SEEDS:
        dedup = hashlib.sha256(title.encode("utf-8")).hexdigest()
        cur = conn.execute(
            """INSERT OR IGNORE INTO hotspots
               (title, summary, source, url, domain, heat, trend, dedup_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (title, summary, "示例种子", "", domain, heat, trend, dedup),
        )
        added += cur.rowcount
    return added


def try_embed(text: str):
    """可选向量化钩子：BGE 可用则返回向量，否则 None（M3 由 FAISS 主存储）。"""
    try:
        # 延迟导入，避免无模型环境报错
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except Exception:
        return None
    # 真实加载权重需要网络/镜像（计划书 3.3.1），M2 不强制；返回 None 表示待 M3 构建
    return None


def reset_materials(conn: sqlite3.Connection):
    conn.execute("DELETE FROM material_chunks WHERE material_id IN "
                 "(SELECT id FROM materials WHERE user_id=?)", (SEED_USER_ID,))
    conn.execute("DELETE FROM materials WHERE user_id=?", (SEED_USER_ID,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空已入库测试素材后重建")
    args = ap.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库 {DB_PATH}，请先运行 db/init_db.py 初始化")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_seed_user(conn)
    hotspots_added = ensure_hotspot_seeds(conn)
    if args.reset:
        reset_materials(conn)
        conn.commit()

    seen_content_hash = set()
    materials_added = 0
    chunks_added = 0
    skipped_dup = 0

    files = sorted(CORPUS_DIR.glob("*.txt"))
    for f in files:
        m = NAME_RE.match(f.name)
        if not m:
            print(f"  跳过（文件名不符合规范）: {f.name}")
            continue
        meta = m.groupdict()
        raw = f.read_text(encoding="utf-8")
        cleaned = clean_text(raw)
        if not cleaned:
            continue
        ch = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        if ch in seen_content_hash:
            skipped_dup += 1
            continue
        seen_content_hash.add(ch)

        chunks = split_chunks(cleaned)
        cur = conn.execute(
            """INSERT INTO materials
               (user_id, title, source_type, content, platforms, domains, styles, char_count)
               VALUES (?,?,?,?,?,?,?,?)""",
            (SEED_USER_ID, f"{meta['domain']}·{meta['style']}示例素材#{meta['seq']}",
             "txt", security.encrypt_field(cleaned), meta["platform"], meta["domain"], meta["style"], len(cleaned)),
        )
        mid = cur.lastrowid
        for i, ck in enumerate(chunks):
            emb = try_embed(ck)
            conn.execute(
                """INSERT INTO material_chunks (material_id, chunk_index, content, embedding, char_count)
                   VALUES (?,?,?,?,?)""",
                (mid, i, security.encrypt_field(ck), emb, len(ck)),
            )
            chunks_added += 1
        conn.execute("UPDATE materials SET chunk_count=? WHERE id=?", (len(chunks), mid))
        materials_added += 1

    conn.commit()

    # 覆盖率统计
    rows = conn.execute(
        "SELECT platforms, domains, COUNT(*) FROM materials WHERE user_id=? GROUP BY platforms, domains",
        (SEED_USER_ID,),
    ).fetchall()
    platforms = {r[0] for r in rows}
    domains = {r[1] for r in rows}
    total_chunks = conn.execute(
        "SELECT COUNT(*) FROM material_chunks WHERE material_id IN (SELECT id FROM materials WHERE user_id=?)",
        (SEED_USER_ID,),
    ).fetchone()[0]
    hotspot_domains = {
        r[0] for r in conn.execute("SELECT DISTINCT domain FROM hotspots WHERE domain IS NOT NULL")
    }

    # 加密落库自检：敏感字段应为密文（落实计划书 2.2.4 / 4.2）
    row = conn.execute(
        "SELECT content FROM materials WHERE user_id=? LIMIT 1", (SEED_USER_ID,)
    ).fetchone()
    if not (row and row[0] and row[0].startswith(security.FIELD_PREFIX)):
        conn.close()
        raise SystemExit("❌ 安全自检失败：materials.content 未加密落库（应落实计划书 2.2.4）")

    conn.close()
    print(f"入库完成：素材 +{materials_added}（去重跳过 {skipped_dup}），分块 +{chunks_added}，热点种子 +{hotspots_added}")
    print(f"平台覆盖 {sorted(platforms)}  领域覆盖 {sorted(domains)}  分块总数 {total_chunks}")
    print(f"热点领域覆盖 {sorted(hotspot_domains)}")
    print("🔒 敏感字段已加密落库（materials.content / material_chunks.content），种子账号口令已 pbkdf2 哈希")
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
