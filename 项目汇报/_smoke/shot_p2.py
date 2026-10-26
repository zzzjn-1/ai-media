# -*- coding: utf-8 -*-
"""P2 二期热点看板截图取证（CDP 驱动本机 Edge，无 playwright）。

断言三层：像素尺寸 / 页面独有标记文本 / 无错误态；
另加"互异断言"（各图 md5 必须不同），防止截图区域算错时画面看似有内容。
"""
import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.request

import websockets

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
BASE = "http://localhost:5173"
PORT = 9222
os.makedirs(OUT, exist_ok=True)

results = []


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} 不是 PNG"
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
        return json.load(r)


async def main():
    ws_url = next(t["webSocketDebuggerUrl"] for t in targets() if t["type"] == "page")
    async with websockets.connect(ws_url, max_size=None) as ws:
        seq = 0

        async def cmd(method, **params):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == seq:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        async def js(expr):
            r = await cmd("Runtime.evaluate", expression=expr,
                          returnByValue=True, awaitPromise=True)
            return r.get("result", {}).get("value")

        async def shot(name, marker, forbid=("用户名或口令错误", "Failed to fetch", "页面加载中…")):
            await asyncio.sleep(1.2)
            r = await cmd("Page.captureScreenshot", format="png")
            path = os.path.join(OUT, f"{name}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            w, h = png_size(path)
            body = await js("document.body.innerText") or ""
            errs = [x for x in forbid if x in body]
            ok = (marker in body) and not errs and w >= 1200
            results.append({"page": name, "size": f"{w}x{h}", "marker": marker,
                            "marker_found": marker in body, "errors": errs, "pass": ok})
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {w}x{h} marker={marker!r} errs={errs}")
            return path, body

        await cmd("Page.enable")
        await cmd("Runtime.enable")
        await cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                  deviceScaleFactor=1, mobile=False)

        # 登录态在 localStorage（前端 AuthContext 读取），先在同源写入再进受保护路由
        await cmd("Page.navigate", url=f"{BASE}/login")
        await asyncio.sleep(2.5)
        await js("localStorage.setItem('token','mock-token');"
                 "localStorage.setItem('username','demo_seed');'ok'")

        # 1) 推荐视图（默认）
        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(3.5)
        _, body = await shot("p2_hotspots_feed", "为你推荐")
        print("  建议角度出现次数:", body.count("建议角度"))
        print("  '新' 标记:", "新" in body, "| 演示标注:", "演示数据" in body)
        print("  首屏有无'采集热点'按钮:", "采集热点" in body)

        # 2) 全部视图（应出现「其他」入口）
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='全部');
          if(!b) return 'no-all'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(2.0)
        _, body2 = await shot("p2_hotspots_all", "按来源")
        print("  有「其他」入口:", "其他" in body2)

        # 3) 按来源视图 + 微博
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='按来源');
          if(!b) return 'no-src'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(1.8)
        _, body3 = await shot("p2_hotspots_source", "按来源")
        print("  来源筛选条出现:", body3.count("微博") > 0 and body3.count("知乎") > 0)

        # 4) 用它做选题 → 选题页带上下文并自动出题
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('用它做选题'));
          if(!b) return 'no-cta'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(3.2)
        _, body4 = await shot("p2_topics_from_hotspot", "来自热点")
        print("  选题条数:", body4.count("一键选用"), "| 清除入口:", "清除" in body4)

        # 5) 回看板：Mock 也要记得「已用过」（P2-7），否则离线演示看不到这个能力
        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(3.0)
        _, body5 = await shot("p2_hotspots_adopted", "为你推荐")
        adopted_ok = "已用过" in body5 and "再写一版" in body5
        print("  已用过标记:", "已用过" in body5, "| CTA 变「再写一版」:", "再写一版" in body5)
        if not adopted_ok:
            results.append({"page": "p2_hotspots_adopted", "size": "-",
                            "marker": "已用过/再写一版", "marker_found": False,
                            "errors": ["Mock 未记录采纳"], "pass": False})

        # 互异断言
        md5s = {}
        for n in ("p2_hotspots_feed", "p2_hotspots_all", "p2_hotspots_source",
                  "p2_topics_from_hotspot", "p2_hotspots_adopted"):
            p = os.path.join(OUT, f"{n}.png")
            md5s[n] = hashlib.md5(open(p, "rb").read()).hexdigest()
        dup = len(set(md5s.values())) != len(md5s)
        print("互异断言:", "FAIL 有重复" if dup else "PASS 五张互不相同")

    passed = all(r["pass"] for r in results) and not dup
    report = {"all_pass": passed, "shots": results, "md5_unique": not dup}
    with open(os.path.join(OUT, "p2_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n汇总:", "全部通过" if passed else "存在失败")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
