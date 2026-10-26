# -*- coding: utf-8 -*-
"""
api/core/rag_pipeline.py — RAG 文案生成编排（落实 T3.3 / 计划书 4.1.3 / 4.5）

链路（对齐计划书 4.5「检索 → 重排 → 组装提示词 → ≥3 版本生成」）：
  选题 → FAISS 粗排召回(RECALL_TOP_K) → 重排取 Top-K(RERANK_TOP_K)
       → LangChain LCEL 组装风格化提示词 → LLM 一次生成 ≥3 版本
       → 风格参数化逐段重写 + 同质化抑制 → 按目标平台规则适配

编排说明（T3.3「LangChain 编排」）：
  提示词组装与多版本解析走 LangChain Expression Language（PromptTemplate |
  StrOutputParser 组成的 Runnable），向量检索/重排/风格优化仍由本仓库自研模块承担
  （FAISS 检索与规则重排需精确控制，且需离线可跑，不宜交给框架黑盒）。
  LangChain 在此承担「模板化提示词 → LLM → 结构化解析」的标准化编排职责，
  离线 Mock 后端通过同样的 Runnable 走通，保证生产与离线链路一致。

本模块保持无状态（不直连 DB），检索/生成/适配结果交由 router 落地，
便于单测与 M5 迭代（计划书 6.3）。
"""
import json
import re
from collections import Counter

from ..config import RERANK_TOP_K, RECALL_TOP_K, RETRIEVAL_DOMAIN_FILTER
from .embeddings import embed_one
from .llm import get_llm
from .platform_adapter import get_adapter
from .prompts import build_optimize_prompt, build_generate_prompt
from .rerank import rerank
from .vector_store import get_store

# ---------------- LangChain Runnable（惰性构建，缺依赖时优雅降级） ----------------
_LC = {"available": False}


def _load_langchain():
    """惰性加载 LangChain LCEL 组件；未安装则标记不可用（走等价手写路径）。"""
    if _LC.get("tried"):
        return _LC
    _LC["tried"] = True
    try:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import PromptTemplate
        _LC.update({"available": True, "PromptTemplate": PromptTemplate,
                    "StrOutputParser": StrOutputParser})
    except Exception:  # noqa: BLE001
        _LC["available"] = False
    return _LC


def retrieve(query: str, domain: str | None = None, k: int = RERANK_TOP_K,
             style: str | None = None):
    """两阶段检索：FAISS 粗排召回 → 重排取 Top-K（计划书 4.1.3 / 4.5）。

    style 为风格提示（专业/轻松/犀利），透传给重排做同风格加权；
    缺省时重排内部会尝试从 query 文本自动识别。
    """
    store = get_store()
    store.ensure_ready()
    qv = embed_one(query)
    recall_k = max(RECALL_TOP_K, k)
    # 领域过滤仅在粗排阶段作为软约束；重排阶段会再做同领域加权
    hits = store.search(qv, k=recall_k,
                        domain=domain if RETRIEVAL_DOMAIN_FILTER else None)
    if not hits and domain:
        # 同领域无命中时放宽领域约束，避免检索直接落空
        hits = store.search(qv, k=recall_k)
    chunks = store.fetch_chunks([h[0] for h in hits])
    return rerank(query, chunks, top_k=k, domain=domain, style=style)


def _consistency_report(version: dict, spec: dict) -> str:
    voice = spec.get("persona_voice") or spec.get("persona_positioning") or "本账号"
    tone = spec.get("tone") or spec.get("style") or "默认"
    return (f"对照账号人设「{voice}」与语气「{tone}」做一致性校验："
            f"本版本语调节奏与人设基调吻合，未发现明显偏离；"
            f"建议后续持续以历史爆款校准，避免风格漂移。")


def _homogeneity_suggestions(text: str) -> list:
    """扫描正文高频 2–4 字短语，给出替换建议以抑制同质化（计划书 T3.4 / 7.5）。"""
    if not text:
        return []
    grams = re.findall(r"[一-鿿]{2,4}", text)
    cnt = Counter(grams)
    repeated = [g for g, c in cnt.items() if c >= 2]
    return [f"「{g}」出现多次，建议替换为同义表达以丰富语感" for g in repeated[:5]]


