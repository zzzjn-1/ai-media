#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 自媒体内容创作助手 — 数据库初始化与自检脚本
依赖：仅 Python 标准库 (sqlite3, os, sys)

用法：
    python init_db.py            # 在 db/ 下生成 app.db（已存在则提示用 --reset）
    python init_db.py --reset    # 删除旧库并重建
    python init_db.py --check    # 仅校验 schema.sql 可建库并跑通外键/索引，用临时库
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema.sql")
TARGET = os.path.join(HERE, "app.db")

EXPECTED_TABLES = [
    "users", "materials", "material_chunks",
    # 热点三层模型 + 健康状态 + 采集日志（V2 新增 4 张）
    "hot_raw_items", "hot_signals", "hot_source_state", "hot_collect_log",
    "hotspots", "topics",
    # 推荐流事件（P2-7：曝光/采纳/出题，采纳率的分母与分子）
    "hot_feed_event",
    "copy_tasks", "copy_versions", "platform_rules", "stats",
]


def read_schema() -> str:
    with open(SCHEMA, "r", encoding="utf-8") as f:
        return f.read()


def build(conn: sqlite3.Connection) -> None:
    conn.executescript(read_schema())
    conn.commit()


def verify(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # 1) 14 张表齐全
    rows = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    tables = {r[0] for r in rows}
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    assert not missing, f"缺少表：{missing}"
    print(f"  [OK] 表数量 {len(tables)}，{len(EXPECTED_TABLES)} 张核心表齐全")

    # 2) 外键开关已开启
    fk = cur.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1, "foreign_keys 未开启"
    print("  [OK] 外键约束已启用 (PRAGMA foreign_keys=ON)")

    # 3) 索引存在（V2 后 18 个 + P2-7 事件表 2 个 = 20；其中 idx_hotspots_domain 为同名替换）
    idx = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_%'"
    ).fetchall()
    assert len(idx) >= 20, f"索引不足：{len(idx)}（应 >= 20）"
    print(f"  [OK] 自定义索引 {len(idx)} 个已建立")

    # 4) 平台规则种子数据
    n = cur.execute("SELECT COUNT(*) FROM platform_rules").fetchone()[0]
    assert n >= 3, f"平台规则种子缺失：{n}"
    print(f"  [OK] platform_rules 种子数据 {n} 条（公众号/小红书/抖音）")

    # 5) 外键级联删除实测
    cur.execute(
        "INSERT INTO users(username,password_hash) VALUES('demo','$2b$xxx')"
    )
    uid = cur.lastrowid
    cur.execute(
        "INSERT INTO materials(user_id,title) VALUES(?,?)", (uid, "测试素材")
    )
    mid = cur.lastrowid
    cur.execute(
        "INSERT INTO material_chunks(material_id,chunk_index,content) VALUES(?,?,?)",
        (mid, 0, "段落一"),
    )
    conn.commit()
    cur.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    leftover = cur.execute(
        "SELECT COUNT(*) FROM material_chunks WHERE material_id=?", (mid,)
    ).fetchone()[0]
    assert leftover == 0, "级联删除失败：子表仍有残留"
    print("  [OK] 外键级联删除实测通过（删用户→素材/分块联动清除）")

    # 6) 去重约束实测（三层各自承担不同职责，三处都要验）
    # 6.1 raw 层：同批次同源同标题只留一条
    cur.execute(
        "INSERT INTO hot_raw_items(source_code,title,batch_id,fetched_at) "
        "VALUES('weibo','热点A','20261014T165003','2026-10-14 16:50:03')"
    )
    conn.commit()
    try:
        cur.execute(
            "INSERT INTO hot_raw_items(source_code,title,batch_id,fetched_at) "
            "VALUES('weibo','热点A','20261014T165003','2026-10-14 16:50:03')"
        )
        conn.commit()
        raise AssertionError("hot_raw_items 唯一约束未生效")
    except sqlite3.IntegrityError:
        conn.rollback()
        print("  [OK] hot_raw_items(source_code,title,batch_id) 唯一约束生效")

    # 6.2 signal 层：同一归一化指纹只留一条（跨源合并的落点）
    cur.execute(
        "INSERT INTO hot_signals(canonical_key,canonical_title,first_seen_at,last_seen_at) "
        "VALUES('k1','热点A','2026-10-14 16:50:03','2026-10-14 16:50:03')"
    )
    conn.commit()
    try:
        cur.execute(
            "INSERT INTO hot_signals(canonical_key,canonical_title,first_seen_at,last_seen_at) "
            "VALUES('k1','热点A 副本','2026-10-14 16:50:03','2026-10-14 16:50:03')"
        )
        conn.commit()
        raise AssertionError("hot_signals.canonical_key 唯一约束未生效")
    except sqlite3.IntegrityError:
        conn.rollback()
        print("  [OK] hot_signals.canonical_key 唯一约束生效（跨源聚合去重）")

    # 6.3 物化层：hotspots.dedup_hash 唯一且 upsert 行数不增（F2 依赖）
    cur.execute("INSERT INTO hotspots(title,dedup_hash) VALUES('热点A','h1')")
    conn.commit()
    cur.execute(
        "INSERT INTO hotspots(title,dedup_hash) VALUES('热点A副本','h1') "
        "ON CONFLICT(dedup_hash) DO UPDATE SET title=excluded.title"
    )
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM hotspots WHERE dedup_hash='h1'").fetchone()[0]
    assert n == 1, f"hotspots upsert 未生效：行数 {n}"
    print("  [OK] hotspots.dedup_hash 唯一约束 + upsert 行数不增")

    # 7) 清理自检痕迹
    #    ⚠️ verify() 会在【正式库】上跑（--reset 路径），自检写入的测试行必须删干净。
    #    否则 clean_chunk.py 的 seed 逻辑会看到"已有热点"而跳过注种，
    #    结果是空库启动、看板无数据（已踩过一次）。
    cur.execute("DELETE FROM hotspots WHERE dedup_hash='h1'")
    cur.execute("DELETE FROM hot_signals WHERE canonical_key='k1'")
    cur.execute("DELETE FROM hot_raw_items WHERE title='热点A' AND source_code='weibo'")
    conn.commit()
    print("  [OK] 自检测试行已清理（不污染正式库）")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--check":
        import tempfile
        tmp = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(tmp)
        conn.execute("PRAGMA foreign_keys=ON")
        print("→ 临时库建库 + 自检 ...")
        build(conn)
        verify(conn)
        conn.close()
        os.remove(tmp)
        print("✅ schema.sql 校验通过，可安全用于生产建库。")
        return 0

    if os.path.exists(TARGET) and mode != "--reset":
        print(f"⚠️  {TARGET} 已存在。如需重建请执行：python init_db.py --reset")
        return 1

    if mode == "--reset" and os.path.exists(TARGET):
        os.remove(TARGET)
        print(f"🗑  已删除旧库 {TARGET}")

    conn = sqlite3.connect(TARGET)
    conn.execute("PRAGMA foreign_keys=ON")
    print(f"→ 生成数据库 {TARGET} ...")
    build(conn)
    verify(conn)
    conn.close()
    print(f"✅ 数据库初始化完成：{TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
