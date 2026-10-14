# -*- coding: utf-8 -*-
"""
api/main.py — M3 后端入口（FastAPI 应用）

启动：uvicorn main:app --port 8000  （在 api/ 目录下）
生命周期：启动时构建/加载 FAISS 向量主存储 + 启动热点采集调度（计划书 4.6）。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS, HOTSPOT_ENABLED
from .core.embeddings import EmbeddingService
from .core.hotspot_collector import shutdown_scheduler, start_scheduler
from .core.llm import get_llm
from .core.vector_store import get_store
from .database import get_conn
from .routers import auth, copy, export, hotspots, materials, persona, topics


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("→ M3 后端启动中 ...")
    # 1) 向量主存储就绪（缺则构建 FAISS）
    try:
        n = get_store().ensure_ready(verbose=True)
        print(f"  [向量] FAISS 就绪：{n} 向量，backend={EmbeddingService.get().backend}")
    except Exception as e:  # noqa: BLE001
        print(f"  [向量] 构建跳过：{e}（请先运行 data/clean_chunk.py 入库素材）")
    # 2) 热点采集调度
    if HOTSPOT_ENABLED:
        start_scheduler()
    yield
    shutdown_scheduler()
    print("← M3 后端已关闭")


app = FastAPI(title="AI 自媒体内容创作助手 — M3 后端", version="1.0.0", lifespan=lifespan)

# CORS（本地开发放行；生产按域名收紧，计划书 2.2.3）
allow_all = CORS_ORIGINS == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False if allow_all else True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册（当前 16 路由：15 功能端点 + /api/health 探针；M4 扩充后口径，详见 M3 交付说明第四节）
app.include_router(auth.router, prefix="/api")
app.include_router(materials.router, prefix="/api")
app.include_router(hotspots.router, prefix="/api")
app.include_router(topics.router, prefix="/api")
app.include_router(copy.router, prefix="/api")
app.include_router(persona.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.get("/api/health")
def health():
    conn = get_conn()
    counts = {}
    for t in ["users", "materials", "material_chunks", "hotspots", "topics",
              "copy_tasks", "copy_versions", "platform_rules"]:
        try:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            counts[t] = -1
    conn.close()
    return {
        "ok": True,
        "service": "M3 backend",
        "llm_backend": get_llm().backend,
        "embedding_backend": EmbeddingService.get().backend,
        "counts": counts,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