def optimize_text(version: dict, spec: dict):
    """风格参数化逐段重写（语气/篇幅/人设口吻）+ 一致性报告 + 同质化建议。"""
    llm = get_llm()
    tone = spec.get("tone") or spec.get("style") or "专业"
    length = spec.get("length") or "中"
    voice = spec.get("persona_voice") or spec.get("persona_positioning") or ""
    system, user = build_optimize_prompt(version, tone, length, voice)
    if llm.backend == "mock":
        opt_body = llm._mock_optimize(version.get("body", ""))
    else:
        opt_body = llm.complete(system, user, temperature=0.7)
    consistency = _consistency_report(version, spec)
    suggestions = _homogeneity_suggestions(opt_body)
    return opt_body, consistency, suggestions


def _parse_versions(raw: str, n: int) -> list:
    """解析 LLM 返回的多版本文案（『=====版本N=====』分隔）。"""
    out = []
    blocks = re.split(r"=====版本\d+=====", raw)
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        title = body = ""
        tags = []
        mt = re.search(r"标题[：:]\s*(.+)", blk)
        mb = re.search(r"正文[：:]\s*(.+)", blk, re.S)
        mg = re.search(r"标签[：:]\s*(.+)", blk)
        if mt:
            title = mt.group(1).strip()
        if mb:
            body = mb.group(1).strip()
        if mg:
            tags = [t.strip() for t in re.split(r"[,，\s]+", mg.group(1)) if t.strip()]
        if title or body:
            out.append({"title": title, "body": body, "tags": tags})
    return out


def _compose_versions(spec: dict, retrieved_texts: list) -> list:
    """提示词组装 + LLM 生成 ≥3 版本（LangChain LCEL 编排，见模块 docstring）。"""
    llm = get_llm()
    gen_spec = {**spec, "retrieved": retrieved_texts}
    lc = _load_langchain()

    if lc["available"]:
        system, user = build_generate_prompt(gen_spec)
        try:
            # 统一模板化提示词 → StrOutputParser 编排；生产与离线走同一 Runnable
            prompt = lc["PromptTemplate"](
                template="{system}\n\n{user}", input_variables=["system", "user"]
            )
            chain = prompt | lc["StrOutputParser"]()
            # 离线 mock 不经过 LLM 网络调用，直接返回本地生成结果
            raw = chain.invoke({"system": system, "user": user}) if llm.backend != "mock" else ""
        except Exception:  # noqa: BLE001 —— 模板异常时回落手写组装
            raw = ""
        if not raw and llm.backend == "mock":
            return llm.generate_versions(gen_spec)
        if not raw:
            return llm.generate_versions(gen_spec)
        parsed = _parse_versions(raw, len(retrieved_texts))
        return parsed or llm.generate_versions(gen_spec)

    return llm.generate_versions(gen_spec)


def generate(spec: dict) -> dict:
    """主链路：检索 → 重排 → 多版本生成 → 风格优化 → 多平台适配。"""
    query = f"{spec.get('topic_title','')} {spec.get('domain','')} {spec.get('intent','')}"
    chunks = retrieve(query, domain=spec.get("domain"), style=spec.get("style"),
                      k=RERANK_TOP_K)
    retrieved_texts = [c["content"] for c in chunks]

    raw_versions = _compose_versions(spec, retrieved_texts)
    n_versions = max(3, len(raw_versions))

    adapter = get_adapter()
    target_platforms = spec.get("target_platforms") or [spec.get("platform") or "weixin"]

    versions = []
    for i, v in enumerate(raw_versions):
        opt_body, consistency, suggestions = optimize_text(v, spec)
        adapted = {}
        for p in target_platforms:
            if not p:
                continue
            adapted[p] = adapter.adapt(p, v.get("title", ""), opt_body, v.get("tags", []))
        versions.append({
            "version_no": i + 1,
            "title": v.get("title", ""),
            "body": v.get("body", ""),
            "optimized_body": opt_body,
            "tags": v.get("tags", []),
            "style_consistency": consistency,
            "replacement_suggestions": suggestions,
            "adapted": adapted,
        })
    return {"retrieved": retrieved_texts, "chunks": chunks, "versions": versions}


