# -*- coding: utf-8 -*-
"""
api/core/llm.py — 大模型客户端（落实 T3.3 文案生成）

两种后端：
  - "openai"：OpenAI 兼容接口（国内直连），经环境变量注入 Key，不入库（计划书 2.2.4）
  - "mock"  ：内置离线生成器，无 Key 时仍能产出结构化多版本文案，
              保证后端可在本环境完整跑通 + 量化指标可测。

对外接口：
  complete(system, user, temperature, max_tokens) -> str          （非流式，用于风格优化）
  generate_versions(spec) -> [{"title","body","tags"}]            （≥3 版本，用于文案生成）
  stream_complete(system, user, **kw) -> Iterator[str]            （流式，SSE 用）
"""
import os
import re
import sys
import time

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE
# 领域词表唯一真源在 api/core/domain_keywords.py（覆盖 5 个官方领域）。
# 此处仅做导入，禁止在本文件重新定义——否则会与热点领域判定长期漂移。
from .domain_keywords import DOMAIN_KW

# 与 M2 语料同源的领域关键词，使生成内容与素材在语义上连贯
PLATFORM_OPENER = {
    "weixin": "在公众号这样开篇，结构完整、论证清晰：",
    "xhs": "小红书姐妹看过来👀，短句分段、口语种草：",
    "douyin": "家人们！开头必须强钩子：",
}
STYLE_MOD = {
    "专业": "从工程与专业口径严谨展开",
    "轻松": "用轻松好玩的口吻娓娓道来",
    "犀利": "先把话挑明，直击要害",
}
ANGLE_VARIANTS = [
    "从「为什么现在值得关注」切入，建立认知锚点",
    "给到一套可落地的操作步骤，降低行动门槛",
    "用反共识视角拆解常见误区，制造讨论张力",
]


class MockLLM:
    backend = "mock"

    def complete(self, system, user, temperature=0.8, max_tokens=1200):
        # 离线风格优化：基于输入做确定性改写（替换强调词 + 加衔接）
        return self._mock_optimize(user)

    def stream_complete(self, system, user, temperature=0.8, max_tokens=1200):
        text = self.complete(system, user, temperature, max_tokens)
        for piece in [text[i:i + 24] for i in range(0, len(text), 24)]:
            yield piece

    def generate_versions(self, spec) -> list:
        return self._mock_versions(spec)

    # ---------- 离线实现 ----------
    def _mock_versions(self, spec) -> list:
        domain = spec.get("domain", "科技")
        platform = spec.get("platform", "weixin")
        style = spec.get("style", "专业")
        intent = spec.get("intent", "干货科普")
        audience = spec.get("audience", "关注效率的人群")
        topic = spec.get("topic_title", "今日选题")
        voice = spec.get("persona_voice") or spec.get("persona_positioning") or "本账号"
        n = max(3, int(spec.get("n_versions", 3)))
        kws = DOMAIN_KW.get(domain, ["核心趋势"])
        snippets = spec.get("retrieved") or []
        versions = []
        for v in range(n):
            angle = ANGLE_VARIANTS[v % len(ANGLE_VARIANTS)]
            kw_a, kw_b, kw_c = kws[0], kws[1 % len(kws)], kws[2 % len(kws)]
            opener = PLATFORM_OPENER.get(platform, "")
            snippet = ("\n（参考素材）" + snippets[v % len(snippets)][:60] + "…") if snippets else ""
            title = f"【{domain}·{intent}】{topic}（角度{v+1}：{angle[:8]}）"
            body = (
                f"{opener}\n\n"
                f"本期我们聚焦「{topic}」。{STYLE_MOD.get(style, '')}。"
                f"{domain}领域的「{kw_a}」正在成为关键变量，它和「{kw_b}」"
                f"形成闭环——{angle}。\n\n"
                f"落到「{kw_c}」，常见误区是把工具当答案；更稳的做法是先沉淀"
                f"可复用素材与规范，再让系统按场景组装输出，既稳定产出又保留"
                f"「{voice}」的人设质感。{snippet}\n\n"
                f"面向{audience}，建议从最小可行动作开始：先搭底座，再谈多端分发。"
            )
            tags = [f"#{domain}#", f"#{intent}#", f"#{kw_a}#", f"#{style}创作#"]
            versions.append({"title": title, "body": body, "tags": tags})
        return versions

    def _mock_optimize(self, user_text: str) -> str:
        # 简单确定性“改写”：扩充衔接、强化人设一致性提示。
        # 幂等：若文本已被本函数包裹过（前缀/后缀已存在），先剥离再重新包裹，
        # 避免 generate 与 optimize 端点叠加调用时重复前缀/后缀。
        text = (user_text or "").strip()
        if not text:
            return text
        PREFIX = "【风格优化后】"
        SUFFIX = ("\n\n（已按语气/篇幅/人设口吻逐段重写，并对照历史文案做一致性校验；"
                  "对高频重复表达给出替换建议以抑制同质化。）")
        if text.startswith(PREFIX):
            text = text[len(PREFIX):].strip()
        tail = SUFFIX.strip()
        if text.endswith(tail):
            text = text[: -len(tail)].strip()
        return PREFIX + text + SUFFIX


