# M3 交付说明与 API 文档 · AI 自媒体内容创作助手

> 版本：M3 · v1.0  ｜  关联计划书：`AI自媒体内容创作助手项目计划书V1.1.0.docx`（V1.1.0）
> 阶段目标（计划书 5.2.3）：完成「核心算法与后端」，形成可被评测、可离线运行的 FastAPI 服务，支撑后续 M4 前端联调。
> 配套文档：M1（PRD / 架构设计 / DB 设计 / 原型）、M2（语料分块 / 评测集 / 评测方案）。

---

## 一、M3 目标与任务映射（T3.1 – T3.6）

| 计划书任务 | 内容 | M3 交付物（落代码） | 状态 |
|---|---|---|---|
| T3.1 数据接入与向量化 | 素材分块入库 + BGE 向量化 + FAISS 主存储 | `api/core/embeddings.py`（`_BgeEmbedding` 加载 BAAI/bge-large-zh，`sentence-transformers`）、`api/core/vector_store.py`、`api/routers/materials.py`（import/tag/embed/检索）、`db/security.py` 复用 | ✅ |
| T3.2 热点采集调度 | 分源调度 + 跨源聚合 + 退榜 + 熔断 | `api/core/hotspot_collector.py`（采集调度/熔断/清理）、`hot_sources.py`（多适配器）、`hot_signal.py`（聚合与物化）、`hot_rank.py`（相关性）、`domain_keywords.py`、`hot_metrics.py`（事件/采纳率）、`api/routers/hotspots.py`、`api/main.py` lifespan 启动 | ✅ |
| T3.3 选题策划 | 基于热点+人设生成选题，≥3 条 | `api/core/rag_pipeline.py:generate_topics()`、`api/routers/topics.py` | ✅ |
| T3.4 RAG 文案生成 | **LangChain 编排**：检索→**重排**→提示词组装→≥3 版本→风格优化 | `api/core/rag_pipeline.py:generate()`（LCEL Runnable 编排）、**`api/core/rerank.py`（重排）**、`api/core/prompts.py`、`api/core/llm.py`、`api/routers/copy.py`（SSE） | ✅ |
| T3.5 多平台适配 | 规则引擎按平台规范适配 | `api/core/platform_adapter.py`、`api/routers/copy.py:/adapt`、`db/platform_rules` 种子 | ✅ |
| T3.6 后端 API + 文档 | FastAPI 服务 + 14 端点/15 方法 + SSE + API 文档 | `api/main.py`、`api/routers/*`、`api/deps.py`、`api/core/auth.py`、本文件 | ✅ |

> **注：T3.4 的「重排」为本轮补齐项。** 首版实现仅有 FAISS 粗排，未落实 4.1.3「检索器召回 → 重排」环节，导致 Top-5 命中率仅 70.4%（全量）。新增 `api/core/rerank.py` 实现两阶段检索后提升至 **97.2%**（详见第七节 7.1 消融数据）。

**核心设计原则（落实计划书 2.2.5「配置项外置」与 7.4「API 限流→降级」）：**
- **可插拔 Provider**：LLM（`MockLLM` 离线 / `OpenAILLM` OpenAI 兼容）、Embedding（`BGE` 语义 / `offline` 确定性哈希）。无 API Key、无模型权重时后端**离线可运行、可被评测**。
- **两阶段检索**（4.1.3 / 4.5）：FAISS 粗排召回 `RECALL_TOP_K`（默认 40）→ 重排取 `RERANK_TOP_K`（默认 5）。重排策略由 `RERANK_STRATEGY` 控制：`lexical`（默认，零依赖确定性）/ `cross`（cross-encoder，失败自动降级）/ `off`（消融用）。
  - **offline 后端不做哈希粗排**（2026-10-26）：offline 向量是「字符 2-gram 哈希 + 随机符号」（dim=256），碰撞严重且不含语义，用它取 Top-40 等于给重排喂半个随机抽样（只覆盖 270 块语料的 15%）。故 `rag_pipeline.retrieve()` 在 `embedding_backend != bge` 时**直接全量召回**（实测量级 ~9ms），候选池不再是运气的产物。`bge` 后端仍走 FAISS 粗排。
- **字段级加密**：素材标题/正文经 `db/security.py`（Fernet + 派生密钥）加密存储，密钥来自环境变量 `DB_FIELD_KEY`。
- **JWT 鉴权**：`pyjwt` HS256，默认 72h 过期，Bearer 令牌经 `api/deps.py` 校验。

