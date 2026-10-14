# -*- coding: utf-8 -*-
"""
api/config.py — M3 后端配置中心（落实计划书 2.2.5「配置项外置」）

所有外部依赖（模型地址 / 采集源 / 平台规则 / API Key）均通过环境变量注入，不入库。
缺省以「离线可运行」模式启动：无 LLM Key 走内置 Mock 生成器，无 BGE 权重走确定性
哈希向量，保证后端可在本环境端到端跑通并被评测；配置真实 Key/模型即切换生产链路。
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # api/.. = 项目根
DB_DIR = PROJECT_ROOT / "db"
DB_PATH = os.environ.get("DB_PATH", str(DB_DIR / "app.db"))
FAISS_INDEX_PATH = os.environ.get("FAISS_INDEX_PATH", str(DB_DIR / "faiss.index"))
FAISS_META_PATH = os.environ.get("FAISS_META_PATH", str(DB_DIR / "faiss_meta.json"))

# -------- LLM（OpenAI 兼容接口，国内直连） --------
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.8"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1400"))

# -------- 向量嵌入（BGE 真实 / 离线哈希兜底） --------
# offline: 确定性字符 n-gram 哈希向量，零下载即可跑通检索与 Top-5 评测
# bge:     sentence-transformers 加载 BAAI/bge-large-zh（经 HF_ENDPOINT 镜像）
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "offline")  # offline | bge
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-large-zh")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "256"))  # offline 默认维度
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")  # BGE 镜像
HF_HUB_DOWNLOAD_TIMEOUT = os.environ.get("HF_HUB_DOWNLOAD_TIMEOUT", "120")  # 大权重下载超时

# -------- 检索与重排（4.5「FAISS 相似检索 + 可选重排（cross-encoder）」） --------
# 召回深度：先粗排 RECALL_TOP_K 条，再重排取 RERANK_TOP_K 条进入提示词
# T5.3：20→40。实测离线 n-gram 向量下，同领域同风格素材可能排到粗排第 29 位
# （查询模板词稀释领域信号），20 条池会漏掉 gold；40 条仍为零成本（FAISS 暴力检索）。
RECALL_TOP_K = int(os.environ.get("RECALL_TOP_K", "40"))
RERANK_TOP_K = int(os.environ.get("RERANK_TOP_K", "5"))
# 检索域过滤：True 时优先只召回同领域素材（4.1.3「同领域范例」）
RETRIEVAL_DOMAIN_FILTER = os.environ.get("RETRIEVAL_DOMAIN_FILTER", "true").lower() == "true"
# 重排策略：lexical（字符重合度，默认，零依赖） | cross（cross-encoder，需权重） | off
RERANK_STRATEGY = os.environ.get("RERANK_STRATEGY", "lexical")  # lexical | cross | off
RERANK_CROSS_MODEL = os.environ.get("RERANK_CROSS_MODEL", "BAAI/bge-reranker-large")

# -------- JWT 鉴权（F6） --------
JWT_SECRET = os.environ.get("JWT_SECRET", "m3-dev-secret-please-change-32b+")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "72"))

# -------- 热点采集调度（T3.2 / 4.6） --------
HOTSPOT_ENABLED = os.environ.get("HOTSPOT_ENABLED", "true").lower() == "true"
HOTSPOT_INTERVAL_MINUTES = int(os.environ.get("HOTSPOT_INTERVAL_MINUTES", "60"))

# -------- CORS（本地开发放行，生产按域名收紧） --------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
