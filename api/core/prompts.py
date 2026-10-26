# -*- coding: utf-8 -*-
"""
api/core/prompts.py — 提示词模板库（落实 T3.3 提示词组装 + T3.4 风格优化）

模板配置化，便于 M5 针对 bad case 迭代（计划书 4.5 / 6.3）。
"""
SYSTEM_GENERATE = (
    "你是资深自媒体内容创作助手。请基于给定的选题、平台规范、账号人设与检索素材，"
    "一次性产出 {n} 个差异化版本文案。每个版本用分隔符『=====版本N=====』隔开，"
    "每版本内含「标题：」「正文：」「标签：」三段。标题贴合平台字数规范，正文口语自然、"
    "事实表述谨慎并标注「待核实」，避免编造。\n"
    # 模型常把"我检索到的素材不太相关"写成正文开头，用户看到的就是一句免责声明而不是文案
    "正文直接进入文案内容：不要写素材边界说明、方法论说明、免责声明或任何创作过程说明；"
    "检索素材是否相关不需要向读者交代，不相关就不用它。"
)

SYSTEM_TOPIC = (
    "你是自媒体选题策划专家。结合热点趋势与账号定位，产出 ≥3 条选题清单。"
    "严格输出 JSON 数组，每条含 title(选题标题)、angle(创作角度)、"
    "reason(推荐理由)、target_audience(目标受众)。"
    # 字段长度是硬约束：不限长时模型会给每条写 200 字，3 条就撑爆 max_tokens，
    # 返回被截断成非法 JSON（实测踩过，表现为整体静默退化成模板选题）
    "angle 与 reason 各不超过 60 字，不要输出 JSON 之外的任何文字。"
)

SYSTEM_OPTIMIZE = (
    "你是文案润色专家。按给定语气、篇幅与人设口吻对正文逐段重写，"
    "保持信息完整、抑制同质化高频表达，并对照账号人设保持一致性。\n"
    "输出纪律：只输出重写后的正文本身。不要写「标题：」「正文：」「以下是重写稿」"
    "这类标签或说明，不要复述原标题，不要加任何前言、总结或点评。"
)

# 热点切入点（P2-1）：产出的是「从哪儿写」，不是「这条在说什么」。
# 三条禁令都对应真实 bad case：复述标题 = 废话；「值得关注」这类空话 = 无法执行；
# 带编号或引号 = 前端直接展示时脏字符外露。
SYSTEM_HOT_ANGLE = (
    "你是自媒体选题策划。为每条热榜话题给出一个「切入角度」，帮作者决定从哪儿写。"
    "要求：一句话、不超过 30 字；必须落到一个具体动作或冲突（成本账 / 前后对比 / 避坑清单 / 反常识 / 亲历）；"
    "禁止复述标题，禁止『值得关注』『引发热议』『可以从多方面展开』这类空话；"
    "不要编号、不要引号、不要解释。"
    "严格输出 JSON 字符串数组，元素数量与输入条数一致、顺序一致。"
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
        f"检索到的相关素材（仅供参考，发布前核实；与选题无关就忽略，不要在正文里解释它是否相关）：\n{ctx}\n\n"
        f"请产出 {n} 个版本。"
    )
    return SYSTEM_GENERATE.format(n=n), user


def build_topic_prompt(hotspots: list, persona: dict, focus: dict | None = None) -> str:
    pos = persona.get("positioning") or "综合自媒体"
    tone = persona.get("tone") or "专业"
    domains = persona.get("domains") or "科技,生活,财经"
    hs = "\n".join(f"- {h['title']}（{h.get('domain','')}）" for h in hotspots[:12]) or "（暂无热点）"
    # focus：从热点看板「用它做选题」直传的那条。必须显式点名，
    # 否则模型会在 12 条热点里自由漂移，用户点的那条反而没被用上。
    focus_line = ""
    if focus:
        focus_line = (f"本次必须围绕这条热点出题（至少 1 条选题直接以它为素材，不要绕开）："
                      f"「{focus.get('title', '')}」（领域：{focus.get('domain', '')}）\n\n")
    return (
        f"账号定位：{pos}；默认口吻：{tone}；覆盖领域：{domains}\n\n"
        f"{focus_line}"
        f"当前热点：\n{hs}\n\n"
        f"请结合以上热点与账号定位，产出选题清单（JSON 数组）。"
    )


def build_hot_angle_prompt(items: list) -> str:
    """把待生成建议的热点列表拼成批量 user prompt（一次调用处理多条，见 P2-1）。"""
    lines = []
    for i, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        summary = (it.get("summary") or "").strip()[:60]
        domain = it.get("domain") or "综合"
        line = f"{i}. [{domain}] {title}"
        if summary:
            line += f"｜摘要：{summary}"
        lines.append(line)
    return ("热榜话题：\n" + "\n".join(lines)
            + f"\n\n请输出 {len(items)} 个字符串的 JSON 数组，顺序与上面一致。")


def build_optimize_prompt(version: dict, tone: str, length: str, persona_voice: str):
    user = (
        f"请将以下文案按【语气={tone}】【篇幅={length}】【人设口吻={persona_voice or '保持原风格'}】"
        f"逐段重写。\n\n标题：{version.get('title','')}\n正文：{version.get('body','')}"
    )
    return SYSTEM_OPTIMIZE, user