**相关配置项**（均见 `api/config.py`，可用环境变量覆盖）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDING_BACKEND` | `offline` | `offline` 哈希 / `bge` 语义 |
| `RERANK_STRATEGY` | `lexical` | `lexical` / `cross` / `off` |
| `RECALL_TOP_K` | `20` | 粗排召回数 |
| `RERANK_TOP_K` | `5` | 重排后进入提示词的条数 |
| `RETRIEVAL_DOMAIN_FILTER` | `true` | 检索是否优先同领域 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 空 / OpenAI / gpt-4o-mini | 配置后切换真实 LLM |

---

## 二、交付物清单（api/ 与 db/ 变更）

```
api/
  config.py                 配置中心（全部外部依赖经环境变量注入）
  database.py               SQLite 连接 + 加密/口令哈希复用（转发 encrypt_field）
  main.py                   FastAPI 入口；lifespan 启动 FAISS + 调度器；/api/health
  deps.py                   get_current_user（HTTPBearer 解析 JWT）
  requirements.txt          依赖清单（fastapi/uvicorn/faiss-cpu/pyjwt/cryptography/apscheduler/langchain…）
  core/
    auth.py                 JWT 签发/校验
    embeddings.py           EmbeddingService 单例（offline 哈希 / BGE 语义）
    vector_store.py         VectorStore 单例（FAISS 主存储 + 序列化落盘，规避中文路径 fopen）
    llm.py                  MockLLM（离线结构化多版本+确定性优化）/ OpenAILLM
    prompts.py              提示词模板库（生成/优化）
    rerank.py               检索重排（lexical / cross-encoder / off 三策略，落实 4.1.3「重排」）
    rag_pipeline.py         LangChain LCEL 编排：retrieve(两阶段检索) / generate / generate_topics / optimize_text
    platform_adapter.py     PlatformAdapter 规则引擎（platform_rules 驱动）
    hotspot_collector.py    APScheduler 热点采集 + dedup_hash 去重
  routers/
    auth.py                 register / login
    materials.py            import / tag / embed / 检索（含 split_chunks 句边界分块）
    hotspots.py             热点看板
    topics.py               选题生成 / 选用
    copy.py                 文案生成(SSE) / 风格优化 / 平台适配
    persona.py              账号人设配置
    export.py               导出 txt/docx（docx 由标准库 zipfile 生成，零额外依赖）
  _e2e_test.py             端到端冒烟测试（覆盖全部关键端点）
  _demo.py                 9 环节连贯演示脚本（health→登录→热点→选题→SSE→优化→三端适配→检索→人设→导出）
  eval_retrieval.py        检索 Top-5 命中率评测（两阶段检索 / --no-rerank 消融；offline / bge 双后端）
  eval_adapt.py            平台适配硬性规范符合率自检
db/
  schema.sql               14 张表（V2 新增热点三层 + 来源状态 + 采集日志；P2-7 新增推荐流事件）；platform_rules 种子
  init_db.py               建库 + 自检（外键/索引/级联/去重约束实测）
data/（M2 交付，本阶段刷新）
  eval_set.json            评测集（180 条，V1.2.0，8:1:1 划分；本次按当前库重对齐 gold 素材）
  build_eval_set.py        评测集构建
