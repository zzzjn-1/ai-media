# -*- coding: utf-8 -*-
"""
api/core/prompts.py — 提示词模板库（落实 T3.3 提示词组装 + T3.4 风格优化）

模板配置化，便于 M5 针对 bad case 迭代（计划书 4.5 / 6.3）。
"""
SYSTEM_GENERATE = (
    "你是资深自媒体内容创作助手。请基于给定的选题、平台规范、账号人设与检索素材，"
    "一次性产出 {n} 个差异化版本文案。每个版本用分隔符『=====版本N=====』隔开，"
    "每版本内含「标题：」「正文：」「标签：」三段。标题贴合平台字数规范，正文口语自然、"
    "事实表述谨慎并标注「待核实」，避免编造。"
)

SYSTEM_TOPIC = (
    "你是自媒体选题策划专家。结合热点趋势与账号定位，产出 ≥3 条选题清单。"
    "严格输出 JSON 数组，每条含 title(选题标题)、angle(创作角度)、"
    "reason(推荐理由)、target_audience(目标受众)。"
)

SYSTEM_OPTIMIZE = (
    "你是文案润色专家。按给定语气、篇幅与人设口吻对正文逐段重写，"
    "保持信息完整、抑制同质化高频表达，并对照账号人设保持一致性。"
)


def build_generate_prompt(spec: dict):
    domain = spec.get("domain", "")
    platform = spec.get("platform", "")
    style = spec.get("style", "")
    intent = spec.get("intent", "")
    audience = spec.get("audience", "")
    topic = spec.get("topic_title", "")
    voice = spec.get("persona_voice") or spec.get("persona_positioning") or ""
    n = max(3, int(spec.get("n_versions", 3)))
    retrieved = spec.get("retrieved") or []
    ctx = "\n".join(f"- {c[:120]}" for c in retrieved[:5]) or "（无检索素材）"
    user = (
        f"选题：{topic}\n平台：{platform}　领域：{domain}　意图：{intent}\n"
        f"语气风格：{style}　目标受众：{audience}\n账号人设/口吻：{voice}\n\n"
        f"检索到的相关素材（仅供参考，发布前核实）：\n{ctx}\n\n"
        f"请产出 {n} 个版本。"
    )
    return SYSTEM_GENERATE.format(n=n), user


def build_topic_prompt(hotspots: list, persona: dict) -> str:
    pos = persona.get("positioning") or "综合自媒体"
    tone = persona.get("tone") or "专业"
    domains = persona.get("domains") or "科技,生活,财经"
    hs = "\n".join(f"- {h['title']}（{h.get('domain','')}）" for h in hotspots[:12]) or "（暂无热点）"
    return (
        f"账号定位：{pos}；默认口吻：{tone}；覆盖领域：{domains}\n\n"
        f"当前热点：\n{hs}\n\n"
        f"请结合以上热点与账号定位，产出选题清单（JSON 数组）。"
    )


def build_optimize_prompt(version: dict, tone: str, length: str, persona_voice: str):
    user = (
        f"请将以下文案按【语气={tone}】【篇幅={length}】【人设口吻={persona_voice or '保持原风格'}】"
        f"逐段重写。\n\n标题：{version.get('title','')}\n正文：{version.get('body','')}"
    )
    return SYSTEM_OPTIMIZE, user
