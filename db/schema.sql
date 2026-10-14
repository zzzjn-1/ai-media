-- =============================================================
-- AI 自媒体内容创作助手 — 数据库建表脚本 (SQLite)
-- 对应项目计划书 V1.0.0 第四章 4.2 数据库设计
-- 9 张核心表：users / materials / material_chunks / hotspots /
--             topics / copy_tasks / copy_versions /
--             platform_rules / stats
-- 运行：sqlite3 app.db < schema.sql   (或 init_db.py)
-- =============================================================

PRAGMA foreign_keys = ON;

-- -------------------------------------------------------------
-- users — 用户与账号人设
-- -------------------------------------------------------------
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,                 -- bcrypt/argon2 哈希，绝不存明文
    email         TEXT    UNIQUE,
    display_name  TEXT,
    positioning   TEXT,                             -- 账号定位
    tone          TEXT,                             -- 默认口吻（专业/轻松/犀利…）
    domains       TEXT,                             -- 领域标签，逗号分隔
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    status        INTEGER NOT NULL DEFAULT 1        -- 1=正常 0=停用
);

-- -------------------------------------------------------------
-- materials — 素材
-- -------------------------------------------------------------
CREATE TABLE materials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    title        TEXT,
    source_type  TEXT    NOT NULL DEFAULT 'text',   -- text/docx/txt/md
    content      TEXT,                              -- 清洗后正文（敏感内容按规则脱敏）
    file_path    TEXT,                              -- 原始文件落盘路径（可选）
    platforms    TEXT,                              -- 平台标签，逗号分隔
    domains      TEXT,                              -- 领域标签
    styles       TEXT,                              -- 风格标签
    custom_tags  TEXT,                              -- 自定义标签
    char_count   INTEGER DEFAULT 0,
    chunk_count  INTEGER DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- -------------------------------------------------------------
-- material_chunks — 素材分块
--   向量化结果由 FAISS 主存储；sqlite 仅存分块文本与元数据。
--   embedding 列保留为可选字段，便于切换到 Chroma 等可替代后端。
-- -------------------------------------------------------------
CREATE TABLE material_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,                  -- 分块序号
    content     TEXT    NOT NULL,                   -- 分块文本
    embedding   BLOB,                               -- 可选：BGE 向量（FAISS 为主存储）
    char_count  INTEGER DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
    UNIQUE (material_id, chunk_index)
);

-- -------------------------------------------------------------
-- hotspots — 热点（APScheduler 定时采集 + 去重）
-- -------------------------------------------------------------
CREATE TABLE hotspots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    summary      TEXT,
    source       TEXT,                              -- 采集源
    url          TEXT,
    domain       TEXT,                              -- 领域
    heat         INTEGER DEFAULT 0,                 -- 热度值
    trend        TEXT,                              -- 上升趋势描述
    dedup_hash   TEXT,                              -- 去重指纹
    collected_at TEXT    NOT NULL DEFAULT (datetime('now')),
    is_active    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (dedup_hash)
);

-- -------------------------------------------------------------
-- topics — 选题
-- -------------------------------------------------------------
CREATE TABLE topics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    hotspot_id       INTEGER,                       -- 可关联热点（可选）
    title            TEXT    NOT NULL,
    angle            TEXT,                          -- 创作角度
    reason           TEXT,                          -- 推荐理由
    target_audience  TEXT,                          -- 目标受众
    status           INTEGER NOT NULL DEFAULT 0,    -- 0=待选用 1=已选用进入创作
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)    REFERENCES users(id)   ON DELETE CASCADE,
    FOREIGN KEY (hotspot_id) REFERENCES hotspots(id) ON DELETE SET NULL
);

