# AI 自媒体内容创作助手

> 面向自媒体创作者的智能内容生产平台：素材库 → 热点看板 → 选题策划 → 多平台文案生成 → 平台适配预览，覆盖「科技 / 生活 / 财经 / 美妆 / 教育」五大领域。

- 后端：Python + FastAPI + SQLite（SQLAlchemy 风格原生 SQL）+ JWT 鉴权 + 字段级加密（Fernet）
- 前端：React + TypeScript + Vite + Tailwind（公开站点）+ Ant Design（业务组件）
- 能力：BGE 向量检索（离线兜底为标题 LIKE）+ LLM 多版本文案生成（SSE 流式）+ 重排 + 平台差异化适配

---

## 一、目录结构

```
ai-media/
├── api/                  # FastAPI 后端（入口 api/main.py）
│   ├── main.py           # 应用装配、路由注册、生命周期
│   ├── config.py         # 配置项（CORS / JWT / 嵌入后端开关）
│   ├── core/             # 安全、嵌入、向量库、LLM、热点采集等核心模块
│   ├── routers/          # 路由：auth/materials/hotspots/topics/copy/persona/export
│   └── requirements.txt  # 后端依赖（核心必需 + 语义增强可选）
├── db/                   # 数据库层
│   ├── schema.sql        # 9 张表 + 索引 + 外键 + 平台规则种子
│   ├── init_db.py        # 建库 / 重建 / --check 自检
│   ├── security.py       # 字段级加密(Fernet) + 口令哈希(pbkdf2)
│   └── app.db            # 运行时数据库（被 .gitignore 排除，不入库）
├── data/                 # 语料与评测
│   ├── corpus/           # 90 篇语料（5 领域 × 18 篇）
│   ├── clean_chunk.py    # 清洗/分块/入库 + 播种演示账号与热点
│   ├── build_eval_set.py # 生成评测集
│   └── eval_set.json     # 180 条评测集（5 领域 × 36，3 风格 × 60）
├── frontend/             # React + Vite 前端
│   ├── src/              # 页面（素材/热点/选题/生成/适配/人设/登录）
│   ├── .env.example      # 前端运行配置模板
│   └── package.json
├── docs/                 # 文档（PRD / 架构 / 数据库设计 / 各阶段审查与测试报告）
├── 项目汇报/             # M5 项目汇报 PPT 与冒烟取证证据
├── prototype/            # 早期 HTML 原型
└── .env                  # 后端运行配置（DB_FIELD_KEY 等，被 .gitignore 排除）
```

> ⚠️ `db/app.db` 与根目录 `.env` **不入库**（见 `.gitignore`）。克隆后需按本文「数据初始化」与「配置」两步重建。

---

## 二、环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 推荐 3.13；需支持 `str \| None` 等语法 |
| Node.js | 18+ | 前端构建与 dev server |
| Git | 任意 | 拉取代码 |

操作系统：Windows / macOS / Linux 均可（以下命令以 Git Bash / 类 Unix shell 为例；PowerShell 把 `python` 换成 `python.exe`、路径用反斜杠即可）。

---

## 三、后端：本地复现步骤

### 1. 创建并激活虚拟环境

```bash
cd ai-media
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r api/requirements.txt
```

依赖分两层（详见 `api/requirements.txt` 注释）：

- **核心必需**（离线可运行）：`fastapi` `uvicorn` `faiss-cpu` `pydantic` `cryptography` `python-multipart` `apscheduler` `pyjwt`
- **语义增强可选**：`sentence-transformers` `langchain` `langchain-community`（及其 `torch`/`transformers` 链）。**离线复现可跳过**——默认 `EMBEDDING_BACKEND=offline`，检索走标题 LIKE 兜底，无需模型权重；装齐后切换为 BGE 向量语义检索（首次下载约 1.3GB）。

### 3. 配置（可选但推荐）

根目录 `.env` 用于注入密钥。**不创建也能跑**（见下方回退说明），但生产务必配置：

```bash
# 生成 Fernet 字段密钥（复制输出值）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

在项目根目录创建 `.env`：

```ini
# 字段级加密密钥（Fernet 32 字节 base64 urlsafe 编码）。一旦用于加密生产数据，更换后旧密文无法解密，初始化前定好并保持一致。
DB_FIELD_KEY=<上一步生成的密钥>

# JWT 签名密钥（不设置则使用代码内开发默认值，仅限本地演示）
JWT_SECRET=<任意足够长的随机串>
```

> **回退规则**（来自 `db/security.py` / `api/config.py`）：
> - `DB_FIELD_KEY` 未设置时自动读取根目录 `.env`；都没有则生成一次性内存密钥并告警，仅限离线演示（重启后历史密文不可解密）。
> - `JWT_SECRET` 未设置时使用硬编码开发默认值 `m3-dev-secret-please-change-32b+`，生产必须覆盖。

### 4. 初始化数据库

```bash
# 建 9 张表 + 索引 + 外键 + 平台规则种子（公众号/小红书/抖音）
python db/init_db.py --reset

