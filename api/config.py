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

# -------- 本地 .env 加载（离线演示用，免手动 export）--------
# 与 db/security.py 的 DB_FIELD_KEY 解析保持一致：仅当同名变量尚未在环境中
# 设置时，才从项目根 .env 注入 os.environ，绝不覆盖已显式导出的变量。
def _load_local_env_if_present() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_local_env_if_present()

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

# -------- 热点采集 V2（来源 / 调度 / 排序） --------
# ⚠️ 旧键 HOTSPOT_ENABLED / HOTSPOT_INTERVAL_MINUTES 已废弃：
#    - 前者重命名为 HOTSPOT_SCHED_ENABLED（语义完全重叠，保留会造成
#      "关了一个开关、调度还在跑"的排查地狱）
#    - 后者被"每源独立节奏"取代（间隔移入 api/core/hot_sources.py 的 SOURCES 注册表）
#    全库不应再有对这两个旧键的引用。
HOT_SOURCES_ENABLED = [
    s.strip() for s in os.environ.get(
        "HOT_SOURCES_ENABLED", "weibo,zhihu,baidu,douyin,bilibili,toutiao"
    ).split(",") if s.strip()
]
HOTSPOT_API_BASE = os.environ.get("HOTSPOT_API_BASE", "")   # 自托管 DailyHot 实例；空则用内置免费源
HOTSPOT_TIMEOUT = int(os.environ.get("HOTSPOT_TIMEOUT", "8"))  # 单源超时(s)
HOTSPOT_SCHED_ENABLED = os.environ.get("HOTSPOT_SCHED_ENABLED", "true").lower() == "true"
HOTSPOT_MIN_REFRESH_INTERVAL = int(os.environ.get("HOTSPOT_MIN_REFRESH_INTERVAL", "60"))  # 手动刷新最小间隔(s)
HOTSPOT_SIGNAL_TTL_HOURS = int(os.environ.get("HOTSPOT_SIGNAL_TTL_HOURS", "24"))      # 退榜阈值(h)
HOTSPOT_NEW_WINDOW_HOURS = int(os.environ.get("HOTSPOT_NEW_WINDOW_HOURS", "24"))      # 新颖度窗口(h)
HOTSPOT_SOURCE_STALE_MINUTES = int(os.environ.get("HOTSPOT_SOURCE_STALE_MINUTES", "180"))  # 源快照过期(分)
HOTSPOT_SUGGESTION_ENABLED = os.environ.get("HOTSPOT_SUGGESTION_ENABLED", "true").lower() == "true"
# 单次请求最多为几条热点惰性生成建议（批量 1 次 LLM 调用，控制首屏延迟与成本）
HOTSPOT_SUGGESTION_MAX_PER_REQUEST = int(
    os.environ.get("HOTSPOT_SUGGESTION_MAX_PER_REQUEST", "5"))
HOTSPOT_MAX_SOURCES_PER_REFRESH = int(os.environ.get("HOTSPOT_MAX_SOURCES_PER_REFRESH", "6"))
HOTSPOT_RAW_RETENTION_DAYS = int(os.environ.get("HOTSPOT_RAW_RETENTION_DAYS", "7"))   # raw 层保留天数
HOTSPOT_LOG_RETENTION_DAYS = int(os.environ.get("HOTSPOT_LOG_RETENTION_DAYS", "30"))  # 采集日志保留天数
# 推荐流事件（曝光/采纳/出题）保留天数：比 raw 长得多（指标要看趋势），但也得设上限
HOTSPOT_EVENT_RETENTION_DAYS = int(os.environ.get("HOTSPOT_EVENT_RETENTION_DAYS", "90"))
# 同一条热点多少秒内的重复「采纳」只算一次（双击 / 点完返回再点，是同一个动作）
HOTSPOT_ADOPT_DEDUPE_SECONDS = int(os.environ.get("HOTSPOT_ADOPT_DEDUPE_SECONDS", "30"))
# 相关性权重：领域命中 / 新颖度 / 覆盖度 / 归一化热度 / 可创作性（和须为 1.0）
_HOTSPOT_FEED_WEIGHTS_RAW = os.environ.get("HOTSPOT_FEED_WEIGHTS", "0.30,0.25,0.20,0.15,0.10")


def _parse_feed_weights(raw: str) -> tuple[float, ...]:
    """解析 5 因子权重。非法输入只告警并回落默认，绝不抛异常导致服务起不来。"""
    default = (0.30, 0.25, 0.20, 0.15, 0.10)
    try:
        parts = tuple(float(x) for x in raw.split(",") if x.strip())
    except ValueError:
        print(f"  [配置告警] HOTSPOT_FEED_WEIGHTS 含非数字：{raw!r}，已回落默认值 {default}")
        return default
    if len(parts) != 5:
        print(f"  [配置告警] HOTSPOT_FEED_WEIGHTS 需 5 个数（领域/新颖/覆盖/热度/可创作），"
              f"实际 {len(parts)} 个，已回落默认值 {default}")
        return default
    if any(w < 0 for w in parts) or abs(sum(parts) - 1.0) > 1e-6:
        print(f"  [配置告警] HOTSPOT_FEED_WEIGHTS 须非负且和为 1.0，实际和={sum(parts):.3f}，"
              f"已回落默认值 {default}")
        return default
    return parts


HOTSPOT_FEED_WEIGHTS = _parse_feed_weights(_HOTSPOT_FEED_WEIGHTS_RAW)

# -------- CORS（本地开发放行，生产按域名收紧） --------
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
