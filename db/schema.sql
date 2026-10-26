-- =============================================================
-- AI 自媒体内容创作助手 — 数据库建表脚本 (SQLite)
-- 对应项目计划书 V1.0.0 第四章 4.2 数据库设计
--
-- V2 热点采集改造后共 14 张表：
--   users / materials / material_chunks /
--   hot_raw_items / hot_signals / hot_source_state / hot_collect_log /
--   hotspots / topics / copy_tasks / copy_versions /
--   platform_rules / stats / hot_feed_event
--
-- 三层热点模型（见 docs/热点采集方案设计V2.md）：
--   hot_raw_items  原始层，追加式时间序列，永不做跨源合并
--   hot_signals    聚合层，跨源同话题合并 + 0-100 热度归一 + 生命周期
--   hotspots       物化层，供 F2 选题按 domain 读取（保留该表是为了不破
--                  topics.hotspot_id 外键，并让 F2 零改动）
--
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
-- hot_raw_items — 原始采集层（追加式，永不更新）
--   存在的意义：只有保留"每轮抓到了什么"，才能算出
--   ① 首次上榜时间（新颖度）② 是否持续在榜（趋势）③ 跨源覆盖度
--   ⚠️ 本层禁止跨源合并：同名话题在不同平台各自保留一行，
--      合并发生在 hot_signals 层（两层是分工，不是矛盾）。
-- -------------------------------------------------------------
CREATE TABLE hot_raw_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_code  TEXT    NOT NULL,                 -- weibo/zhihu/baidu/douyin/bilibili/toutiao
    title        TEXT    NOT NULL,                 -- 原样标题，不做归一化
    summary      TEXT,                             -- 源提供的摘要/描述，可为空
    url          TEXT,
    rank         INTEGER,                          -- 榜单内排名（1-based）
    raw_heat     INTEGER,                          -- 原始热度，仅同源内可比，禁止跨源排序
    batch_id     TEXT    NOT NULL,                 -- 采集批次，秒级：20261014T165003
    fetched_at   TEXT    NOT NULL,                 -- 'YYYY-MM-DD HH:MM:SS'
    UNIQUE (source_code, title, batch_id)          -- 同批次同源同标题只留一条
);

-- -------------------------------------------------------------
-- hot_signals — 聚合层（跨源合并 + 热度归一化 + 生命周期）
-- -------------------------------------------------------------
CREATE TABLE hot_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key   TEXT    NOT NULL UNIQUE,       -- sha256(归一化标题)
    canonical_title TEXT    NOT NULL,              -- 展示标题：取覆盖源中 rank 最优的原题
    summary         TEXT,                          -- 覆盖源中首条非空摘要；全空则兜底文案（禁止 NULL）
    source_count    INTEGER NOT NULL DEFAULT 0,    -- 当前覆盖的来源数（趋势强度）
    sources         TEXT,                          -- 逗号分隔来源码
    best_rank       INTEGER,                       -- 跨源最优排名
    prev_best_rank  INTEGER,                       -- 上轮最优排名（算 trend 用）
    best_raw_heat   INTEGER,                       -- 最优排名那条的原始热度（物化到 hotspots.heat）
    norm_heat       REAL    NOT NULL DEFAULT 0,    -- 0-100 归一化热度
    prev_norm_heat  REAL,                          -- 上轮归一化热度（算 trend 用）
    trend           TEXT,                          -- 上升/平稳/下降
    domain_hint     TEXT,                          -- 命中关键词（调试用，逗号分隔）
    main_domain     TEXT,                          -- 主领域；未命中为「综合」
    first_seen_at   TEXT    NOT NULL,              -- 首次上榜（只在新 key 插入时写）
    last_seen_at    TEXT    NOT NULL,              -- 最近在榜（退榜判据）
    source_url      TEXT,                          -- 代表链接
    suggestion      TEXT,                          -- 切入建议（二期惰性生成，可空）
    suggestion_at   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- hot_source_state — 来源健康状态（熔断退避依据）
-- -------------------------------------------------------------
CREATE TABLE hot_source_state (
    source_code   TEXT PRIMARY KEY,
    last_ok_at    TEXT,
    last_error    TEXT,
    fail_streak   INTEGER NOT NULL DEFAULT 0,
    backoff_until TEXT,                            -- 该时刻前不再请求该源
    total_fetched INTEGER NOT NULL DEFAULT 0
);

-- -------------------------------------------------------------
-- hot_collect_log — 采集日志（成功率 / P95 耗时的唯一数据来源）
--   没有这张表，"采集成功率 ≥95%""P95 ≤5s" 就是空口指标
-- -------------------------------------------------------------
CREATE TABLE hot_collect_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT    NOT NULL,
    source_code TEXT    NOT NULL,
    ok          INTEGER NOT NULL,                  -- 1 成功 / 0 失败
    item_count  INTEGER DEFAULT 0,
    elapsed_ms  INTEGER,
    error       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------