-- -------------------------------------------------------------
-- copy_tasks — 创作任务
-- -------------------------------------------------------------
CREATE TABLE copy_tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL,
    topic_id           INTEGER,                      -- 关联选题（可选）
    selected_materials TEXT,                         -- 选用素材 id 列表，JSON
    target_platforms   TEXT,                         -- 目标平台，逗号分隔
    target_audience    TEXT,
    creation_intent    TEXT,                         -- 创作意图
    tone               TEXT,                         -- 语气（专业/轻松/犀利）
    length             TEXT,                         -- 篇幅（短/中/长）
    persona_voice      TEXT,                         -- 人设口吻
    status             TEXT    NOT NULL DEFAULT 'pending', -- pending/running/done/failed
    error_msg          TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)   ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE SET NULL
);

-- -------------------------------------------------------------
-- copy_versions — 文案版本（单次任务 ≥3 版本）
-- -------------------------------------------------------------
CREATE TABLE copy_versions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    copy_task_id           INTEGER NOT NULL,
    version_no             INTEGER NOT NULL,         -- 第几个版本
    platform               TEXT,                     -- 适配平台
    title                  TEXT,
    body                   TEXT,
    hashtags               TEXT,                     -- 话题标签，逗号分隔/JSON
    tone                   TEXT,
    style_consistency      TEXT,                     -- 风格一致性报告
    replacement_suggestions TEXT,                    -- 同质化替换建议
    status                 INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (copy_task_id) REFERENCES copy_tasks(id) ON DELETE CASCADE,
    UNIQUE (copy_task_id, version_no, platform)
);

-- -------------------------------------------------------------
-- platform_rules — 平台规则（多平台适配规则引擎基线）
-- -------------------------------------------------------------
CREATE TABLE platform_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    platform       TEXT    NOT NULL UNIQUE,          -- weixin/xhs/douyin/...
    display_name   TEXT,
    title_max_len  INTEGER,
    body_max_len   INTEGER,
    hashtag_format TEXT,                             -- #话题# 等格式模板
    hashtag_count  INTEGER,
    layout_style   TEXT,
    export_format  TEXT,                             -- txt/docx
    is_active      INTEGER NOT NULL DEFAULT 1
);

-- -------------------------------------------------------------
-- stats — 统计（按用户/日期聚合）
-- -------------------------------------------------------------
CREATE TABLE stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    stat_date       TEXT    NOT NULL,                -- YYYY-MM-DD
    materials_count INTEGER DEFAULT 0,
    topics_count    INTEGER DEFAULT 0,
    copies_count    INTEGER DEFAULT 0,
    avg_gen_seconds REAL    DEFAULT 0,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, stat_date),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- =============================================================
-- 索引（热点查询 / 用户维度检索路径）
-- =============================================================
CREATE INDEX idx_materials_user      ON materials(user_id);
CREATE INDEX idx_chunks_material     ON material_chunks(material_id);
CREATE INDEX idx_hotspots_domain     ON hotspots(domain, is_active);
CREATE INDEX idx_topics_user         ON topics(user_id);
CREATE INDEX idx_topics_hotspot      ON topics(hotspot_id);
CREATE INDEX idx_copy_tasks_user     ON copy_tasks(user_id);
CREATE INDEX idx_copy_tasks_topic    ON copy_tasks(topic_id);
CREATE INDEX idx_copy_versions_task  ON copy_versions(copy_task_id);
CREATE INDEX idx_copy_versions_platform ON copy_versions(platform);   -- 加速按平台匹配 platform_rules 的适配检索
CREATE INDEX idx_stats_user_date     ON stats(user_id, stat_date);

-- =============================================================
-- 平台规则种子数据（公众号 / 小红书 / 抖音 基线规范）
-- 注：具体字数上限以各平台最新规范为准，此处为初始默认值。
-- =============================================================
INSERT INTO platform_rules
    (platform, display_name, title_max_len, body_max_len, hashtag_format, hashtag_count, layout_style, export_format)
VALUES
    ('weixin', '微信公众号', 64,  NULL, '#{}#',  NULL, '图文排版/段落分明',      'docx'),
    ('xhs',    '小红书',     20,  1000, '#{}#',  10,   '短句+emoji+分段',       'txt'),
    ('douyin', '抖音',       30,  200,  '#{}#',  5,    '口语化/强钩子开头',     'txt');