```

> 说明：`data/clean_chunk.py`、`db/security.py` 等 M2 资产被直接复用，未改动其语义；`db/schema.sql` 仅修正 `platform_rules.hashtag_format` 种子值（见第七节「修复记录」）。

---

## 三、架构与模块映射（功能模块 ↔ 代码）

| 计划书功能模块 | 端点/能力 | 代码落点 |
|---|---|---|
| F1 素材库与热点 | 素材导入/打标/向量化/检索、热点推荐流、切入建议角度 | `materials.py`、`hotspots.py`、`vector_store.py`、`embeddings.py`、`hotspot_collector.py`、`hot_sources.py`、`hot_signal.py`、`hot_rank.py`、`hot_suggestion.py`、`hot_metrics.py`（采纳漏斗） |
| F2 选题策划 | 选题生成、一键选用、热点直传出题 | `topics.py`、`rag_pipeline.generate_topics` |
| F3 文案生成(RAG) | 检索增强、≥3 版本、SSE 流式 | `copy.py:/generate`、`rag_pipeline.generate`、`llm.py`、`prompts.py` |
| F4 风格优化 | 语气/篇幅/人设参数化重写 + 同质化抑制 | `copy.py:/optimize`、`rag_pipeline.optimize_text` |
| F5 多平台适配 | 规则引擎适配 + 发布备注 | `copy.py:/adapt`、`platform_adapter.py`、`export.py` |
| F6 用户系统/管理 | 注册/登录/人设/JWT | `auth.py`、`persona.py`、`core/auth.py`、`deps.py` |

**RAG 链路（计划书 4.5）**：`检索(FAISS Top-K) → 重排 → 提示词组装 → ≥3 版本生成 → 风格优化 → 平台适配`。离线 Mock 链路与在线 BGE/OpenAI 链路共用同一套编排，仅 Provider 可替换。

---

## 四、API 端点对照（计划书 4.3 基线 14 端点 / 15 方法；当前实测 21 路由 / 22 方法）

> 实测：`/openapi.json` 暴露 **15 个路径 / 16 个 HTTP 方法**。其中 `/api/health` 为运维存活探针（不计入计划书 14 端点）；功能端点恰为 **14 个**，且 `/api/persona` 含 GET+POST 两方法，合计 **15 个 HTTP 方法**，与计划书完全一致。所有 `copy/generate` 为 **SSE 流式**。

| # | 方法 | 路径 | 模块 | 说明 |
|---|---|---|---|---|
| 1 | POST | `/api/auth/register` | F6 | 注册（pbkdf2 口令哈希） |
| 2 | POST | `/api/auth/login` | F6 | 登录（签发 JWT） |
| 3 | GET | `/api/health` | 运维 | 存活 + 后端状态 + 各表计数（额外，不入 14） |
| 4 | GET | `/api/hotspots` | F1 | 热点推荐流（`view=feed\|all`、`domain`/`platform`/`new_only`/`limit`；带 token 时个性化排序） |
| 5 | GET | `/api/topics` | F2 | 历史选题列表（`status`/`q`/`limit`/`offset` 分页，**新增**） |
| 6 | POST | `/api/topics/generate` | F2 | 选题生成（≥3 条） |
| 7 | POST | `/api/topics/{topic_id}/select` | F2 | 选用选题 |
| 8 | GET | `/api/materials` | F1 | 素材列表 / 语义+关键词检索 |
| 9 | POST | `/api/materials/import` | F1 | 素材导入（自动分块入库） |
| 10 | POST | `/api/materials/{id}/tag` | F1 | 素材打标（平台/领域/风格/标签） |
| 11 | POST | `/api/materials/{id}/embed` | F1 | 重建 FAISS 向量索引 |
| 12 | POST | `/api/copy/generate` | F3 | 文案生成（SSE，≥3 版本） |
| 13 | GET | `/api/copy/{task_id}` | F3 | 历史创作任务读取（摘要 + 聚合版本 + 按平台分组，**新增**，供刷新恢复） |
| 14 | POST | `/api/copy/{task_id}/optimize` | F4 | 风格优化 |
| 15 | POST | `/api/copy/{task_id}/adapt` | F5 | 平台适配（支持生成后追加新平台） |
| 16 | GET/POST | `/api/persona` | F6 | 人设配置（**1 端点 2 方法**） |
| 17 | POST | `/api/export` | F5 | 导出 txt / docx（含发布备注） |
| 18 | GET | `/api/materials/{id}/chunks` | F1 | 素材分块预览（M4 新增，解密正文） |
| 19 | POST | `/api/hotspots/refresh` | F1 | 加速刷新（**需鉴权** + 最小间隔 + 来源数上限 + 批次复用） |
| 20 | GET | `/api/hotspots/status` | F1 | 采集健康状态 + 近 7 天成功率与 P95 + **采纳漏斗**（**需鉴权**） |
| 21 | POST | `/api/hotspots/events` | F1 | 推荐流事件上报 `expose`/`adopt`（**需鉴权**，采纳率的分母与分子） |

> **口径校准（V2 热点改造 + P2-7 埋点 + 选题历史列表 + 流程恢复后）**：计划书 4.3 基线为 M3 完成时实测「14 端点 / 15 方法」；M4 新增素材分块预览、选题选用、创作优化等；**V2 热点采集改造新增 `POST /api/hotspots/refresh` 与 `GET /api/hotspots/status`**；**P2-7 新增 `POST /api/hotspots/events`**；**P2-8 新增 `GET /api/topics`**（此前选题只有「生成」没有「读回」，页面刷新即空，库里历史选题无任何入口）；**P2-9 新增 `GET /api/copy/{task_id}`**（此前创作任务也只能写不能读，刷新丢 taskId 后适配页直接回空态）。截至当前代码实装共 **21 路由 / 22 HTTP 方法**（含 `/api/health` 探针），**20 个功能端点**（persona 含 GET+POST 计为 1 端点 2 方法）。`api/main.py` 注释已同步更新。

---

## 五、API 详细参考（请求/响应字段，取自实际实现）

### 5.1 认证 F6
- **POST `/api/auth/register`**  
  请求：`{username:str, password:str, email?:str}`  
  响应：`{ok, access_token, token_type:"bearer", user_id}`
- **POST `/api/auth/login`**  
  请求：`{username:str, password:str}`  
  响应：`{ok, access_token, token_type:"bearer", user_id}`（失败：`{ok:false, msg}`）
- 其余端点均需在 Header 带 `Authorization: Bearer <token>`。

### 5.2 热点 F1（V2 三层模型）

- **GET `/api/hotspots`**（公开可读；带 token 时按账号领域个性化）
  参数：`view=feed|all`（默认 `feed`）、`domain`、`platform`（热榜来源码 `weibo/zhihu/...`）、`new_only`、`limit`（默认 20，上限 100）、`include_misc`（是否含「综合」条目：**feed 默认 false / all 默认 true**）  
  响应：`{ok, count, view, personalized, include_misc, freshness:{updated_at,age_minutes,degraded,sources_ok,sources_total}, hotspots:[{id,signal_id,title,summary,source,platform,url,domain,heat,norm_heat,source_count,best_rank,trend,first_seen_at,is_new,suggestion,adopted,collected_at, relevance?, reason?}]}`  
  - `relevance` / `reason` **仅 `view=feed` 且已登录**时返回；`view=all` 不返回，避免误以为全榜也做过个性化。
  - `adopted`（P2-7 新增，布尔）：**当前账号是否已用这条出过选题**（来自 `hot_feed_event`）。未登录恒为 `false`，字段始终存在（前端不写分支）。
  - **展示口径（V2.1 二期起）**：前端展示 `norm_heat`（0-100，跨源可比）+ `best_rank`（该话题在最优来源榜内的名次）；`heat` 仅作兼容保留，跨源不可比，不再参与展示与排序。
  - `suggestion`（切入建议角度，≤40 字）由后端**惰性生成**：仅对本次要返回、且尚无建议的条目补齐，单次最多 `HOTSPOT_SUGGESTION_MAX_PER_REQUEST` 条（默认 5，一次批量 LLM 调用），并回写 `hotspots.suggestion`。`HOTSPOT_SUGGESTION_ENABLED=false` 时该字段恒为空，前端不展示该行。
  - **推荐流默认剔除 `domain='综合'`**：真实热榜实测 105 条里 78 条是时政/体育/娱乐/社会新闻（5 个垂类命不中），对创作者不可用且时政内容有合规风险。要看全量用 `view=all` 或 `include_misc=true`；若剔除后一条不剩，会自动放开一次以保证不空屏（响应里 `include_misc` 会如实回显最终取值）。
  - **命中过少不"凑数"**：剔除「综合」后可能只剩 1–2 条（2026-10-26 实测某天 93 条在榜仅 1 条命中垂类）。后端**不会**用「综合」补足（那等于把刚过滤掉的时政/体育换个位置塞回来），改由前端提示条说明并给「全部」入口，由用户决定。
  - 全源不可达时仍返回库内数据，并以 `freshness.degraded=true` 标注（空屏率 0）。
- **POST `/api/hotspots/refresh`**（**需鉴权**）  
  请求：`{sources?:[str], force?:bool}`  
  - `sources` 超过 `HOTSPOT_MAX_SOURCES_PER_REFRESH` → 400；未知来源 → 400
  - 距上次成功采集 < `HOTSPOT_MIN_REFRESH_INTERVAL` 且 `force=false` → 429，`msg` 内直接给出剩余秒数（如「刷新过于频繁，请 56 秒后再试」），同时保留 `retry_after_seconds`
  - 已有批次在跑 → 复用该批次并返回 `reused=true`，不新开批次  
  响应：`{ok, batch_id, reused, collected, by_source, failed, skipped, degraded, elapsed_ms}`
- **GET `/api/hotspots/status`**（**需鉴权**）  
  响应：`{ok, freshness, sources:[{source_code,last_ok_at,last_error,fail_streak,backoff_until,total_fetched}], window_days:7, total_runs, success_rate, p95_elapsed_ms, adoption:{...}}`  
  成功率的唯一数据来源是 `hot_collect_log`，不是估算值。  
  `adoption`（P2-7 新增，近 7 天采纳漏斗）：`{window_days, impressions, adopts, generates, ctr, adopt_to_generate, active_users, top_adopted:[{hotspot_id,signal_id,title,domain,adopts}], note}`  
  - `ctr = adopts / impressions`，**无曝光数据时返回 `null` 而不是 0**（0 会被误读成"没人点"）；`adopt_to_generate = generates / adopts`。
  - 两个比率必须一起看：只盯 `ctr` 会把推荐调成标题党；`adopt_to_generate` 偏低说明"点了却写不出来"，问题在选题质量。
  - 采集健康（数据新不新）与采纳漏斗（推荐准不准）放同一入口，因为源全挂时 `ctr` 暴跌并不代表推荐变差。
- **POST `/api/hotspots/events`**（**需鉴权**，P2-7 新增）  
  请求：`{action:"expose"|"adopt", surface?:"feed"|"all"|"platform", count?, hotspot_id?, position?}`  
  - `expose`：前端**真正渲染出一屏**时上报一次，`count` = 本屏条数（上限 100，`0` 直接不记）→ **分母**。
  - `adopt`：点「用它做选题」时上报，`hotspot_id` 必填（不存在 → 404），`position` = 点击时名次（用于看位置偏差）→ **分子**。
  - `generate`（第三级·强信号）**不接受客户端上报**（传了返回 400）：由 `/topics/generate` 带 `signal_id` 成功后在服务端写入，客户端伪造不了。
  - 短窗去重：同一用户对同一热点的 `adopt` / `generate` 在 `HOTSPOT_ADOPT_DEDUPE_SECONDS`（默认 30s）内只记一次，重复请求返回 `{recorded:0, deduped:true}` 且仍是 200（前端不该因此弹错误）。
  - **曝光为什么不由 `GET /hotspots` 计数**：GET 是只读接口，一次请求不代表真的渲染（前端补建议角度会再取一轮、Mock 也会重取），按请求计数等于给分母灌水。
  响应：`{ok, action, recorded, deduped?}`

### 5.3 选题 F2
- **GET `/api/topics?status=0&q=折叠屏&limit=12&offset=0`**（P2-8 新增，需鉴权）  
  历史选题列表。改造前 F2 只有「生成」没有「读回」：`POST /topics/generate` 的响应只存在页面内存里，**刷新即空**，库里累积的历史选题没有任何入口。
  - `status`：`0` 待选用 / `1` 已选用；不传 = 全部（非法值忽略，不报错）
  - `q`：`title` 或 `angle` 模糊匹配（与素材列表同口径）
  - `limit` 默认 12、上限 100；`offset` 越界返回空数组
  - 排序：`created_at DESC, id ASC`。**同一批生成的多条共用一个 `created_at`（秒级）**，用 `id ASC` 保证批内顺序与生成响应一致
  - `created_at` 已用 `datetime(created_at,'localtime')` 转成本地时间——库里 `DEFAULT (datetime('now'))` 存的是 **UTC**，直接回给前端会早 8 小时（刚生成的选题显示成"8 小时前"）
  响应：`{ok, total, limit, offset, results:[{id, hotspot_id, title, angle, reason, target_audience, status, created_at, hotspot_domain, hotspot_signal_id, copy_task_id}]}`  
  > `copy_task_id` = 该选题已产出文案的最近一次任务（`MAX(copy_tasks.id)`）；`hotspot_*` 来自 `LEFT JOIN hotspots`，非热点出题为 `null`。
- **POST `/api/topics/generate`**  
  请求：`{domain?:str, count:int=3, signal_id?:int}`  
  - `signal_id`（V2.1 二期新增，**可选**）：热点看板「用它做选题」直传。传了就以该热点为主输入——标题进 prompt（并要求至少 1 条选题围绕它）、其**主领域覆盖** `domain`、且 `topics.hotspot_id` 落地外键。
  - 兼容：也接受 `hotspots.id`（前端传错一种 id 也不至于整条链路断掉）；热点不存在/已删除 → 404。
  - **不传时行为与改造前完全一致**（向后兼容，`_e2e_test.py` 的 T-13 守着这条）。
  响应：`{ok, domain, signal_id, topics:[{id,title,angle,reason,target_audience, domain?}]}`（落库 topics）
- **POST `/api/topics/{topic_id}/select`**  
  响应：`{ok, topic:{id,title,angle,reason,target_audience}}`（置 status=1）

### 5.4 素材 F1
- **GET `/api/materials?q=科技&limit=50`**  
  带 `q`：`{ok, query, results:[{id,title,domains,platforms,styles,chunk_count,created_at}], mode:"search"}`（语义 Top-5 + LIKE 兜底）  
  不带 `q`：`{ok, results:[{id,title,domains,platforms,styles,chunk_count,created_at}], mode:"list"}`  
  > 另：M4 为素材库「分块预览」新增只读端点 **`GET /api/materials/{id}/chunks`** → `{ok, material_id, chunks:[{index, content(解密), char_count}]}`（校验归属，见 M4 交付说明第五节）。
- **POST `/api/materials/import`**  
  请求：`{title:str, content:str, source_type?:str="text", platforms?:str, domains?:str, styles?:str, custom_tags?:str}`  
  响应：`{ok, material_id, chunk_count}`（按句边界 ≤300 字分块，自动加密入库）
- **POST `/api/materials/{id}/tag`**  
  请求：`{platforms?, domains?, styles?, custom_tags?}`（仅更新传入字段）  
  响应：`{ok, material_id}`
- **POST `/api/materials/{id}/embed`**  
  响应：`{ok, rebuilt_vectors}`（重建全量 FAISS 索引，保证一致性）

### 5.5 文案生成 / 优化 / 适配（F3/F4/F5）
- **POST `/api/copy/generate`**（SSE `text/event-stream`）  
  请求：`{topic_id?:int, topic_title:str, target_platforms?:[str], platform?:str, audience?, intent?, tone?, length?, persona_voice?, n_versions?:int}`  
  事件流见第六节。
- **POST `/api/copy/{task_id}/optimize`**  
  请求：`{tone?, length?, persona_voice?}`  
  响应：`{ok, task_id, versions:[{version_no,title,body,style_consistency,replacement_suggestions}]}`
- **POST `/api/copy/{task_id}/adapt`**  
  请求：`{platform:str}`  
  响应：`{ok, task_id, platform, versions:[{version_no,id,platform,title,body,tags,layout_style,export_format,truncated,note}]}`（`note` 为平台发布备注，M4 新增）  
  说明：若该平台尚无适配行，自动从已有版本补建（支持生成后追加新平台分发）。

- **GET `/api/copy/{task_id}`**（P2-9 新增，**需鉴权**）  
  响应：`{ok, task:{id,status,topic_id,topic_title,target_platforms,target_audience,creation_intent,tone,length,persona_voice,created_at}, platforms:[str], versions:[{version_no,title,body,tags,style_consistency,replacement_suggestions,adapted}], by_platform:{平台码:[{id,version_no,title,body,tags,style_consistency,replacement_suggestions,layout_style,export_format,truncated,note}]}}`  
  说明：**只读不重算**——不调优化、不调适配，避免"刷新一次白烧一次 LLM"。`versions` 按 `target_platforms[0]` 的正文聚合（供生成页恢复），`by_platform` 供适配页直接复用已落库的适配结果。`layout_style`/`export_format`/`truncated`/`note` 因表中无对应列，按 `platform_rules` 现算以保持与 `adapt` 响应同构。任务不存在或不属于当前用户 → **404**。`created_at` 已转本地时间。

### 5.6 人设 F6
- **GET `/api/persona`** → `{ok, persona:{display_name,positioning,tone,domains}}`
- **POST `/api/persona`**  
  请求：`{positioning?, tone?, domains?, display_name?}` → `{ok, persona:{...}}`

### 5.7 导出 F5
- **POST `/api/export`**  
  请求：`{task_id:int, platform?:str, format:"txt"|"docx"}`  
  响应：`附件`（txt=纯文本，含「===== 版本N（平台）=====」标记；docx=标准库 zipfile 生成的最小合规文档）

### 5.8 运维
- **GET `/api/health`** → `{ok, service:"M3 backend", llm_backend, embedding_backend, counts:{users,materials,material_chunks,hotspots,topics,copy_tasks,copy_versions,platform_rules}}`

> 交互式文档：`启动后访问 http://127.0.0.1:8000/docs`（FastAPI Swagger UI）与 `/redoc`。