-- hotspots — 热点（物化宽表，由 hot_signals 物化而来）
--   ⚠️ 命名铁律：本表 platform 是「热榜来源码」(weibo/zhihu/...)，
--      与 platform_rules.platform「发布平台码」(weixin/xhs/douyin) 语义
--      完全不同，禁止混用同一个常量。
-- -------------------------------------------------------------
CREATE TABLE hotspots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id     INTEGER,                       -- 物化来源（可空：历史种子无 signal）
    title         TEXT    NOT NULL,
    summary       TEXT,
    source        TEXT,                          -- 展示用来源名，如「微博热搜」
    platform      TEXT,                          -- 来源码 weibo/zhihu/...（★非发布平台码）
    url           TEXT,
    domain        TEXT,                          -- ★主领域；未命中为「综合」
    heat          INTEGER DEFAULT 0,             -- 原始热度，★仅为不破坏前端契约保留，不参与排序
    norm_heat     REAL    DEFAULT 0,             -- 0-100 归一化热度（排序主用）
    source_count  INTEGER DEFAULT 1,             -- 覆盖来源数
    trend         TEXT,
    first_seen_at TEXT,
    is_new        INTEGER DEFAULT 1,             -- 是否新上榜（默认视图用）
    suggestion    TEXT,                          -- 切入建议
    dedup_hash    TEXT,                          -- = hot_signals.canonical_key
    collected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (dedup_hash),
    FOREIGN KEY (signal_id) REFERENCES hot_signals(id) ON DELETE SET NULL
);

-- -------------------------------------------------------------
-- hot_feed_event — 推荐流事件（采纳率 = 分子/分母，缺一没意义）
--   expose  ：前端真正渲染出一屏时上报一次，item_count = 本次渲染条数（分母）
--   adopt   ：点「用它做选题」时上报（分子·弱信号＝意向）
--   generate：/topics/generate 带 signal_id 成功后由**服务端**写入（强信号＝真用了）
--
--   ★ 为什么不记 GET /hotspots 的返回条数当分母：GET 是只读接口，一次请求
--     不代表真的渲染（前端会为了补建议角度再取一轮、mock 也会重取），
--     按请求计数等于给自己的分母灌水。曝光必须由"真的画出来了"来报。
--   ★ created_at 必须显式写本地时间：DEFAULT (datetime('now')) 是 UTC，
--     与本地 now() 相差 8 小时，会让"近 7 天"窗口整体偏移（hot_collect_log 踩过）
--   ★ hotspot_id 故意不加外键：热点退榜（is_active=0）不删行，但一旦真要清理，
--     历史采纳记录不该跟着消失，否则趋势图会凭空回溯变少
-- -------------------------------------------------------------
CREATE TABLE hot_feed_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    action     TEXT    NOT NULL,                -- expose / adopt / generate
    hotspot_id INTEGER,
    signal_id  INTEGER,
    surface    TEXT,                            -- feed / all / platform（来自哪个视图）
    position   INTEGER,                         -- 点击时在列表中的名次（用于看位置偏差）
    item_count INTEGER DEFAULT 0,               -- expose：本次渲染条数
    domain     TEXT,
    created_at TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
    hashtags               TEXT,                     -- 话题标签
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
-- 索引
--   V2 前 10 个 idx_*：materials/chunks/domain/topics×2/copy×4/stats
--   V2 净增 8 个：raw 2 + signals 3 + collect_log 1 + hotspots 2
--   （idx_hotspots_domain 为同名替换，不计入净增）
--   P2-7 净增 2 个：hot_feed_event 按用户/按动作各 1
--   合计 20 个，init_db.verify() 断言 >= 20
-- =============================================================
CREATE INDEX idx_materials_user      ON materials(user_id);
CREATE INDEX idx_chunks_material     ON material_chunks(material_id);
CREATE INDEX idx_topics_user         ON topics(user_id);
CREATE INDEX idx_topics_hotspot      ON topics(hotspot_id);
CREATE INDEX idx_copy_tasks_user     ON copy_tasks(user_id);
CREATE INDEX idx_copy_tasks_topic    ON copy_tasks(topic_id);
CREATE INDEX idx_copy_versions_task  ON copy_versions(copy_task_id);
CREATE INDEX idx_copy_versions_platform ON copy_versions(platform);   -- 加速按平台匹配 platform_rules 的适配检索
CREATE INDEX idx_stats_user_date     ON stats(user_id, stat_date);

-- 热点三层模型
CREATE INDEX idx_hot_raw_lookup      ON hot_raw_items(source_code, title, fetched_at);
CREATE INDEX idx_hot_raw_batch       ON hot_raw_items(batch_id);
CREATE INDEX idx_hot_signals_last_seen ON hot_signals(last_seen_at);
CREATE INDEX idx_hot_signals_heat    ON hot_signals(norm_heat);
CREATE INDEX idx_hot_signals_domain  ON hot_signals(main_domain, last_seen_at);
CREATE INDEX idx_hot_collect_log_batch ON hot_collect_log(batch_id, source_code);
CREATE INDEX idx_hotspots_domain     ON hotspots(domain, is_active);
CREATE INDEX idx_hotspots_platform   ON hotspots(platform, is_active);
CREATE INDEX idx_hotspots_active_heat ON hotspots(is_active, norm_heat);

-- 推荐流事件：按用户查"我采纳过哪些"、按动作+时间算近 7 天采纳率
CREATE INDEX idx_hot_feed_event_user   ON hot_feed_event(user_id, created_at);
CREATE INDEX idx_hot_feed_event_action ON hot_feed_event(action, created_at);

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