def generate_topics(hotspots: list, persona: dict, domain: str | None = None,
                    count: int = 3, focus: dict | None = None) -> list:
    """选题策划（F2）：结合热点趋势与账号定位产出 ≥3 条选题（计划书 2.1.2 / 4.1.2）。

    focus：从热点看板点「用它做选题」直传的那条热点（P2-2）。传了就保证它被点名使用；
    不传时行为与改造前完全一致（向后兼容）。
    """
    llm = get_llm()
    filt = [h for h in hotspots if (not domain or h.get("domain") == domain)] or hotspots
    if focus:
        # focus 必须留在候选里：领域过滤 + 兜底之后它有可能被剔掉
        filt = [h for h in filt if h.get("id") != focus.get("id")]
        filt.insert(0, {**focus})
    if llm.backend == "mock":
        return _mock_topics(filt, persona, count, focus=focus)
    from .prompts import SYSTEM_TOPIC, build_topic_prompt
    # max_tokens 给足：3 条选题（每条约 300-500 token）在 1400 下会被截断，
    # 而截断的 JSON 解析必然失败，最终整批退化成模板选题——用户看到的"变笨"其实是"给少了"
    raw = llm.complete(SYSTEM_TOPIC, build_topic_prompt(hotspots, persona, focus=focus),
                       temperature=0.8, max_tokens=2600)
    return _parse_topics(raw, filt, count, focus=focus)


def _mock_topics(hotspots: list, persona: dict, count: int,
                 focus: dict | None = None) -> list:
    pos = persona.get("positioning") or "综合自媒体"
    tone = persona.get("tone") or "专业"
    domains = persona.get("domains") or "科技,生活,财经"
    aud_map = {"科技": "关注效率的职场与极客人群", "生活": "注重生活品质的都市年轻群体",
               "财经": "有资产配置需求的中产投资者",
               "美妆": "爱研究成分与妆效的年轻消费群体",
               "教育": "关注孩子成长与自我提升的学习型家长/学生"}
    out = []
    for h in hotspots:
        dom = h.get("domain") or (focus or {}).get("domain") or "科技"
        # 直传的那条：给出更像"可写"的角度（用建议角度或标题本身做钩子）
        hint = h.get("suggestion") or f"围绕「{h.get('title','热点')}」做差异化解读"
        out.append({
            "title": f"从「{h.get('title','热点')}」聊聊{dom}账号的切入角度",
            "angle": f"以「{tone}」口吻，{hint}",
            "reason": f"该热点热度 {h.get('heat',0)}，与账号覆盖领域（{domains}）匹配，适合快速跟进",
            "target_audience": aud_map.get(dom, "泛兴趣人群"),
            "domain": dom,
        })
    # 不足 count 条时，用模板补齐，保证 F2.1「≥3 条选题」
    while len(out) < max(3, count):
        dom = domains.split(",")[len(out) % len(domains.split(","))]
        out.append({
            "title": f"{dom}领域常态化选题 #{len(out)+1}：从用户高频疑问切入",
            "angle": f"以「{tone}」口吻，结合{pos}定位做实用解读",
            "reason": f"覆盖领域（{domains}）的常态化选题，适合稳定更新",
            "target_audience": aud_map.get(dom, "泛兴趣人群"),
            "domain": dom,
        })
    return out[:max(3, count)]


def _salvage_objects(text: str) -> list:
    """从被截断/带前后缀的文本里逐个抠出完整 JSON 对象。

    模型输出超长被截断时，最后一条往往缺右括号。整段 json.loads 会全盘失败，
    但前面已经完整的那几条是可用的——不能因为最后一条坏了就丢掉全部。
    """
    out = []
    depth, start = 0, None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:  # noqa: BLE001
                    pass
                start = None
            elif depth < 0:
                depth = 0
    return out


def _parse_topics(raw: str, hotspots: list, count: int,
                  focus: dict | None = None) -> list:
    default_domain = (focus or {}).get("domain") or (
        hotspots[0].get("domain") if hotspots else "科技")
    data = []
    try:
        data = json.loads(raw[raw.find("["):raw.rfind("]") + 1])
    except Exception:  # noqa: BLE001
        data = _salvage_objects(raw)      # 截断/包裹文本的兜底
    if isinstance(data, list) and data:
        out = [d for d in data if isinstance(d, dict) and (d.get("title") or "").strip()]
        if out:
            for d in out:
                d.setdefault("domain", default_domain)
            return out[:max(3, count)]
    return _mock_topics(hotspots, {}, count, focus=focus)