---

## 六、SSE 事件协议（`/api/copy/generate`）

```
event: start    {type:"start", task_id}
event: retrieved{type:"retrieved", chunk_count, snippets:[前3条≤60字]}
event: version  {type:"version", version_no, title, optimized_body, tags:[...],
                 adapted:{平台:标题}, style_consistency, replacement_suggestions}
   …（≥3 个 version 事件）…
event: done     {type:"done", task_id, versions_count, generation_seconds, target_platforms}
event: error    {type:"error", task_id, msg}   （异常时；任务状态置 failed）
```
每次生成默认 **3 个版本**（可由 `n_versions` 调整），并对每个目标平台各存一行适配结果（`copy_versions` 唯一约束为 `(task,version,platform)`）。

---

## 七、验收对照表（计划书 5.2.3 验收标准）

| 验收项（计划书） | 阈值 | M3 实测 | 结论 |
|---|---|---|---|
| 单篇生成耗时 | ≤15s | 离线 Mock：**0.01s**（SSE done.generation_seconds） | ✅ 远超阈值 |
| API 自测通过 | 全绿 | `_e2e_test.py` 全 11 步通过（含 SSE ≥3 版本、优化、适配、导出、检索、人设、导入） | ✅ |
| 素材检索 Top-5 命中率 | ≥90% | **BGE + 重排：100%（108/108）**；BGE 单向量 93.5%；离线哈希 + 重排 97.2% | ✅ **达标** |
| 平台规范适配符合率 | ≥90% | **硬性格式约束 100%（36/36，eval_adapt）**；语义调性层需真实 LLM+人工 | ✅（格式层）/ ⚠️（语义层） |
| 一次可用率 | ≥80% | 离线 Mock 为模板化文案，不主张该指标；需接入真实 LLM 后以「人工/模型评分」度量（路径见下） | ⏳ 待 LLM |

