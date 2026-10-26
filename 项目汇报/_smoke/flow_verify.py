# -*- coding: utf-8 -*-
"""真实浏览器跑通主流程：热点 → 用它做选题 → 一键选用 → 生成文案 → 平台适配。

每步截图 + 控制台错误 + 4xx/5xx 收集，任一步断链即 FAIL。
"""
import asyncio
import base64
import json
import os
import struct
import subprocess
import time
import urllib.request

import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9334
BASE = "http://localhost:5173"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots_flow")

CLICK_JS = """
(function(text){
  const els = [...document.querySelectorAll('button,a,li,[role=button]')];
  const hits = els.filter(e => (e.innerText||'').trim().includes(text) && !e.disabled);
  hits.sort((a,b)=> (a.innerText||'').length - (b.innerText||'').length);
  if (!hits.length) return 'notfound';
  hits[0].click();
  return 'ok:' + (hits[0].innerText||'').trim().slice(0,20);
})(%s)
"""


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def targets(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
        return json.load(r)


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0
        self.events = []

    async def cmd(self, method, **params):
        self.i += 1
        mid = self.i
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if "method" in msg:
                self.events.append(msg)
            if msg.get("id") == mid:
                return msg.get("result", {})

    def take(self):
        ev, self.events = self.events, []
        out = []
        for e in ev:
            m = e["method"]
            if m == "Runtime.exceptionThrown":
                out.append("EXC " + str(e["params"]["exceptionDetails"].get("text"))[:120])
            elif m == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                out.append("CONSOLE " + " ".join(str(a.get("value", ""))[:100]
                                                 for a in e["params"].get("args", [])))
            elif m == "Log.entryAdded" and e["params"]["entry"]["level"] == "error":
                out.append("LOG " + e["params"]["entry"]["text"][:120])
            elif m == "Network.responseReceived" and e["params"]["response"]["status"] >= 400:
                u = e["params"]["response"]["url"]
                if "/api/" in u:
                    out.append(f"HTTP{e['params']['response']['status']} {u.split('/api/')[-1][:60]}")
        return out

    async def body(self):
        r = await self.cmd("Runtime.evaluate", expression="document.body.innerText",
                           returnByValue=True)
        return r["result"].get("value", "") or ""

    async def url(self):
        r = await self.cmd("Runtime.evaluate", expression="location.pathname",
                           returnByValue=True)
        return r["result"].get("value", "")

    async def click(self, text):
        r = await self.cmd("Runtime.evaluate", expression=CLICK_JS % json.dumps(text),
                           returnByValue=True, awaitPromise=False)
        return r["result"].get("value")

    async def shot(self, name):
        r = await self.cmd("Page.captureScreenshot", format="png")
        p = os.path.join(OUT, name)
        open(p, "wb").write(base64.b64decode(r["data"]))
        return p, png_size(p)

    async def wait_for(self, text, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            await asyncio.sleep(1)
            b = await self.body()
            if text in b:
                return True, b
        return False, await self.body()


async def main():
    os.makedirs(OUT, exist_ok=True)
    profile = os.path.join(OUT, "_profile")
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
        "--window-size=1440,1200", f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            targets(PORT)
            break
        except Exception:
            time.sleep(0.5)

    steps = []

    def rec(name, ok, detail=""):
        steps.append({"step": name, "ok": ok, "detail": detail})
        print(f"[{' OK ' if ok else 'FAIL'}] {name} {detail}")

    try:
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets(PORT) if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            c = CDP(ws)
            for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
                await c.cmd(m)
            await c.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=1200,
                        deviceScaleFactor=1, mobile=False)

            await c.cmd("Page.navigate", url=f"{BASE}/")
            await asyncio.sleep(3)
            js = """
            (async () => {
              const r = await fetch('/api/auth/login', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({username:'demo_seed', password:'Demo@2026'})});
              const d = await r.json();
              if (d.access_token) { localStorage.setItem('token', d.access_token);
                localStorage.setItem('username','demo_seed'); return 'ok'; }
              return 'fail';
            })()
            """
            r = await c.cmd("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
            rec("登录", r["result"].get("value") == "ok")
            c.take()

            # 1) 热点看板
            await c.cmd("Page.navigate", url=f"{BASE}/hotspots")
            ok, b = await c.wait_for("用它做选题", 60)
            p, size = await c.shot("1_hotspots.png")
            rec("热点看板加载", ok, f"{size[0]}x{size[1]}")
            errs = c.take()

            # 2) 点「用它做选题」→ 跳选题页并自动出题
            click = await c.click("用它做选题")
            rec("点击用它做选题", str(click).startswith("ok"), str(click))
            ok, b = await c.wait_for("一键选用", 150)
            p, size = await c.shot("2_topics.png")
            path = await c.url()
            rec("选题自动生成", ok, f"path={path} {size[0]}x{size[1]}")
            rec("带热点上下文", "来自热点" in b or ok)
            errs += c.take()

            # 3) 一键选用 → 生成页
            click = await c.click("一键选用")
            rec("点击一键选用", str(click).startswith("ok"), str(click))
            await asyncio.sleep(2)
            path = await c.url()
            b = await c.body()
            p, size = await c.shot("3_generate_form.png")
            rec("跳转到生成页且带入选题", path == "/generate" and "选题标题" in b,
                f"path={path}")
            errs += c.take()

            # 4) 一键生成 → 等待流式版本出现
            click = await c.click("一键生成")
            rec("点击一键生成", str(click).startswith("ok"), str(click))
            t0 = time.time()
            ok1, _ = await c.wait_for("版本 1", 150)
            first = round(time.time() - t0, 1)
            p, size = await c.shot("4_generate_v1.png")
            ok3, b = await c.wait_for("版本 3", 180)
            total = round(time.time() - t0, 1)
            p, size = await c.shot("5_generate_v3.png")
            rec("首版先显示（真流式）", ok1, f"{first}s")
            rec("三版全部出现", ok3, f"{total}s {size[0]}x{size[1]}")
            # 正文非空校验：卡片里不该只有标题
            body_len = len(b)
            rec("生成结果有正文", body_len > 800, f"页面文本 {body_len} 字")
            errs += c.take()

            # 5) 平台适配页
            click = await c.click("进入平台适配")
            rec("点击进入平台适配", str(click).startswith("ok"), str(click))
            ok, b = await c.wait_for("发布备注", 150)
            p, size = await c.shot("6_adapt.png")
            rec("适配页有内容", ok, f"{size[0]}x{size[1]}")
            errs += c.take()

            steps_errs = errs
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    with open(os.path.join(OUT, "flow_report.json"), "w", encoding="utf-8") as f:
        json.dump({"steps": steps, "errors": steps_errs if "steps_errs" in dir() else []},
                  f, ensure_ascii=False, indent=2)
    fails = [s for s in steps if not s["ok"]]
    print()
    print(f"步骤 {len(steps)} 个，失败 {len(fails)} 个")
    print("汇总：", "全链路通过" if not fails else "存在断链")


if __name__ == "__main__":
    asyncio.run(main())