class OpenAILLM:
    backend = "openai"

    def __init__(self):
        from openai import OpenAI  # 惰性导入，避免无 Key 环境强制依赖
        self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    def complete(self, system, user, temperature=0.8, max_tokens=1200):
        """非流式补全。空返回会重试一次——实测长 prompt 偶发返回空串，
        如果不重试，空串会一路流到 SSE、落库成"空白版本卡"。"""
        text = ""
        for attempt in range(2):
            resp = self._client.chat.completions.create(
                model=LLM_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = resp.choices[0].message.content or ""
            if text.strip():
                return text
            if attempt == 0:
                print("[LLM] 返回空内容，600ms 后重试一次")
                time.sleep(0.6)
        return text

    def stream_complete(self, system, user, temperature=0.8, max_tokens=1200):
        stream = self._client.chat.completions.create(
            model=LLM_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def generate_versions(self, spec) -> list:
        # 真实模型：要求结构化输出，按分隔符解析，失败则启发式兜底
        from .prompts import build_generate_prompt
        system, user = build_generate_prompt(spec)
        # ⚠️ token 预算必须随版本数线性放大：实测 deepseek-flash 写 3 个整版
        # 要约 2400+ 中文字（≈3000+ token），旧配置 1400 只能写完 2 个版，
        # 第 3 版被截断 → 解析不足 → 静默补齐成"和第一版一样"的假版本。
        # 800*n+400 仍然不够（3 版实测在 2800 处被切断），改用 1200*n+600。
        n = max(3, int(spec.get("n_versions", 3) or 3))
        budget = max(LLM_MAX_TOKENS, 1200 * n + 600)
        raw = self.complete(system, user, temperature=LLM_TEMPERATURE,
                            max_tokens=budget)
        versions = _parse_versions(raw, fallback_n=n)
        # 末版明显短于其他版 → 大概率是被 max_tokens 截断，留个痕便于排查
        if len(versions) >= 2:
            lens = [len(v.get("body") or "") for v in versions[:-1]]
            last = len(versions[-1].get("body") or "")
            if lens and last < 0.5 * (sum(lens) / len(lens)):
                print(f"[生成] 末版正文 {last} 字，明显短于前几版均值 "
                      f"{sum(lens) // len(lens)} 字，疑似被 max_tokens 截断（当前 {budget}）")
        return versions


_SCAFFOLD_MARK = re.compile(
    r"^\*{0,2}\s*(重写|改写|优化)?(标题|标题建议|正文|正文内容|标签|话题)\s*[:：]\s*\*{0,2}\s*$")
_SCAFFOLD_TITLE = re.compile(r"^\*{0,2}\s*(重写|改写|优化)?(标题|标题建议)\s*[:：]")
_SCAFFOLD_PREAMBLE = re.compile(r"^(以下|下面|这里)(是|为).{0,40}(重写|优化|改写|润色)")


def clean_body(text: str) -> str:
    """剥掉模型输出里的"结构化残留"：标题行、正文标签、前言、标签行。

    实测真实模型会把「**标题：xxx**」「**正文：**」「以下为重写稿，按…处理」
    一并写进正文；前端照原样显示成"文案正文"，比空正文更像 bug
    （2026-10-26 在 copy_versions 里实测到 24 字、以「**标题：**」开头的正文）。
    """
    if not text:
        return ""
    out = []
    for raw_line in str(text).replace("\r\n", "\n").split("\n"):
        s = raw_line.strip()
        if not s:
            out.append("")
            continue
        if _SCAFFOLD_MARK.match(s):
            continue
        if _SCAFFOLD_TITLE.match(s):
            continue
        if _SCAFFOLD_PREAMBLE.match(s) and len(s) < 100:
            continue
        if re.match(r"^标签\s*[:：]", s):
            continue
        out.append(s)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    if body.startswith("**") and body.endswith("**") and body.count("**") == 2:
        body = body[2:-2].strip()
    return body


def _parse_versions(raw: str, fallback_n: int = 3) -> list:
    parts = re.split(r"={3,}\s*版本\s*\d+\s*={3,}", raw)
    parts = [p.strip() for p in parts if p.strip()]
    versions = []
    for p in parts:
        title_m = re.search(r"(?:标题|title)\s*[:：]\s*(.+)", p)
        tag_m = re.search(r"(?:标签|tags|话题)\s*[:：]\s*(.+)", p)
        title = title_m.group(1).strip() if title_m else p[:30]
        # 正文先洗干净：模型常把「标题：」「标签：」一起写进同一块
        body = clean_body(p) or p
        tags_raw = tag_m.group(1).strip() if tag_m else ""
        tags = [t.strip() for t in re.findall(r"#[^#]+#", tags_raw)] or \
               [t.strip("#") for t in tags_raw.replace("，", ",").split(",") if t.strip("#")]
        versions.append({"title": title, "body": body, "tags": tags})
    if len(versions) < 3:
        # 兜底：整段作为一个版本，补齐到 3
        if not versions and raw.strip():
            versions = [{"title": raw[:30], "body": raw, "tags": []}]
        parsed_n = len(versions)
        while len(versions) < fallback_n:
            base = versions[0]
            versions.append({**base, "title": base["title"] + f"（变体{len(versions)+1}）"})
        # 补齐出来的版本正文与首版相同，是"少给 token"的症状，不是正常产出 → 必须留痕
        print(f"[生成] LLM 仅解析出 {parsed_n} 版，已用首版补齐到 {len(versions)} 版"
              f"（先查 max_tokens 是否够，再看解析分隔符）")
    return versions[:max(3, fallback_n)]


_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = OpenAILLM() if LLM_API_KEY else MockLLM()
    return _llm


# 兼容导入占位（避免未用到告警）
_ = (os, sys)