### 7.1 Top-5 命中率：2×2 消融矩阵（实测）

补齐 4.1.3 要求但首版缺失的「重排」环节后，命中率显著提升。在同一评测集（全量 108 条，即 V1.1.0 版；注：2026-10-12 起领域词表扩为 5 个、评测集升级为 180 条 V1.2.0，最新数据可重跑 `api/eval_retrieval.py`）上对「嵌入后端 × 是否重排」做完整消融：

| 嵌入后端 | 纯向量 Top-5（无重排） | 粗排20→重排→Top-5 | 重排增益 |
|---|---|---|---|
| 离线哈希（`offline`） | 70.4% | 97.2% | **+26.8pp** |
| **BGE 语义（`bge`，dim=1024）** | 93.5% | **100.0%** | **+6.5pp** |

> **2026-10-26 复测（V1.2.0 评测集 180 条 / 当前库）**：先发现评测集 gold 素材 id 与重建后的库完全对不上（152–241 vs 2–91），命中率恒为 **0%**——这是评测集漂移的假故障，不是检索坏了；`data/build_eval_set.py` 重建后 offline + 重排 = **180/180 = 100%**。`api/eval_retrieval.py` 已内置漂移自检（gold 在库中不存在的任务数 > 0 即告警）。
>
> **BGE 是否默认开：暂不**。本机 `sentence-transformers` 已装、`BAAI/bge-large-zh` 权重已在 HF 缓存，实测加载 27.9s、编码 270 块 59.2s（CPU）、dim=1024，但对同一批查询的 Top-5 关键指标（查询二元组覆盖率 / 同领域占比 / 重排分）**与 offline 完全一致**（真实选题标题 kw 均 ≈0.00–0.04，评测集查询 kw 均 0.28）。原因是**语料侧没有可检索的语义**：90 篇语料是「大模型 / 算力 / 端侧推理」这类模板句，与真实热点标题二元组重合≈0。结论：检索相关性的瓶颈在语料质量，不在向量后端；要提升请换真实往期文案语料。另：加载时若无 `HF_HUB_OFFLINE=1` 会被出站代理打成 502，`EmbeddingService` 已加「BGE 失败自动回落 offline」保护。

