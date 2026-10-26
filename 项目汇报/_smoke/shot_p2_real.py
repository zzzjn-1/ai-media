# -*- coding: utf-8 -*-
"""P2 真后端联调取证：Mock 关闭（VITE_USE_MOCK=false），走 /api → 127.0.0.1:8000。

重点验三件事：
  ① 真实数据下展示口径是 norm_heat + 榜内名次（不再是跨源不可比的 heat）；
  ② 数据过期时 degraded 黄条出现（一期硬指标：不空屏 + 标注数据时间）；
  ③ 「用它做选题」→ 真实后端按热点直传出题，选题页带上下文。
"""
import asyncio
import base64
import json
import os
import struct
import sys
import urllib.request

import websockets

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
BASE = "http://localhost:5174"
PORT = 9222
os.makedirs(OUT, exist_ok=True)
results = []


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} 不是 PNG"
    return struct.unpack(">II", head[16:24])


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

        async def shot(name, marker):
            r = await cmd("Page.captureScreenshot", format="png")
            path = os.path.join(OUT, f"{name}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            w, h = png_size(path)
            body = await js("document.body.innerText") or ""
            bad = [x for x in ("用户名或口令错误", "Failed to fetch", "请求失败") if x in body]
            ok = marker in body and not bad and w >= 1200
            results.append({"page": name, "size": f"{w}x{h}", "marker": marker,
                            "found": marker in body, "errors": bad, "pass": ok})
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {w}x{h} marker={marker!r} errs={bad}")
            return body

        await cmd("Page.enable")
        await cmd("Runtime.enable")
        await cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                  deviceScaleFactor=1, mobile=False)

        # 登录：走真实 /api/auth/login，把 token 落到 localStorage（前端 AuthContext 读它）
        await cmd("Page.navigate", url=f"{BASE}/login")
        await asyncio.sleep(3)
        login = await js("""(async () => {
          const r = await fetch('/api/auth/login', {method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({username:'demo_seed', password:'Demo@2026'})});
          const j = await r.json();
          if (j.access_token) {
            localStorage.setItem('token', j.access_token);
            localStorage.setItem('username', 'demo_seed');
          }
          return j.ok ? 'ok' : ('fail:' + (j.msg||r.status));
        })()""")
        print("login:", login)

        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(9)     # 真后端首屏 + 建议角度惰性生成（最多 5 条一次批量）
        body = await shot("p2_real_hotspots", "为你推荐")
        print("  degraded 黄条:", "实时采集暂不可用" in body)
        print("  建议角度:", body.count("建议角度"), "| 榜内名次:", body.count("榜内第"))
        print("  是否仍展示原始热量级文本:", "热度：" in body)

        # 全部视图：命中垂类很少时，用户能在这里看到全网热榜（含「其他」）
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='全部');
          if(!b) return 'no-all'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(10)
        body_all = await shot("p2_real_all", "按来源")
        print("  全部视图含「其他」标签:", "其他" in body_all, "| 建议角度:", body_all.count("建议角度"))

        # 回推荐视图，走「用它做选题」闭环
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='为你推荐');
          if(!b) return 'no-feed'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(6)
        await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('用它做选题'));
          if(!b) return 'no-cta'; b.click(); return 'clicked'; })()""")
        await asyncio.sleep(3)
        for i in range(12):
            await asyncio.sleep(4)
            n = await js("(document.body.innerText.match(/一键选用/g)||[]).length")
            if n and n >= 3:
                break
        body2 = await shot("p2_real_topics", "来自热点")
        print("  选题条数:", body2.count("一键选用"))

    passed = all(r["pass"] for r in results)
    with open(os.path.join(OUT, "p2_real_report.json"), "w", encoding="utf-8") as f:
        json.dump({"all_pass": passed, "shots": results}, f, ensure_ascii=False, indent=2)
    print("\n汇总:", "全部通过" if passed else "存在失败")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
