# M2 数据与评测基础 — 运行说明

M2 交付物：语料清洗与标注代码、测试素材库、评测方案。

## 目录
- `corpus/` — 合成示例语料（`{platform}_{domain}_{style}_{seq}.txt`，标注「非真实抓取」）
- `_gen_corpus.py` — 示例语料生成器（替换 `corpus/` 为真实授权源即可）
- `clean_chunk.py` — 清洗/段落语义分块/标注入库 → `../db/app.db`
- `build_eval_set.py` — 构建 8:1:1 评测集 → `eval_set.json`
- `chunk_spec.md` — 段落语义分块规范（T2.2）
- `评测方案.md` — 评测指标/口径/流程（T2.4）

## 运行（顺序）
```bash
# 1) 初始化数据库（M1 产物，含 9 表 + 平台规则种子）
cd ../db && python init_db.py --reset

# 2) 清洗分块并入库测试素材库
cd ../data && python clean_chunk.py --reset

# 3) 构建评测集
python build_eval_set.py
```

## 验证
- 平台覆盖 weixin/xhs/douyin，领域覆盖 科技/生活/财经/美妆/教育（5 领域 × 18 篇 = 90 素材 / 270 分块）
- 评测集 180 条（3 平台 × 5 领域 × 4 意图 × 3 风格，V1.2.0），8:1:1 划分，覆盖矩阵与风格分布均衡
- `db/app.db` 通过 `init_db.py --check` 完整性校验
- 敏感字段已加密落库（`materials.content` / `material_chunks.content` 为 `enc:` 密文）

## 安全说明（落实计划书 2.2.4 / 4.2）
- 加密依赖 `cryptography`：在隔离 venv 中执行 `pip install cryptography`。
- 字段密钥通过环境变量 `DB_FIELD_KEY`（Fernet 密钥）注入；未设置时回退项目根 `.env`（已含演示密钥，**请勿提交版本库**）。
- 生产部署务必设置真实 `DB_FIELD_KEY` 且不落盘，密钥不入库（计划书 2.2.4）。
- 种子账号口令为 `Demo@2026`（仅演示），生产请改。