**结论：**
1. **BGE 已实测跑通**（`sentence-transformers` 6.1.0 + `BAAI/bge-large-zh`，dim=1024，加载 22.6s，索引 168 向量重建 23.9s），T3.1「BGE 向量化」不再是未验证代码分支。
2. **BGE + 重排 = 100% 命中**，超过 ≥90% 阈值；分领域均为 100%（科技 36/36、生活 36/36、财经 36/36）。
3. **重排在两种嵌入下都是正增益**，印证 4.5「FAISS 相似检索 + 重排提升 Top-5 命中率」的设计判断 —— 双塔向量粗排召回率高（Top-20 召回 100%）但排序精度不足，重排负责把正确素材排进 Top-5。在语义已较强的 BGE 上增益收窄至 +6.5pp，说明两者是互补而非替代关系。

复现命令：
```bash
# BGE + 重排（推荐配置）
EMBEDDING_BACKEND=bge python -m api.eval_retrieval --split all
# 消融：关闭重排
EMBEDDING_BACKEND=bge python -m api.eval_retrieval --split all --no-rerank
# 离线哈希对照
python -m api.eval_retrieval --split all
```

> ⚠️ **切换嵌入后端必须重建 FAISS 索引**：BGE dim=1024 与 offline dim=256 不同。`VectorStore.load()` 已内置「维度或后端变化即重建」的保护（比对 `faiss_meta.json` 的 `dim`/`backend`），直接删除 `db/faiss.index` 与 `db/faiss_meta.json` 亦可。

### 7.2 BGE 落地实测记录（T3.1 验证）

| 项 | 实测值 |
|---|---|
| 依赖 | `sentence-transformers` 6.1.0 + `torch` 2.14.1 + `transformers` 5.19.0 |
| 模型 | `BAAI/bge-large-zh`，向量维度 **1024** |
| 权重获取 | 经 `HF_ENDPOINT=https://hf-mirror.com` 镜像，`model.safetensors` 约 1.3GB |
| 加载耗时 | 22.6s（首次进程内加载，含权重读盘） |
| 索引重建 | 168 向量 / 23.9s |
| 语义验证 | 「端侧大模型推理加速」与「美联储加息预期」余弦相似度 0.7263，符合跨领域相近、非同域的直觉 |

