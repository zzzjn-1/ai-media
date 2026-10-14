#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mock_preview.py — 把 ops_dump.json 渲染成 HTML 版式镜像，供浏览器截图目检。

与送入引擎的 ops 完全同源；pt → px 用 /0.75 还原 1280×720 画布。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))

def page_html(name, ops):
    W, H = 1280, 720
    k = 1 / 0.75  # pt → px
    parts = [f'<div class="pg" id="pg-{name[:2]}" style="width:{W}px;height:{H}px">']
    svg_lines = []
    for sh in ops["shapes"]:
        x, y = sh["x"] * k, sh["y"] * k
        w, h = sh["w"] * k, sh["h"] * k
        css = [f"left:{x:.1f}px", f"top:{y:.1f}px",
               f"width:{w:.1f}px", f"height:{h:.1f}px", "position:absolute"]
        st = sh.get("shape_type")
        if st == "ellipse":
            css.append("border-radius:50%")
        elif st == "roundRect":
            cr = sh.get("_corner", 0) / 100000 * min(w, h)
            css.append(f"border-radius:{cr:.1f}px")
        if sh.get("fill_color"):
            op = 1 - sh.get("fill_alpha", 0) / 100
            css.append(f"background:rgba({int(sh['fill_color'][0:2],16)},"
                       f"{int(sh['fill_color'][2:4],16)},{int(sh['fill_color'][4:6],16)},{op:.2f})")
        if sh.get("border_color"):
            css.append(f"border:{max(sh.get('border_width',1)*k,0.5):.1f}px solid #{sh['border_color']}")
        rot = sh.get("_rotation")
        if rot:
            css.append(f"transform:rotate({rot}deg)")
        parts.append(f'<div style="{";".join(css)}"></div>')
    for ln in ops["lines"]:
        x1, y1 = ln["x1"] * k, ln["y1"] * k
        x2, y2 = ln["x2"] * k, ln["y2"] * k
        dash = ' stroke-dasharray="5 5"' if ln.get("dash") == "dash" else ""
        arrow = ' marker-end="url(#ar)"' if ln.get("line_type") == "arrow" else ""
        svg_lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#{ln["color"]}" stroke-width="{max(ln["w"]*k,0.5):.1f}"{dash}{arrow}/>')
    for img in ops["images"]:
        x, y = img["x"] * k, img["y"] * k
        w, h = img["w"] * k, img["h"] * k
        src = "file:///" + img["image"].replace("\\", "/")
        parts.append(f'<img src="{esc(src)}" style="position:absolute;'
                     f'left:{x:.1f}px;top:{y:.1f}px;width:{w:.1f}px;height:{h:.1f}px;'
                     f'object-fit:cover"/>')
    for tx in ops["texts"]:
        x, y = tx["x"] * k, tx["y"] * k
        w, h = tx["w"] * k, tx["h"] * k
        fs = tx["font_size"] * k
        css = [f"left:{x:.1f}px", f"top:{y:.1f}px",
               f"width:{w:.1f}px", f"height:{h:.1f}px", "position:absolute",
               f"font-size:{fs:.1f}px", "line-height:1.25",
               "font-family:'Microsoft YaHei','Source Han Sans SC',sans-serif",
               "overflow:visible", "white-space:pre"]
        if tx.get("font_color"):
            css.append(f"color:#{tx['font_color']}")
        if tx.get("_bold"):
            css.append("font-weight:bold")
        if tx.get("_ls"):
            css.append(f"letter-spacing:{tx['_ls']*k:.1f}px")
        text = esc(tx["text"])
        parts.append(f'<div style="{";".join(css)}">{text}</div>')
    parts.append("</div>")
    svg = ""
    if svg_lines:
        svg = (f'<svg style="position:absolute;left:0;top:0" width="{W}" height="{H}">'
               f'<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="4" '
               f'orient="auto"><path d="M0 0 L 8 4 L 0 8 z" fill="#3B82F6"/></marker></defs>'
               + "".join(svg_lines) + "</svg>")
        parts.insert(1, svg)
    return "".join(parts)

def main():
    dump = json.load(open(os.path.join(HERE, "ops_dump.json"), encoding="utf-8"))
    pages = "".join(page_html(name, ops) for name, ops in sorted(dump.items()))
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ background:#CBD5E1; margin:0; padding:20px; font-family:'Microsoft YaHei',sans-serif; }}
.pg {{ position:relative; overflow:hidden; margin:0 auto 24px; background:#fff;
       box-shadow:0 2px 12px rgba(0,0,0,.25); }}
</style></head><body>{pages}</body></html>"""
    out = os.path.join(HERE, "mock_preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("mock →", out, f"({len(dump)} pages)")

if __name__ == "__main__":
    main()
