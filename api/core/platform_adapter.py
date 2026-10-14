# -*- coding: utf-8 -*-
"""
api/core/platform_adapter.py — 多平台适配规则引擎（落实 T3.5 / F5 / 计划书 4.1.5）

按 platform_rules 表调整标题长度、正文字数、话题标签格式与数量、排版样式，
并生成发布备注。规则外置（计划书 2.2.5），新增平台只需在 platform_rules 插一行。
"""
from ..database import get_conn


class PlatformAdapter:
    def __init__(self):
        self._rules = None

    def _load_rules(self):
        if self._rules is None:
            conn = get_conn()
            rows = conn.execute(
                "SELECT platform, display_name, title_max_len, body_max_len, "
                "hashtag_format, hashtag_count, layout_style, export_format "
                "FROM platform_rules WHERE is_active=1"
            ).fetchall()
            conn.close()
            self._rules = {r["platform"]: dict(r) for r in rows}

    def get_rule(self, platform: str):
        self._load_rules()
        return self._rules.get(platform)

    def adapt(self, platform: str, title: str, body: str, tags: list):
        rule = self.get_rule(platform)
        if not rule:
            # 未知平台：原样返回（容错，不阻断流程）
            return {
                "platform": platform, "title": title, "body": body,
                "tags": tags or [], "layout_style": "", "export_format": "txt",
                "truncated": False,
            }
        t_max = rule.get("title_max_len")
        b_max = rule.get("body_max_len")
        h_fmt = rule.get("hashtag_format") or "#{}#"
        h_cnt = rule.get("hashtag_count")

        new_title = title or ""
        new_body = body or ""
        truncated = False
        if t_max and len(new_title) > t_max:
            new_title = new_title[:t_max]
            truncated = True
        if b_max and len(new_body) > b_max:
            new_body = new_body[:b_max]
            truncated = True

        # 标签格式化 + 数量限制
        raw_tags = tags or []
        fmt_tags = []
        for t in raw_tags:
            t = str(t).strip().strip("#").strip()
            if not t:
                continue
            if "{}" in h_fmt:
                fmt_tags.append(h_fmt.format(t))
            else:
                fmt_tags.append(h_fmt + t)
        if h_cnt and len(fmt_tags) > h_cnt:
            fmt_tags = fmt_tags[:h_cnt]
            truncated = True

        return {
            "platform": platform,
            "display_name": rule.get("display_name"),
            "title": new_title,
            "body": new_body,
            "tags": fmt_tags,
            "layout_style": rule.get("layout_style") or "",
            "export_format": rule.get("export_format") or "txt",
            "truncated": truncated,
        }

    def publish_note(self, platform: str, title: str, body: str, tags: list) -> str:
        rule = self.get_rule(platform) or {}
        dn = rule.get("display_name") or platform
        t_max = rule.get("title_max_len")
        b_max = rule.get("body_max_len")
        lines = [
            f"【{dn}发布备注】",
            f"· 标题长度：{len(title or '')}/{t_max if t_max else '不限'} 字符",
            f"· 正文字数：{len(body or '')}/{b_max if b_max else '不限'} 字符",
            f"· 话题标签：{', '.join(tags) if tags else '无'}",
            f"· 排版建议：{rule.get('layout_style') or '按平台默认'}",
            "· 合规提示：AI 生成内容仅作创作参考，发布前请人工核实事实并标注「AI生成」。",
        ]
        return "\n".join(lines)


_adapter = PlatformAdapter()


def get_adapter() -> PlatformAdapter:
    return _adapter