**镜像下载踩坑记录（供后续复现参考）：** 首次尝试时 `huggingface_hub` 对 `config.json` / `modules.json` / `config_sentence_transformers.json` 等小文件产生 **0 字节空文件**（SSL 握手超时中断），导致后续 `SentenceTransformer` 加载报 `JSONDecodeError: Expecting value: line 1 column 1` 而非网络错误码，排查成本较高。可靠做法：
1. 设 `HF_HUB_DOWNLOAD_TIMEOUT=120`（默认超时过短）；
2. 小 JSON 文件用 `requests` 逐个显式下载并校验 `len(content) > 0`；
3. 大权重文件用 `Range` 请求**断点续传**（约 27 分钟完成 1.3GB）。

### 7.3 关于一次可用率（≥80%）

该指标衡量「生成文案无需大改即可发布」的比例，需以真实 LLM 产出 + 人工/模型评分。M3 已搭好评测通路（评测集 `eval_set.json` 180 条、8:1:1 划分、维度覆盖 3 平台 × 5 领域 × 4 意图 × 3 风格），M5 接入 LLM 后落地评分脚本即可。

---

## 八、运行指南

### 8.1 环境
```bash
cd <项目根>
PY=python   # 建议使用隔离 venv；依赖见 api/requirements.txt
$PY -m pip install -r api/requirements.txt
```

### 8.2 初始化数据库（仅需一次 / 重置时）
```bash
$PY db/init_db.py --reset     # 建 13 表 + platform_rules 种子
$PY data/clean_chunk.py       # 导入语料 → 90 素材 / 270 分块 + 10 热点 + demo_seed 账号（5 领域）
$PY data/build_eval_set.py    # 构建评测集（对齐当前库）
```

### 8.3 启动后端（默认离线模式）
```bash
$PY -m uvicorn api.main:app --host 127.0.0.1 --port 8000
# 启动后自动：构建/加载 FAISS + 启动热点采集调度
# 浏览器打开 http://127.0.0.1:8000/docs 查看交互式 API
```

### 8.4 切换生产链路（可选）
| 能力 | 环境变量 | 取值 |
|---|---|---|
| 向量嵌入 | `EMBEDDING_BACKEND` | `offline`（默认）｜ `bge` |
| 嵌入模型 | `EMBEDDING_MODEL` | 默认 `BAAI/bge-large-zh` |
| BGE 镜像 | `HF_ENDPOINT` | 默认 `https://hf-mirror.com` |
| LLM | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 留空→Mock；填入→OpenAI 兼容接口 |
| JWT | `JWT_SECRET` | 生产务必改为强随机 ≥32 字节 |
| 热点调度 | `HOTSPOT_SCHED_ENABLED` | 默认 `true`（旧键 `HOTSPOT_ENABLED` 已废弃） |
| 热点来源 | `HOT_SOURCES_ENABLED` | 默认 `weibo,zhihu,baidu,douyin,bilibili,toutiao`（热榜来源码，非发布平台码） |
| 热点实例 | `HOTSPOT_API_BASE` | 自托管 DailyHot 地址；留空用内置免费源 |
| 热点节奏 | 每源间隔在 `api/core/hot_sources.py` 的 `SOURCES` 注册表 | 10–20 分钟/源（旧键 `HOTSPOT_INTERVAL_MINUTES` 已废弃） |
| 退榜/新颖度 | `HOTSPOT_SIGNAL_TTL_HOURS` / `HOTSPOT_NEW_WINDOW_HOURS` | 24 / 24 |
| 保留策略 | `HOTSPOT_RAW_RETENTION_DAYS` / `HOTSPOT_LOG_RETENTION_DAYS` | 7 / 30 天 |
| 推荐权重 | `HOTSPOT_FEED_WEIGHTS` | `0.30,0.25,0.20,0.15,0.10`（和须为 1.0，非法则告警回落默认） |
| 切入建议 | `HOTSPOT_SUGGESTION_ENABLED` / `HOTSPOT_SUGGESTION_MAX_PER_REQUEST` | `true` / `5`（单次请求最多惰性生成几条，批量 1 次 LLM 调用） |
| 采纳埋点 | `HOTSPOT_ADOPT_DEDUPE_SECONDS` / `HOTSPOT_EVENT_RETENTION_DAYS` | `30`（同热点重复采纳/出题的合并窗口）/ `90`（事件表保留天数） |

### 8.5 自测与评测命令
```bash
$PY api/_demo.py                            # 9 环节连贯演示（需后端在跑）
$PY api/_e2e_test.py                        # 端到端冒烟 11 步（需后端在跑）
$PY -m api.eval_retrieval --split all        # 检索 Top-5 命中率（两阶段检索，默认）
$PY -m api.eval_retrieval --split all --no-rerank   # 消融：关闭重排对照
$PY -m api.eval_adapt --samples 4           # 平台适配硬性规范符合率
```

> 三个脚本均支持 `DEMO_BASE` 环境变量指定后端地址（默认 `http://127.0.0.1:8000`），便于换端口验证：
> `DEMO_BASE=http://127.0.0.1:8011 $PY api/_demo.py`

### 8.6 演示账号
- 用户名 `demo_seed` ｜ 口令 `Demo@2026`（由 `data/clean_chunk.py` 播种，仅演示，生产请改）

---

## 九、已知限制与风险（如实）

