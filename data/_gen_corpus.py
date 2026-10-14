# -*- coding: utf-8 -*-
"""
生成 M2 合成示例语料（data/corpus/*.txt）

说明：
- 本脚本产出的是【合成示例数据】，非真实抓取，规避语料版权风险（计划书 7.1/风险项）。
- 文件名编码元数据：{platform}_{domain}_{style}_{seq}.txt
  platform: weixin(公众号) / xhs(小红书) / douyin(抖音)
  domain:   科技 / 生活 / 财经 / 美妆 / 教育（与 frontend/src/api/types.ts 的 DOMAINS 对齐）
  style:    专业 / 轻松 / 犀利
- 真实接入：将 data/corpus/ 下文件替换为公开/授权来源原文即可，clean_chunk.py 自动识别文件名元数据。
"""
import os
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
CORPUS_DIR.mkdir(parents=True, exist_ok=True)

MARKER = "【示例语料·合成数据·仅供测试，非真实抓取，接入真实源请替换本文件】"

# 领域关键词库（用于让同领域素材在语义上聚拢，便于 M3 检索召回与评测 gold 关联）
DOMAIN_KW = {
    "科技": ["大模型", "算力", "端侧推理", "开源生态", "AI  Agent", "向量数据库", "RAG 检索增强"],
    "生活": ["晨间作息", "收纳整理", "一人食", "通勤效率", "居家好物", "情绪管理"],
    "财经": ["美联储加息", "CPI 数据", "权益资产", "现金仓位", "复利效应", "行业景气度"],
    "美妆": ["早C晚A护肤流程", "油皮夏季持妆底妆", "国货彩妆性价比测评", "敏感肌修护面霜", "美白精华成分之争", "防晒补涂与光老化", "黄皮显白唇釉选色", "医美级护肤平替"],
    "教育": ["零基础转行数据分析", "成人自学编程路径图", "考研二战心态管理", "少儿编程该不该学", "雅思口语高分模板", "职场人考证性价比", "碎片化时间学英语", "AI 时代的家庭教育"],
}

# 领域「闭环宾语」用词（科技/生活/财经 沿用历史文案，保证既有语料字节不变）
DOMAIN_NOUN = {"科技": "技术", "生活": "技术", "财经": "技术", "美妆": "事件", "教育": "趋势"}

# 平台口吻提示（写入内容，体现平台调性差异）
PLATFORM_TONE = {
    "weixin": "（公众号图文：结构完整、论证清晰）",
    "xhs": "（小红书：短句分段、口语种草、带 emoji 感）",
    "douyin": "（抖音：强钩子开头、口语化、节奏快）",
}

STYLE_OPEN = {
    "专业": "从工程口径看，",
    "轻松": "说点好玩的，",
    "犀利": "先把话挑明：",
}

def make_content(platform, domain, style, seq):
    kws = DOMAIN_KW[domain]
    # 用 seq 在关键词三元组上做轮换，使同组合不同序号的文本各异（避免入库去重误伤）
    rot = (seq - 1) % 3
    a, b, c = kws[rot], kws[(rot + 1) % 3], kws[(rot + 2) % 3]
    tone = PLATFORM_TONE[platform]
    open_ = STYLE_OPEN[style]
    noun = DOMAIN_NOUN.get(domain, "技术")
    p1 = (f"{open_}{domain}领域的「{a}」正在成为今年的关键变量。"
          f"它并非孤立{noun}，而是与「{b}」形成闭环，决定了普通用户能否真正用上能力。{tone}")
    p2 = (f"落到「{c}」这一环，多数团队的误区是把工具当答案。"
          f"更稳的做法是先沉淀可复用的素材与规范，再让系统按场景组装输出，"
          f"这样{domain}内容既能稳定产出，又不丢失账号本身的{style}质感。")
    p3 = (f"小结：{domain}创作的本质是「{a} + {b} + {c}」三件套的可持续运转。"
          f"先搭好底座，后面的多平台分发与风格适配才有支点。"
          f"（角度变体 {seq}：关注「{c}」在{platform}渠道的落地节奏）")
    return f"{MARKER}\n\n{p1}\n\n{p2}\n\n{p3}\n"

def main():
    platforms = ["weixin", "xhs", "douyin"]
    domains = ["科技", "生活", "财经", "美妆", "教育"]
    styles = ["专业", "轻松", "犀利"]
    count = 0
    for p in platforms:
        for d in domains:
            for s in styles:
                # 每个 (平台,领域,风格) 组合生成 2 篇，保证素材与分块规模
                for seq in (1, 2):
                    fname = f"{p}_{d}_{s}_{seq:02d}.txt"
                    text = make_content(p, d, s, seq)
                    (CORPUS_DIR / fname).write_text(text, encoding="utf-8")
                    count += 1
    print(f"generated {count} corpus files -> {CORPUS_DIR}")

if __name__ == "__main__":
    main()