# 校验 schema.sql 是否可建库（用临时库，不落盘）——可选
python db/init_db.py --check
```

### 5. 播种演示数据

```bash
# 入库 90 素材 / 270 分块 / 10 热点，并创建演示账号 demo_seed
python data/clean_chunk.py --reset
```

> 该脚本**不**清理 `topics` / `copy_*` 表；重复运行用 `--reset` 仅重建素材，生成类残留需手动 `DELETE`（见 `docs/产物审查报告.md` 中的 P0-2 处置记录）。

### 6. 启动后端

```bash
# 从项目根目录运行，确保根 .env 可被自动定位
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

启动后接口基址为 `http://127.0.0.1:8000`，健康检查 `GET /api/health`。

---

## 四、前端：本地复现步骤

```bash
cd frontend
npm install

# 复制配置模板（dev 默认经 Vite 代理 /api → 127.0.0.1:8000）
cp .env.example .env
```

`.env` 关键变量（见 `frontend/.env.example`）：

| 变量 | 取值 | 含义 |
|---|---|---|
| `VITE_API_BASE` | `/api` | API 请求基路径（dev 经代理转发） |
| `VITE_USE_MOCK` | `true` / `false` | `true`=前端 Mock 全链路离线演示；`false`=对接真实后端（上一步启动的 8000 服务） |

启动：

```bash
npm run dev
```

> ⚠️ **Vite 在本机仅监听 IPv6 `[::1]:5173`**，请用 **`http://localhost:5173`** 访问；直接用 `http://127.0.0.1:5173` 会被拒绝（10061）。
> 生产构建：`npm run build`（产物 `frontend/dist/`），配合任意静态服务器 + 反向代理 `/api` 至后端。

---

## 五、访问与演示账号

打开 **http://localhost:5173**，使用以下演示账号登录：

| 字段 | 值 |
|---|---|
| 用户名 | `demo_seed` |
| 口令 | `Demo@2026` |

> 演示账号由 `data/clean_chunk.py` 的 `SEED_USER_NAME` / `SEED_USER_PASSWORD` 播种。生产环境请禁用或强制改密（见 `docs/` 相关部署文档）。

功能链路：登录 → 素材库（可搜「美妆」「教育」等新领域）→ 热点看板 → 选题策划（一键生成）→ 文案生成（多版本 SSE）→ 平台适配 → 账号人设配置。

---

## 六、语义增强（可选，需联网下载模型）

默认离线即可跑通全链路。若需真实语义检索/重排：

```bash
pip install sentence-transformers langchain langchain-community
# 设置镜像以避免直连 HuggingFace 超时
export HF_ENDPOINT=https://hf-mirror.com
```

- `EMBEDDING_BACKEND=bge`：用 `BAAI/bge-large-zh` 做向量化（首次约 1.3GB）。
- `RERANK_STRATEGY=cross`：用 `BAAI/bge-reranker-large` 重排；加载失败自动降级为 `lexical`，不中断链路。
- 接入真实 LLM：设置 `LLM_API_KEY` 后，`copy/generate` 切换为真实生成（离线为 Mock 模板）。

评测：语义口径验收脚本（`data/eval_*.py`）在设置上述 Key 后可直接重跑，结论见 `docs/M5测试报告.md`。

---

## 七、常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 前端打不开，`127.0.0.1:5173` 连接被拒 | Vite 监听 IPv6，改用 `http://localhost:5173` |
| 启动后端报「未找到数据库」 | 漏跑 `python db/init_db.py --reset`，先建表再播种 |
| 已加密字段显示为乱码 / 解密失败 | 更换了 `DB_FIELD_KEY`；初始化前定好并保持一致 |
| 检索时「分块数」显示 0 | 走的是关键词检索分支；属历史缺陷已修复（见 `docs/产物审查报告.md` 3.8） |
| 提交 `topics`/`copy_*` 残留数据 | `clean_chunk.py` 只重建素材，生成类表需手动清理 |
| CORS 报错 | 默认 `CORS_ORIGINS=*`，必要时在 `api/config.py` 或环境变量指定前端源 |

---

## 八、相关文档

- `docs/PRD.md` — 产品需求文档
- `docs/架构设计.md` / `docs/数据库设计.md` — 技术方案
- `docs/M1-M4审查报告.md` / `docs/M5测试报告.md` / `docs/产物审查报告.md` — 各阶段审查与测试
- `docs/M5进度记录.md` — 任务进度与恢复指引
- `data/README.md` — 数据层与字段加密密钥说明
- `frontend/部署文档.md` — 前端部署说明

---

## 许可证

本项目仅用于学习与演示，未声明开源许可证，使用前请与作者确认。