1. **默认仍是离线 Embedding**：本交付以 `EMBEDDING_BACKEND=offline` 为默认启动（启动快、无 1.3GB 依赖），Top-5 可达 97.2%；**BGE 语义后端已实测可用（100%）**，正式部署建议设 `EMBEDDING_BACKEND=bge`。BGE 进程内加载约 22.6s、索引重建约 23.9s，批量导入素材时需预留。
2. **Mock LLM 非真实文案**：离线模式生成的是结构化模板文案，用于打通链路与演示，不代表真实生成质量；真实文案需配置 `LLM_API_KEY`。
3. **热点采集已接真实源（5/6 源实测可用）**：适配器链为 `dailyhot → official → vvhan → tenapi`。其中 `official` 直连**平台官方接口**（百度 `top.baidu.com/api/board`、B站 `search/square`、抖音 `hot/search/list`、头条 `hot-event/hot-board`、微博 `ajax/side/hotSearch`），无需部署、最实时；`vvhan`/`tenapi` 仅作兜底（限频、结构变更、间歇 502）。实测一次采集 5 源 100 条、1.9s、`degraded=false`。
3.1 **知乎仍无官方适配**：`zhihu.com` 的 API 需登录态签名，暂只能走聚合源（部分网络环境不可达）→ 该源会失败并进入退避，不影响其他源。
3.1 **热点领域判定基于关键词表**（`api/core/domain_keywords.py`）：未命中任何领域关键词的条目落「综合」，且不进入 5 个官方领域的筛选结果。词表覆盖不全时会表现为"某领域热点偏少"，调词表即可，无需改代码。
4. **BGE 权重体积大**：`EMBEDDING_BACKEND=bge` 首次需下载约 1.3GB 权重（经 `HF_ENDPOINT` 镜像，本机实测约 27 分钟），且国内镜像对小文件偶发 0 字节空文件（详见 7.2 踩坑记录）。建议在网络稳定时段预热。cross-encoder 重排（`RERANK_STRATEGY=cross`）另需 `BAAI/bge-reranker-large`。
5. **计划书口径待统一**：4.1.3 将「重排」列为链路必备环节，4.5 则表述为「可选重排」。本实现按**必备**处理（默认开启 lexical 重排），并提供 `--no-rerank` 做消融，建议评审时明确以哪一处为准。
6. **数据库累积测试数据**：反复运行 `_e2e_test.py` 会追加测试素材/任务；如需干净库，按 8.2 重置即可。

---

## 十、下一步（M4 / M5）

- **M4 前端与集成**：React + Tailwind/AntD 五大页面（素材库、热点看板、选题列表、生成对比编辑、平台适配预览），对接本 M3 全部端点；CORS 已默认放行 `*`（生产按域名收紧，见 `config.CORS_ORIGINS`）。
- **M5 测试优化**：接入真实 LLM/BGE 后，落地「一次可用率/平台适配符合率」人工-模型评分脚本；热点采集对接真实源；压测与合规标注（AI 生成内容提示）固化。

---

## 附录 A：修复记录（本交付内）
- **新增 `api/core/rerank.py`**：补齐 4.1.3 要求但首版缺失的「重排」环节。Top-5 命中率 70.4% → **97.2%**（全量 108 条消融对比见 7.1；该数字为 V1.1.0 版 108 条评测集上的实测，2026-10-12 起评测集已扩为 180 条 V1.2.0）。
- **`api/core/llm.py: _mock_optimize`**：改为幂等（先剥离既有前后缀再重新包裹）。此前 `generate()` 已加 `【风格优化后】` 前缀，`/optimize` 端点又叠加一次，导致前缀与后缀各重复两次。
- **`api/core/rag_pipeline.py`**：改用 LangChain LCEL（`PromptTemplate | StrOutputParser`）编排提示词组装与生成，新增 `_parse_versions` 解析 `=====版本N=====` 分隔格式，落实 T3.3「LangChain 编排」；`retrieve()` 改为两阶段检索（粗排 → 重排）。
- **`api/eval_retrieval.py`**：升级为两阶段检索口径，支持 `--no-rerank` 消融，默认全量（初版默认仅 test 12 条，样本量偏小；当前评测集全量 180 条）。
- `db/schema.sql`：`platform_rules.hashtag_format` 种子由字面示例 `#话题#` 修正为模板 `#{}#`，修正在线/离线均会触发的标签双重包裹 bug（`#话题#科技` → `#科技#`）。校验：`eval_adapt.py` 符合率由 0% → 100%。
- `db/schema.sql`：`copy_versions` 唯一约束由 `(copy_task_id, version_no)` 改为 `(copy_task_id, version_no, platform)`，以支持「每个版本按多平台各存一行」。
- `api/routers/export.py`：修正两处误用 `r["tags"]`（DB 行实际列名 `hashtags`）导致的导出 500。
- `api/core/vector_store.py`：FAISS 落盘改用 `serialize_index/deserialize_index` 二进制 IO，规避原生 `write_index` 在含中文路径（`新建文件夹`）的 Windows 上 fopen 失败。
- M2 评测集 `eval_set.json`：因 M2→M3 期间库被重建，gold 素材 ID 已按当前库重对齐后重新生成。

## 附录 B：演示示例（生成→适配）
一次 `/api/copy/generate` 的某版本经 `/api/copy/{id}/adapt` 后：
- 微信：`标题≤64字 / 标签 #科技# #干货科普# / 导出 docx`
- 小红书：`标题≤20字 / 标签≤10 / 导出 txt`
- 抖音：`标题≤30字 / 正文≤200字 / 标签≤5 / 导出 txt`
（约束来自 `platform_rules` 表，新增平台只需插一行。）
