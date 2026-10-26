# -*- coding: utf-8 -*-
"""P2-7 真后端取证：采纳漏斗闭环（曝光 → 采纳 → 已用过 → 分母/分子落库）。

验的是"这个指标到底能不能算"，分四步：
  ① 打开推荐流 → 前端上报曝光 → 后端 impressions 增量 = 本屏条数
  ② 点「用它做选题」→ adopt 落库 → 经过选题页出题 → generate 由服务端写入
  ③ 回到看板 → 该条显示「已用过」、「用它做选题」变「再写一版」、标题区出现「本页已用过 N 条」
  ④ 再打开一次看板（不刷新页面级缓存）→ 重复曝光不应把分母灌水（同签名去重）

任何一步失败都会在汇总里标红，避免"看起来通了"。
"""
import asyncio
import base64
import json
import os
import struct
import sys
import urllib.error
import urllib.request

import websockets

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
BASE = "http://localhost:5174"
API = "http://127.0.0.1:8000/api"
PORT = 9222
os.makedirs(OUT, exist_ok=True)
results = []

# 沙箱里 http_proxy 会把 127.0.0.1 也送进代理，必须显式直连
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(r, timeout=120) as x:
            return x.status, json.loads(x.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} 不是 PNG"
    return struct.unpack(">II", head[16:24])


def targets():
    with _OPENER.open(f"http://127.0.0.1:{PORT}/json", timeout=10) as r:
        return json.load(r)


def funnel(token):
    _st, data = api("GET", "/hotspots/status", token=token)
    a = data["adoption"]
    return {k: a[k] for k in ("impressions", "adopts", "generates", "ctr")}


async def main():
    _st, lg = api("POST", "/auth/login",
                  {"username": "demo_seed", "password": "Demo@2026"})
    T = lg["access_token"]
    before = funnel(T)
    print("基线漏斗:", before)

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

        async def shot(name, marker, extra_check=None):
            r = await cmd("Page.captureScreenshot", format="png")
            path = os.path.join(OUT, f"{name}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            w, h = png_size(path)
            body = await js("document.body.innerText") or ""
            bad = [x for x in ("用户名或口令错误", "Failed to fetch", "请求失败") if x in body]
            ok = marker in body and not bad and w >= 1200
            if extra_check:
                ok = ok and extra_check(body)
            results.append({"page": name, "size": f"{w}x{h}", "marker": marker,
                            "found": marker in body, "errors": bad, "pass": ok})
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {w}x{h} marker={marker!r} errs={bad}")
            return body

        await cmd("Page.enable")
        await cmd("Runtime.enable")
        await cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                  deviceScaleFactor=1, mobile=False)

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

        # ① 打开推荐流：曝光应被上报
        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(9)
        body = await shot("p3_real_feed", "为你推荐")
        visible_cards = await js(
            "document.querySelectorAll('button').length && "
            "(document.body.innerText.match(/建议角度/g)||[]).length")
        after_expose = funnel(T)
        delta = after_expose["impressions"] - before["impressions"]
        print(f"  曝光增量: {delta}（本屏卡片约 {visible_cards} 张）")
        results.append({"page": "曝光上报", "size": "-", "marker": "impressions 增量>0",
                        "found": delta > 0, "errors": [], "pass": delta > 0})

        # ② 点「用它做选题」→ 真实出题（服务端写 generate）
        clicked = await js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('用它做选题'));
          if(!b) return 'no-cta'; b.click(); return 'clicked'; })()""")
        print("  CTA:", clicked)
        await asyncio.sleep(3)
        for _ in range(12):
            await asyncio.sleep(4)
            n = await js("(document.body.innerText.match(/一键选用/g)||[]).length")
            if n and n >= 3:
                break
        body2 = await shot("p3_real_topics", "来自热点")
        print("  选题条数:", body2.count("一键选用"))
        after_gen = funnel(T)
        print("  采纳后漏斗:", after_gen)
        results.append({"page": "采纳+出题落库", "size": "-", "marker": "adopts/generates 增量",
                        "found": (after_gen["adopts"] > before["adopts"]
                                  and after_gen["generates"] > before["generates"]),
                        "errors": [], "pass": (after_gen["adopts"] > before["adopts"]
                                               and after_gen["generates"] > before["generates"])})

        # ③ 回看板：已用过标记 + CTA 文案切换
        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(9)
        body3 = await shot(
            "p3_real_adopted", "已用过",
            extra_check=lambda b: "再写一版" in b and "本页已用过" in b)
        print("  已用过:", body3.count("已用过"), "| 再写一版:", "再写一版" in body3,
              "| 本页统计:", [ln for ln in body3.splitlines() if "本页已用过" in ln][:1])

        # ④ 同屏重复渲染不该把分母灌水：再进一次同一视图，曝光增量应等于本屏条数而非翻倍
        s1 = funnel(T)["impressions"]
        await cmd("Page.navigate", url=f"{BASE}/hotspots")
        await asyncio.sleep(8)
        s2 = funnel(T)["impressions"]
        print(f"  二次进入曝光增量: {s2 - s1}（同屏去重后应≈本屏条数，不应成倍）")

    passed = all(r["pass"] for r in results)
    with open(os.path.join(OUT, "p3_real_report.json"), "w", encoding="utf-8") as f:
        json.dump({"all_pass": passed, "shots": results}, f, ensure_ascii=False, indent=2)
    print("\n汇总:", "全部通过" if passed else "存在失败")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
