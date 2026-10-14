#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_deck.py — 把 项目汇报/slides/*.slide（SlideDSL）编译为 pptx。

引擎通道：editor_sdk（edsdk.py，HTTP JSON-RPC，端口 39099）。
slidep CLI 缺失时的恢复路径 —— 直接驱动同一套引擎的 slide_* 工具。

用法：
  python build_deck.py --dry-run          # 只编译不落盘，输出 ops_dump.json
  python build_deck.py                    # 完整构建
  python build_deck.py --pages 1 4 7      # 只构建指定页（1-based）
"""
import argparse
import importlib.util
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
EDS_CANDIDATES = [
    os.path.join(HERE, "edsdk.py"),
    os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\zzz\AppData\Local"),
                 r"..\Roaming") ,  # 占位，主路径见下方
]

def load_edsdk():
    cands = [
        os.path.join(HERE, "edsdk.py"),
        r"C:\Users\zzz\.workbuddy\plugins\cache\workbuddy-builtin\tencent-pptx"
        r"\5.7.7-wb.40774747.g2e8619da.had9031c93ded\skills\tencent-pptx"
        r"\scripts\edsdk.py",
    ]
    for p in cands:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("edsdk", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError("edsdk.py not found")

edsdk = None  # lazy load

def call(tool, **args):
    result = edsdk._rpc("tools/call", {"name": tool, "arguments": args})
    texts = [c.get("text", "") for c in (result.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    raw = "\n".join(texts) if texts else "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 部分工具返回纯文本（如 create_slide: "… file_id=xxx, file_path=yyy"）
        m = re.search(r"file_id=([\w.-]+)", raw)
        if m:
            return {"file_id": m.group(1), "_raw": raw}
        return {"_raw": raw}

# ---------------------------------------------------------------- 样式解析

def parse_style(obj_src):
    """解析 JS 对象字面量（扁平；容忍多层首尾花括号 style={{...}}）。"""
    s = obj_src.strip().lstrip("{").rstrip("}")
    out = {}
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] in " \t,":
            i += 1
        m = re.match(r"([A-Za-z_$][\w$]*)\s*:\s*", s[i:])
        if not m:
            break
        key = m.group(1)
        i += m.end()
        if i < n and s[i] in "'\"":
            q = s[i]
            j = i + 1
            while j < n and s[j] != q:
                j += 1
            out[key] = s[i + 1:j]
            i = j + 1
        else:
            m2 = re.match(r"[-0-9.]+|[A-Za-z_$][\w$.]*", s[i:])
            if not m2:
                break
            v = m2.group(0)
            try:
                out[key] = int(v)
            except ValueError:
                try:
                    out[key] = float(v)
                except ValueError:
                    out[key] = v
            i += m2.end()
    return out

ATTR_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|'[^']*'|\"[^\"]*\")")

def parse_attrs(attr_src):
    attrs = {}
    for m in ATTR_RE.finditer(attr_src):
        k, v = m.group(1), m.group(2)
        if v.startswith("{"):
            attrs[k] = parse_style(v)
        else:
            attrs[k] = v[1:-1]
    return attrs

def parse_padding(v):
    if v is None:
        return (0, 0, 0, 0)
    if isinstance(v, (int, float)):
        return (v, v, v, v)
    parts = [p.strip() for p in str(v).split() if p.strip()]
    vals = []
    for p in parts:
        m = re.match(r"(-?[\d.]+)", p)
        vals.append(float(m.group(1)) if m else 0.0)
    if len(vals) == 1:
        return (vals[0],) * 4
    if len(vals) == 2:
        return (vals[0], vals[1], vals[0], vals[1])
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2], vals[1])
    return tuple(vals[:4])

# ---------------------------------------------------------------- JSX 解析

class Node:
    def __init__(self, tag, style=None, attrs=None):
        self.tag = tag
        self.style = style or {}
        self.attrs = attrs or {}
        self.children = []   # [("text", s) | ("node", Node)]
        self.text = ""
        self.rect = None

    def get(self, k, default=None):
        return self.style.get(k, default)

COMMENT_RE = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.S)

def find_tag_end(src, i):
    """i 指向标签名起点；返回 (tag, attrs_src, self_closing, next_i)。"""
    m = re.match(r"([A-Za-z][\w.:-]*)", src[i:])
    if not m:
        return None
    tag = m.group(1)
    j = i + m.end()
    depth = 0
    quote = None
    while j < len(src):
        c = src[j]
        if quote:
            if c == quote:
                quote = None
            j += 1
            continue
        if c in "'\"":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            self_closing = src[j - 1] == "/"
            return tag, src[i + m.end():j - (1 if self_closing else 0)], self_closing, j + 1
        j += 1
    raise ValueError("unterminated tag: " + tag)

def parse_nodes(src, i=0):
    nodes, buf, n = [], [], len(src)
    while i < n:
        if src[i] == "<":
            if src.startswith("</", i):
                if buf:
                    nodes.append(("text", "".join(buf)))
                    buf = []
                m = re.match(r"</\s*([A-Za-z][\w.:-]*)\s*>", src[i:])
                return nodes, i + m.end()
            if src.startswith("<!--", i):
                j = src.find("-->", i)
                i = j + 3
                continue
            if buf:
                nodes.append(("text", "".join(buf)))
                buf = []
            tag, attr_src, self_closing, nxt = find_tag_end(src, i + 1)
            if tag is None:
                buf.append(src[i])
                i += 1
                continue
            attrs = parse_attrs(attr_src)
            node = Node(tag, style=attrs.get("style", {}), attrs=attrs)
            if self_closing:
                nodes.append(("node", node))
                i = nxt
            else:
                children, i = parse_nodes(src, nxt)
                node.children = children
                nodes.append(("node", node))
        else:
            buf.append(src[i])
            i += 1
    if buf:
        nodes.append(("text", "".join(buf)))
    return nodes, i

def normalize_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def norm_tree(node):
    """规范化：提取 Text 文本；容器只保留已知子标签；svg 子树特殊处理。"""
    if node.tag == "Text":
        parts = []
        for k, v in node.children:
            if k == "text":
                parts.append(v)
            elif v.tag == "br":
                parts.append("\n")
        node.text = normalize_text("".join(parts))
        node.children = []
        return
    if node.tag == "svg":
        norm_svg_children(node)
        return
    if node.tag in ("Box", "Slide"):
        kept = []
        for k, v in node.children:
            if k == "node" and v.tag in ("Box", "Text", "Image", "FAIcon", "svg"):
                norm_tree(v)
                kept.append((k, v))
        node.children = kept
        return
    if node.tag == "g":
        norm_svg_children(node)

def norm_svg_children(node):
    for k, v in node.children:
        if k != "node":
            continue
        if v.tag == "text":
            v.text = normalize_text("".join(
                t for k2, t in v.children if k2 == "text"))
            v.children = []
        elif v.tag == "g":
            norm_svg_children(v)

def parse_slide(path):
    src = open(path, encoding="utf-8").read()
    src = COMMENT_RE.sub(" ", src)
    src = re.sub(r"^\s*const\s+FONT\s*=.*$", "", src, count=1, flags=re.M)
    m = re.search(r"<Slide\b", src)
    tag, attr_src, self_closing, nxt = find_tag_end(src, m.start() + 1)
    attrs = parse_attrs(attr_src)
    slide = Node("Slide", style=attrs.get("style", {}), attrs=attrs)
    children, _ = parse_nodes(src, nxt)
    slide.children = children
    norm_tree(slide)
    return slide

# ---------------------------------------------------------------- 颜色工具

def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb):
    return "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)

def parse_color(v):
    """→ (hex, alpha_pct_transparency) 或 None"""
    if v is None:
        return None
    v = str(v).strip()
    m = re.match(r"rgba?\(([^)]+)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        r, g, b = (float(x) for x in parts[:3])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return (rgb_to_hex((r, g, b)), round((1 - a) * 100))
    m = re.match(r"#([0-9a-fA-F]{3,6})$", v)
    if m:
        return (rgb_to_hex(hex_to_rgb(m.group(1))), 0)
    return None

GRAD_STOP_RE = re.compile(r"(rgba?\([^)]*\)|#[0-9a-fA-F]{3,6})\s+([\d.]+)%")

def gradient_stops(v):
    return [(c, float(o)) for c, o in GRAD_STOP_RE.findall(v)]

def mid_color(stops):
    rgbs = [parse_color(c) for c, _ in stops]
    rgbs = [r[0] for r in rgbs if r]
    if not rgbs:
        return None
    vals = [hex_to_rgb(h) for h in rgbs]
    return rgb_to_hex(tuple(sum(v[i] for v in vals) / len(vals) for i in range(3)))

def resolve_fill(v):
    """background/fill 值 → (hex, alpha_pct) 或 None（跳过）"""
    if v is None:
        return None
    v = str(v)
    if "radial-gradient" in v:
        return None  # 装饰性径向光晕，跳过
    if "linear-gradient" in v:
        stops = gradient_stops(v)
        if not stops:
            return None
        alphas = [parse_color(c)[1] for c, _ in stops
                  if parse_color(c) and parse_color(c)[1] > 0]
        hexv = mid_color(stops)
        if hexv is None:
            return None
        if alphas:
            return (hexv, round(sum(alphas) / len(alphas)))
        return (hexv, 0)
    return parse_color(v)

def parse_border(v):
    """'1px solid #E2E8F0' → (hex, width_px) 或 None"""
    if not v:
        return None
    m = re.search(r"([\d.]+)px\s+solid\s+(\S+)\s*$", str(v).strip())
    if not m:
        return None
    pc = parse_color(m.group(2))
    if not pc:
        return None
    return (pc[0], float(m.group(1)))

# ---------------------------------------------------------------- 文本测量

def char_em(c):
    o = ord(c)
    if o >= 0x2E80 or o in (0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2026):
        return 1.0
    if c == " ":
        return 0.32
    if c.isascii() and c.isalnum():
        return 0.58
    return 0.42

def text_natural_width(s, fs):
    return sum(char_em(c) for c in s) * fs

def text_height(s, fs, avail_w, line_height):
    lines = 0
    for seg in s.split("\n"):
        w = text_natural_width(seg, fs)
        lines += max(1, math.ceil(w / max(avail_w, 1) - 1e-6))
    return lines * fs * line_height

# ---------------------------------------------------------------- 布局引擎

SCALE = 0.75  # px → pt（1280px 画布 → 960pt）

def is_abs(node):
    return node.get("position") == "absolute"

def resolve_len(v, avail):
    if v is None:
        return None
    if isinstance(v, str):
        if v.endswith("%"):
            return avail * float(v[:-1]) / 100.0
        m = re.match(r"(-?[\d.]+)", v)
        return float(m.group(1)) if m else None
    return float(v)

def flow_children(node):
    return [c for k, c in node.children if k == "node" and not is_abs(c)]

def measure(node, avail_w):
    """自然尺寸 (w, h)。行内子节点用极大 avail 获得收缩宽度。"""
    st = node.style
    if node.tag == "Text":
        fs = float(st.get("fontSize", 16))
        lh = float(st.get("lineHeight", 1.3))
        ew = resolve_len(st.get("width"), avail_w)
        s = node.text
        nw = text_natural_width(s.replace("\n", ""), fs)
        w = ew if ew is not None else min(nw, avail_w)
        h = text_height(s, fs, ew if ew is not None else avail_w, lh)
        return (w, h)
    if node.tag == "FAIcon":
        return (float(st.get("width", 24)), float(st.get("height", 24)))
    if node.tag == "Image":
        return (float(st.get("width", 0)), float(st.get("height", 0)))
    if node.tag == "svg":
        return (float(st.get("width", 0)), float(st.get("height", 0)))
    if node.tag == "Box":
        ew = resolve_len(st.get("width"), avail_w)
        pad = parse_padding(st.get("padding"))
        content_w = (ew if ew is not None else avail_w) - pad[1] - pad[3]
        direction = st.get("flexDirection", "column")
        gap = float(st.get("gap", 0) or 0)
        flow = flow_children(node)
        sizes = [measure(c, content_w if direction == "column" else 10 ** 6)
                 for c in flow]
        if direction == "row":
            nat_w = sum(s[0] for s in sizes) + gap * (len(sizes) - 1)
            nat_h = max((s[1] for s in sizes), default=0)
        else:
            nat_w = max((s[0] for s in sizes), default=0)
            nat_h = sum(s[1] for s in sizes) + gap * (len(sizes) - 1)
        extra = sum(c.get("marginTop") for c in flow
                    if isinstance(c.get("marginTop"), (int, float)))
        if direction == "column":
            nat_h += extra
        w = ew if ew is not None else nat_w + pad[1] + pad[3]
        eh = resolve_len(st.get("height"), None)
        h = eh if eh is not None else nat_h + pad[0] + pad[2]
        return (w, h)
    return (0, 0)

def layout_flow(children, cx, cy, cw, ch, direction, gap, justify, align):
    n = len(children)
    if n == 0:
        return []
    sizes = []
    for c in children:
        if direction == "row":
            ew = resolve_len(c.get("width"), cw)
            mw, mh = measure(c, 10 ** 6)
            w = ew if ew is not None else mw
            if c.get("flex"):
                w = None
            eh = resolve_len(c.get("height"), ch)
            h = eh if eh is not None else mh
        else:
            ew = resolve_len(c.get("width"), cw)
            mw, mh = measure(c, cw)
            w = ew if ew is not None else cw
            eh = resolve_len(c.get("height"), ch)
            h = eh if eh is not None else mh
        sizes.append([w, h])
    # flex:1 分配主轴剩余空间
    flex_ids = [i for i, c in enumerate(children) if c.get("flex")]
    if flex_ids:
        mi = 0 if direction == "row" else 1
        fixed = sum(s[mi] or 0 for i, s in enumerate(sizes) if i not in flex_ids)
        free = max(0.0, (cw if direction == "row" else ch) - fixed - gap * (n - 1))
        share = free / len(flex_ids)
        for i in flex_ids:
            sizes[i][mi] = share
    auto_ids = [i for i, c in enumerate(children) if c.get("marginTop") == "auto"]
    total_main = sum(sizes[i][0 if direction == "row" else 1] for i in range(n)) \
        + gap * (n - 1)
    free_main = max(0.0, (cw if direction == "row" else ch) - total_main)

    out = []
    if direction == "row":
        if justify == "space-between" and not flex_ids:
            gap_eff = (cw - sum(s[0] for s in sizes)) / max(1, n - 1) if n > 1 else 0
            pos = cx
            for i, c in enumerate(children):
                w, h = sizes[i]
                y = cy + (ch - h) / 2 if align == "center" else cy
                out.append((c, pos, y, w, h))
                pos += w + gap_eff
        elif justify == "center":
            group = sum(s[0] for s in sizes) + gap * (n - 1)
            pos = cx + (cw - group) / 2
            for i, c in enumerate(children):
                w, h = sizes[i]
                y = cy + (ch - h) / 2 if align == "center" else cy
                out.append((c, pos, y, w, h))
                pos += w + gap
        else:
            extra_gap = free_main / max(1, len(auto_ids)) if auto_ids else 0
            pos = cx
            for i, c in enumerate(children):
                w, h = sizes[i]
                if c.get("marginTop") == "auto":
                    pos += extra_gap
                elif isinstance(c.get("marginTop"), (int, float)):
                    pos += c.get("marginTop")
                y = cy + (ch - h) / 2 if align == "center" else cy
                out.append((c, pos, y, w, h))
                pos += w + gap
    else:
        if justify == "space-between" and not flex_ids:
            gap_eff = (ch - sum(s[1] for s in sizes)) / max(1, n - 1) if n > 1 else 0
            pos = cy
            for i, c in enumerate(children):
                w, h = sizes[i]
                x = cx + (cw - w) / 2 if align == "center" else cx
                out.append((c, x, pos, w, h))
                pos += h + gap_eff
        elif justify == "center":
            group = sum(s[1] for s in sizes) + gap * (n - 1)
            pos = cy + (ch - group) / 2
            for i, c in enumerate(children):
                w, h = sizes[i]
                x = cx + (cw - w) / 2 if align == "center" else cx
                out.append((c, x, pos, w, h))
                pos += h + gap
        else:
            extra_gap = free_main / max(1, len(auto_ids)) if auto_ids else 0
            pos = cy
            for i, c in enumerate(children):
                w, h = sizes[i]
                if c.get("marginTop") == "auto":
                    pos += extra_gap
                elif isinstance(c.get("marginTop"), (int, float)):
                    pos += c.get("marginTop")
                x = cx + (cw - w) / 2 if align == "center" else cx
                out.append((c, x, pos, w, h))
                pos += h + gap
    return out

def layout_node(node, x, y, w, h):
    node.rect = (x, y, w, h)
    st = node.style
    if node.tag in ("Text", "FAIcon", "Image", "svg"):
        return
    pad = parse_padding(st.get("padding"))
    cx, cy = x + pad[3], y + pad[0]
    cw, ch = w - pad[1] - pad[3], h - pad[0] - pad[2]
    placed = layout_flow(flow_children(node), cx, cy, cw, ch,
                         st.get("flexDirection", "column"),
                         float(st.get("gap", 0) or 0),
                         st.get("justifyContent", "flex-start"),
                         st.get("alignItems", "stretch"))
    for c, px, py, pw, ph in placed:
        layout_node(c, px, py, pw, ph)
    # 绝对定位子节点（相对父框原点；缺省尺寸取自然尺寸）
    for k, c in node.children:
        if k == "node" and is_abs(c):
            ax = resolve_len(c.get("left"), w)
            ay = resolve_len(c.get("top"), h)
            aw = resolve_len(c.get("width"), w)
            ah = resolve_len(c.get("height"), h)
            if ax is None and c.get("right") is not None:
                ax = w - float(c.get("right")) - (aw or 0)
            if ay is None and c.get("bottom") is not None:
                ay = h - float(c.get("bottom")) - (ah or 0)
            if aw is None or ah is None:
                nw, nh = measure(c, aw if aw is not None else w)
                aw = aw if aw is not None else nw
                ah = ah if ah is not None else nh
            layout_node(c, x + (ax or 0), y + (ay or 0), aw or 0, ah or 0)

def layout_slide(slide):
    W = float(slide.get("width", 1280))
    H = float(slide.get("height", 720))
    pad = parse_padding(slide.get("padding"))
    slide.rect = (0, 0, W, H)
    placed = layout_flow(flow_children(slide), pad[3], pad[0],
                         W - pad[1] - pad[3], H - pad[0] - pad[2],
                         slide.get("flexDirection", "column"),
                         float(slide.get("gap", 0) or 0),
                         slide.get("justifyContent", "flex-start"),
                         slide.get("alignItems", "stretch"))
    for c, px, py, pw, ph in placed:
        layout_node(c, px, py, pw, ph)
    for k, c in slide.children:
        if k == "node" and is_abs(c):
            ax = resolve_len(c.get("left"), W) or 0
            ay = resolve_len(c.get("top"), H) or 0
            aw = resolve_len(c.get("width"), W)
            ah = resolve_len(c.get("height"), H)
            if aw is None or ah is None:
                nw, nh = measure(c, aw if aw is not None else W)
                aw = aw if aw is not None else nw
                ah = ah if ah is not None else nh
            layout_node(c, ax, ay, aw or 0, ah or 0)

# ---------------------------------------------------------------- SVG 翻译

SVG_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")

def svg_attrs(src):
    return {k: v[1:-1] for k, v in SVG_ATTR_RE.findall(src)}

def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def parse_path_d(d):
    tokens = re.findall(r"[MmLlCcZz]|[-+]?[\d.]+(?:e[-+]?\d+)?", d)
    cmds, i, cur = [], 0, None
    while i < len(tokens):
        t = tokens[i]
        if t in "MmLlCcZz":
            cmd, i = t, i + 1
        else:
            cmd = cur
        if cmd in ("M", "m"):
            cmds.append(("M", float(tokens[i]), float(tokens[i + 1]))); i += 2
        elif cmd in ("L", "l"):
            cmds.append(("L", float(tokens[i]), float(tokens[i + 1]))); i += 2
        elif cmd in ("C", "c"):
            cmds.append(("C",) + tuple(float(tokens[i + k]) for k in range(6))); i += 6
        elif cmd in ("Z", "z"):
            cmds.append(("Z",)); i += 1
        else:
            i += 1
        cur = cmd
    return cmds

def flatten_cubic(p0, p1, p2, p3, steps=12):
    pts = []
    for k in range(1, steps + 1):
        t = k / steps
        mt = 1 - t
        pts.append((
            mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
            mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]))
    return pts

def emit_svg(node, ox, oy, out):
    st = node.style
    vb = st.get("viewBox")
    vb_w = fnum(str(vb).split()[2]) if isinstance(vb, str) and len(str(vb).split()) >= 3 else 0
    svg_w = float(st.get("width", vb_w or 0))
    scale = (svg_w / vb_w) if vb_w else 1.0
    grads = {}

    def col(v):
        if v and str(v).startswith("url(#"):
            g = grads.get(str(v)[5:-1])
            if g:
                mc = mid_color(g)
                if mc:
                    return (mc, 0)
            return None
        return resolve_fill(v)

    def walk(items):
        for kind, val in items:
            if kind != "node":
                continue
            tag = val.tag.lower()
            a = val.attrs
            if tag == "defs":
                walk(val.children)
                continue
            if tag == "lineargradient":
                stops = []
                for k2, v2 in val.children:
                    if k2 == "node" and v2.tag.lower() == "stop":
                        sa = v2.attrs
                        stops.append((sa.get("stop-color", sa.get("stopColor", "#000000")),
                                      fnum(sa.get("offset", "0%"))))
                grads[a.get("id")] = stops
                continue
            if tag in ("marker",):
                continue
            if tag == "g":
                walk(val.children)
                continue
            if tag == "rect":
                x, y = fnum(a.get("x")), fnum(a.get("y"))
                w, h = fnum(a.get("width")), fnum(a.get("height"))
                rx = fnum(a.get("rx", a.get("ry", 0)))
                fill, stroke = col(a.get("fill")), col(a.get("stroke"))
                sw = fnum(a.get("stroke-width", a.get("strokeWidth", 0))) * scale
                if not fill and not (stroke and sw > 0):
                    continue
                shape = {"x": round((ox + x * scale) * SCALE, 2),
                         "y": round((oy + y * scale) * SCALE, 2),
                         "w": round(w * scale * SCALE, 2),
                         "h": round(h * scale * SCALE, 2)}
                if rx > 0 and w and h:
                    if rx >= 0.5 * min(w, h):
                        shape["shape_type"] = "ellipse"
                    else:
                        shape["shape_type"] = "roundRect"
                        shape["_corner"] = min(50000, round(rx / min(w, h) * 100000))
                if fill:
                    shape["fill_color"] = fill[0]
                    if fill[1]:
                        shape["fill_alpha"] = fill[1]
                if stroke and sw > 0:
                    shape["border_color"] = stroke[0]
                    shape["border_width"] = round(sw * SCALE, 2)
                    if stroke[1]:
                        shape["border_alpha"] = stroke[1]
                out["shapes"].append(shape)
            elif tag == "circle":
                cx, cy, r = fnum(a.get("cx")), fnum(a.get("cy")), fnum(a.get("r"))
                fill = col(a.get("fill"))
                if not fill:
                    continue
                sh = {"x": round((ox + (cx - r) * scale) * SCALE, 2),
                      "y": round((oy + (cy - r) * scale) * SCALE, 2),
                      "w": round(2 * r * scale * SCALE, 2),
                      "h": round(2 * r * scale * SCALE, 2),
                      "shape_type": "ellipse", "fill_color": fill[0]}
                if fill[1]:
                    sh["fill_alpha"] = fill[1]
                out["shapes"].append(sh)
            elif tag == "line":
                x1, y1 = fnum(a.get("x1")), fnum(a.get("y1"))
                x2, y2 = fnum(a.get("x2")), fnum(a.get("y2"))
                stroke = col(a.get("stroke"))
                sw = fnum(a.get("stroke-width", a.get("strokeWidth", 1))) * scale
                if not stroke:
                    continue
                out["lines"].append({
                    "x1": round((ox + x1 * scale) * SCALE, 2),
                    "y1": round((oy + y1 * scale) * SCALE, 2),
                    "x2": round((ox + x2 * scale) * SCALE, 2),
                    "y2": round((oy + y2 * scale) * SCALE, 2),
                    "color": stroke[0], "w": round(sw * SCALE, 2)})
            elif tag == "path":
                emit_path(a, ox, oy, scale, col, out)
            elif tag == "text":
                emit_svg_text(val, a, ox, oy, scale, col, out)
    walk(node.children)

def emit_path(a, ox, oy, scale, col, out):
    cmds = parse_path_d(a.get("d", ""))
    if not cmds:
        return
    fill, stroke = col(a.get("fill")), col(a.get("stroke"))
    sw = fnum(a.get("stroke-width", a.get("strokeWidth", 1))) * scale
    dash = a.get("stroke-dasharray", a.get("strokeDasharray"))
    marker = a.get("marker-end", a.get("markerEnd"))
    pts, cur = [], None
    for c in cmds:
        if c[0] == "M":
            cur = (c[1], c[2]); pts.append(cur)
        elif c[0] == "L":
            cur = (c[1], c[2]); pts.append(cur)
        elif c[0] == "C":
            seg = flatten_cubic(cur, (c[1], c[2]), (c[3], c[4]), (c[5], c[6]))
            pts.extend(seg); cur = seg[-1]
    if fill and len(pts) == 3 and any(c[0] == "Z" for c in cmds):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        apex = max(range(3), key=lambda i: pts[i][1])
        rotation = 180 if pts[apex][1] > sum(ys) / 3 else 0
        sh = {"x": round((ox + min(xs) * scale) * SCALE, 2),
              "y": round((oy + min(ys) * scale) * SCALE, 2),
              "w": round((max(xs) - min(xs)) * scale * SCALE, 2),
              "h": round((max(ys) - min(ys)) * scale * SCALE, 2),
              "shape_type": "triangle", "fill_color": fill[0]}
        if rotation:
            sh["_rotation"] = rotation
        out["shapes"].append(sh)
        return
    if not stroke:
        return
    segs = [(pts[i - 1], pts[i]) for i in range(1, len(pts))]
    for idx, (p1, p2) in enumerate(segs):
        if abs(p1[0] - p2[0]) < 0.01 and abs(p1[1] - p2[1]) < 0.01:
            continue
        line = {"x1": round((ox + p1[0] * scale) * SCALE, 2),
                "y1": round((oy + p1[1] * scale) * SCALE, 2),
                "x2": round((ox + p2[0] * scale) * SCALE, 2),
                "y2": round((oy + p2[1] * scale) * SCALE, 2),
                "color": stroke[0], "w": round(sw * SCALE, 2)}
        if dash:
            line["dash"] = "dash"
        if marker and idx == len(segs) - 1:
            line["line_type"] = "arrow"
        out["lines"].append(line)

def emit_svg_text(node, a, ox, oy, scale, col, out):
    fs = fnum(a.get("font-size", a.get("fontSize", 14))) * scale
    content = node.text or ""
    if not content.strip():
        return
    anchor = a.get("text-anchor", a.get("textAnchor", "start"))
    fill = col(a.get("fill"))
    bold = str(a.get("font-weight", a.get("fontWeight", ""))) in ("bold", "600", "700")
    x, y = fnum(a.get("x")), fnum(a.get("y"))
    nw = text_natural_width(content, fs)
    tx = x - nw / 2 if anchor == "middle" else x
    spec = {"x": round((ox + tx * scale) * SCALE, 2),
            "y": round((oy + (y - 0.82 * fs) * scale) * SCALE, 2),
            "w": round((nw + fs * 0.5) * SCALE, 2),
            "h": round(fs * 1.4 * SCALE, 2),
            "text": content, "font_size": max(8, round(fs * SCALE))}
    if fill:
        spec["font_color"] = fill[0]
    if bold:
        spec["_bold"] = True
    out["texts"].append(spec)

# ---------------------------------------------------------------- FAIcon 配方

def emit_icon(node, x, y, w, h, out):
    st = node.style
    name = (node.attrs.get("name") or "").lower()
    fill = resolve_fill(st.get("fill")) or ("3B82F6", 0)
    color, alpha = fill
    S = lambda v: round(v * SCALE, 2)

    def sh_rect(fx, fy, fw, fh, r=None, extra=None):
        sh = {"x": S(x + fx * w), "y": S(y + fy * h),
              "w": S(fw * w), "h": S(fh * h), "fill_color": color}
        if alpha:
            sh["fill_alpha"] = alpha
        if r:
            sh["shape_type"] = "roundRect"
            sh["_corner"] = min(50000, round(r * 100000))
        if extra:
            sh.update(extra)
        out["shapes"].append(sh)

    def dot(cx, cy, r):
        sh = {"x": S(x + (cx - r) * w), "y": S(y + (cy - r) * h),
              "w": S(2 * r * w), "h": S(2 * r * h),
              "shape_type": "ellipse", "fill_color": color}
        if alpha:
            sh["fill_alpha"] = alpha
        out["shapes"].append(sh)

    def line(x1, y1, x2, y2, wpt=1.0):
        out["lines"].append({"x1": S(x + x1 * w), "y1": S(y + y1 * h),
                             "x2": S(x + x2 * w), "y2": S(y + y2 * h),
                             "color": color, "w": wpt})

    if name == "check-circle":
        out["texts"].append({"x": S(x), "y": S(y + h * 0.02), "w": S(w), "h": S(h),
                             "text": "✓", "font_size": max(9, round(h * 0.9 * SCALE)),
                             "font_color": color})
    elif name == "arrow-right":
        sh_rect(0, 0.28, 1, 0.44, extra={"shape_type": "rightArrow"})
    elif name == "star":
        sh_rect(0, 0, 1, 1, extra={"shape_type": "star5"})
    elif name == "cog":
        sh_rect(0.08, 0.08, 0.84, 0.84, extra={"shape_type": "donut"})
        dot(0.5, 0.5, 0.16)
    elif name == "lock":
        dot(0.5, 0.32, 0.22)
        sh_rect(0.22, 0.5, 0.56, 0.44, r=0.12)
    elif name == "folder":
        sh_rect(0.08, 0.24, 0.3, 0.14, r=0.05)
        sh_rect(0.08, 0.34, 0.84, 0.52, r=0.08)
    elif name == "chart-bar":
        sh_rect(0.14, 0.5, 0.2, 0.4)
        sh_rect(0.4, 0.28, 0.2, 0.62)
        sh_rect(0.66, 0.08, 0.2, 0.82)
    elif name == "chart-line":
        dot(0.16, 0.72, 0.09); dot(0.5, 0.4, 0.09); dot(0.84, 0.16, 0.09)
        line(0.16, 0.72, 0.5, 0.4); line(0.5, 0.4, 0.84, 0.16)
    elif name == "user":
        dot(0.5, 0.3, 0.2)
        sh_rect(0.18, 0.54, 0.64, 0.46, r=0.5)
    elif name == "share":
        dot(0.22, 0.24, 0.13); dot(0.78, 0.24, 0.13); dot(0.5, 0.78, 0.13)
        line(0.3, 0.33, 0.44, 0.68); line(0.7, 0.33, 0.56, 0.68)
    elif name == "edit":
        sh_rect(0.2, 0.06, 0.34, 0.88, r=0.2, extra={"_rotation": 45})
    else:
        dot(0.5, 0.5, 0.42)

# ---------------------------------------------------------------- 页面编译

def compile_page(slide):
    out = {"shapes": [], "texts": [], "images": [], "lines": []}
    W = float(slide.get("width", 1280))
    H = float(slide.get("height", 720))
    bg = resolve_fill(slide.get("background"))
    if bg:
        out["shapes"].append({"x": 0, "y": 0,
                              "w": round(W * SCALE, 2), "h": round(H * SCALE, 2),
                              "fill_color": bg[0],
                              **({"fill_alpha": bg[1]} if bg[1] else {})})

    def walk(node):
        if node.rect is None:
            return
        x, y, w, h = node.rect
        st = node.style
        if node.tag == "Image":
            src = node.attrs.get("src", "")
            out["images"].append({
                "x": round(x * SCALE, 2), "y": round(y * SCALE, 2),
                "w": round(w * SCALE, 2), "h": round(h * SCALE, 2),
                "image": os.path.abspath(os.path.join(HERE, src))})
            return
        if node.tag == "svg":
            emit_svg(node, x, y, out)
            return
        if node.tag == "FAIcon":
            emit_icon(node, x, y, w, h, out)
            return
        if node.tag == "Text":
            content = node.text
            if not content:
                return
            color = None
            bgimg = st.get("backgroundImage")
            if st.get("backgroundClip") == "text" and bgimg:
                color = mid_color(gradient_stops(bgimg)) or "FFFFFF"
            else:
                pc = resolve_fill(st.get("color"))
                color = pc[0] if pc else None
            spec = {"x": round(x * SCALE, 2), "y": round(y * SCALE, 2),
                    "w": round(w * SCALE, 2), "h": round(h * SCALE, 2),
                    "text": content, "font_size": max(8, round(float(st.get("fontSize", 16)) * SCALE)),
                    "font_name": "Microsoft YaHei"}
            if color:
                spec["font_color"] = color
            if st.get("fontWeight") in ("bold", "600", "700"):
                spec["_bold"] = True
            ls = st.get("letterSpacing")
            if isinstance(ls, (int, float)) and ls:
                spec["_ls"] = round(ls * SCALE, 2)
            if st.get("textAlign") == "center" and w:
                nw = text_natural_width(content.replace("\n", ""), float(st.get("fontSize", 16)))
                if nw < w:
                    spec["x"] = round((x + (w - nw) / 2) * SCALE, 2)
                    spec["w"] = round((nw + float(st.get("fontSize", 16)) * 0.6) * SCALE, 2)
            out["texts"].append(spec)
            return
        if node.tag == "Box":
            fill = resolve_fill(st.get("background"))
            border = parse_border(st.get("border"))
            shape = None
            if fill or border:
                shape = {"x": round(x * SCALE, 2), "y": round(y * SCALE, 2),
                         "w": round(w * SCALE, 2), "h": round(h * SCALE, 2)}
                r = st.get("borderRadius")
                if isinstance(r, (int, float)) and r > 0 and w and h:
                    if r >= 0.5 * min(w, h):
                        shape["shape_type"] = "ellipse"
                    elif r >= 6:
                        shape["shape_type"] = "roundRect"
                        shape["_corner"] = min(50000, round(r / min(w, h) * 100000))
                if fill:
                    shape["fill_color"] = fill[0]
                    if fill[1]:
                        shape["fill_alpha"] = fill[1]
                if border:
                    shape["border_color"] = border[0]
                    shape["border_width"] = round(border[1] * SCALE, 2)
                out["shapes"].append(shape)
            if st.get("borderBottom"):
                m = re.search(r"([\d.]+)px\s+solid\s+(\S+)", str(st.get("borderBottom")))
                if m:
                    lw = float(m.group(1))
                    lc = parse_color(m.group(2))
                    if lc:
                        out["shapes"].append({
                            "x": round(x * SCALE, 2),
                            "y": round((y + h - lw / 2) * SCALE, 2),
                            "w": round(w * SCALE, 2),
                            "h": round(max(lw * SCALE, 0.75), 2),
                            "fill_color": lc[0]})
        for k, c in node.children:
            if k == "node":
                walk(c)

    walk(slide)
    return out

def strip_private(spec):
    return {k: v for k, v in spec.items() if not k.startswith("_")}

# ---------------------------------------------------------------- 引擎执行

def execute_page(ops, file_id, page_index):
    shape_ids = []
    if ops["shapes"]:
        res = call("slide_add_shapes", file_id=file_id, page_index=page_index,
                   shapes=[strip_private(s) for s in ops["shapes"]])
        shape_ids.extend(res.get("shape_ids") or res.get("shapeIds") or [])
    if ops["texts"]:
        res = call("slide_add_texts", file_id=file_id, page_index=page_index,
                   texts=[strip_private(t) for t in ops["texts"]])
        shape_ids.extend(res.get("shape_ids") or res.get("shapeIds") or [])
    for img in ops["images"]:
        call("slide_add_image", file_id=file_id, page_index=page_index, **img)
    for ln in ops["lines"]:
        call("slide_add_line_shape", file_id=file_id, page_index=page_index, **ln)
    post_sp = []
    for i, sh in enumerate(ops["shapes"]):
        if i >= len(shape_ids):
            break
        props = {"shape_id": shape_ids[i]}
        changed = False
        if sh.get("_corner") is not None:
            props["corner_radius"] = sh["_corner"]; changed = True
        if sh.get("_rotation"):
            props["rotation"] = sh["_rotation"]; changed = True
        if changed:
            post_sp.append(props)
    text_ids = shape_ids[len(ops["shapes"]):]
    post_tx = []
    for i, tx in enumerate(ops["texts"]):
        if i >= len(text_ids):
            break
        props = {"shape_id": text_ids[i], "index": 0, "count": -1}
        changed = False
        if tx.get("_bold"):
            props["bold"] = True; changed = True
        if tx.get("_ls"):
            props["letter_spacing"] = tx["_ls"]; changed = True
        if changed:
            post_tx.append(props)
    if post_sp:
        call("slide_set_shape_properties", file_id=file_id,
             page_index=page_index, shapes=post_sp)
    if post_tx:
        call("slide_set_text_property", file_id=file_id,
             page_index=page_index, shapes=post_tx)
    return (len(ops["shapes"]) + len(ops["texts"])
            + len(ops["images"]) + len(ops["lines"]))

# ---------------------------------------------------------------- 主流程

def main():
    global edsdk
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pages", nargs="*", type=int, default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "AI自媒体内容创作助手项目汇报.pptx"))
    args = ap.parse_args()

    slide_files = sorted(f for f in os.listdir(os.path.join(HERE, "slides"))
                         if re.match(r"\d{2}\.slide$", f))
    if args.pages:
        slide_files = [f for f in slide_files if int(f[:2]) in args.pages]

    pages = []
    for f in slide_files:
        s = parse_slide(os.path.join(HERE, "slides", f))
        layout_slide(s)
        ops = compile_page(s)
        pages.append((f, ops))
        print(f"[compile] {f}: shapes={len(ops['shapes'])} "
              f"texts={len(ops['texts'])} images={len(ops['images'])} "
              f"lines={len(ops['lines'])}")

    if args.dry_run:
        with open(os.path.join(HERE, "ops_dump.json"), "w", encoding="utf-8") as fh:
            json.dump({f: ops for f, ops in pages}, fh, ensure_ascii=False, indent=1)
        print("dry-run 完成 → ops_dump.json")
        return

    edsdk = load_edsdk()
    res = call("create_slide")
    file_id = res.get("file_id")
    file_path = res.get("file_path")
    print("create_slide →", json.dumps(res, ensure_ascii=False)[:200])
    if not file_path:
        # 从 _raw 文本里再捞一次 file_path
        m = re.search(r"file_path=([^,]+)", res.get("_raw", ""))
        file_path = m.group(1).strip() if m else None
    op = call("open_file", file_path=file_path, file_type="slide", wait=True)
    print("open_file →", json.dumps(op, ensure_ascii=False)[:200])
    info = call("slide_get_info", file_id=file_id)
    print("slide_get_info →", json.dumps(info, ensure_ascii=False)[:300])
    count = info.get("slide_count", 0)
    if count >= 1:
        pi = call("slide_get_page_info", file_id=file_id, page_index=0)
        shapes = pi.get("shapes") or []
        ids = [s.get("id") or s.get("shape_id") for s in shapes]
        ids = [i for i in ids if i]
        if ids:
            call("slide_remove_shapes", file_id=file_id,
                 page_index=0, shape_ids=ids)
            print(f"清理 page0 预置占位符 {len(ids)} 个")
        need = len(pages) - count
        if need > 0:
            r = call("slide_add_slides", file_id=file_id, count=need)
            print("slide_add_slides →", json.dumps(r, ensure_ascii=False)[:120])
    else:
        r = call("slide_add_slides", file_id=file_id, count=len(pages))
        print("slide_add_slides →", json.dumps(r, ensure_ascii=False)[:120])

    for i, (f, ops) in enumerate(pages):
        n = execute_page(ops, file_id, i)
        print(f"[page {i + 1:02d}] {f} → {n} 元素")

    res = call("save_file", file_id=file_id, file_path=os.path.abspath(args.out))
    print("save_file →", json.dumps(res, ensure_ascii=False)[:200])
    call("close_file", file_id=file_id)
    print("完成 →", args.out)

if __name__ == "__main__":
    main()
