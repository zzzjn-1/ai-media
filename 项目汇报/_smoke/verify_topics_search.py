# -*- coding: utf-8 -*-
"""选题搜索框 + 空态 + 清空还原（只读，不跑 LLM）。

用法：python verify_topics_search.py
"""
import asyncio
import base64
import json
import os
import shutil
import struct
import subprocess
import time
import urllib.request

import websockets

EDGE = next((p for p in [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
] if os.path.exists(p)), None)
PORT = 9337
BASE = os.environ.get("SMOKE_BASE", "http://localhost:5173")
MOCK = os.environ.get("SMOKE_MOCK") == "1"
KW = os.environ.get("SMOKE_KW", "折叠屏")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots_topics")

CARD_JS = """(()=>[...document.querySelectorAll('button')].filter(b=>/一键选用|再次创作/.test(b.textContent||'')).length)()"""
TOTAL_JS = """(()=>{const m=(document.body.innerText||'').match(/共\\s*(\\d+)\\s*条/);return m?Number(m[1]):null})()"""
BODY_JS = "(document.body.innerText||'')"

TYPE_SEARCH = """
(async (kw) => {
  const inp = document.querySelector('input[placeholder*="搜索"]');
  if (!inp) return 'no-input';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(inp, kw);
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  await new Promise(r => setTimeout(r, 120));
  const btn = document.querySelector('.ant-input-search-button');
  if (!btn) return 'no-button';
  btn.click();
  return 'ok';
})(%s)
"""


def png(path):
    with open(path, "rb") as f:
        head = f.read(33)
    return struct.unpack(">II", head[16:24])


class CDP:
    def __init__(self, ws):
        self.ws, self.i, self.events = ws, 0, []

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

    async def js(self, expr, await_promise=False):
        r = await self.cmd("Runtime.evaluate", expression=expr,
                           returnByValue=True, awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    async def shot(self, name):
        r = await self.cmd("Page.captureScreenshot", format="png")
        p = os.path.join(OUT, name)
        with open(p, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return p, *png(p)


async def main():
    os.makedirs(OUT, exist_ok=True)
    profile = os.path.join(OUT, "_profile2")
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
                             "--window-size=1440,900", f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                json.load(r)
            break
        except Exception:
            time.sleep(0.5)

    checks, errs = [], []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        print(("[OK] " if ok else "[FAIL] ") + name + ("  " + str(detail) if detail else ""))

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
            ws_url = next(t["webSocketDebuggerUrl"] for t in json.load(r) if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            c = CDP(ws)
            for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
                await c.cmd(m)
            await c.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                        deviceScaleFactor=1, mobile=False)
            await c.cmd("Page.navigate", url=f"{BASE}/")
            await asyncio.sleep(2.5)
            login = """
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
            if MOCK:
                # Mock 模式不校验 token；直接写入本地存储，避免 login 请求被代理打到真后端
                await c.js("localStorage.setItem('token','mock-token');"
                           "localStorage.setItem('username','demo_seed');'ok'")
                check("登录（Mock 模式）", True, "本地写入 token")
            else:
                check("登录", (await c.js(login, True)) == "ok")

            await c.cmd("Page.navigate", url=f"{BASE}/topics")
            base_n, base_total = 0, None
            for _ in range(30):
                await asyncio.sleep(0.4)
                base_n = await c.js(CARD_JS) or 0
                if base_n:
                    break
            base_total = await c.js(TOTAL_JS)
            check("初始列表", base_n > 0 and base_total, f"{base_n} 张 / 共 {base_total} 条")
            c.events.clear()

            # 1) 关键词「折叠屏」→ 结果子集
            r = await c.js(TYPE_SEARCH % json.dumps(KW), True)
            await asyncio.sleep(1.5)
            n1, t1 = await c.js(CARD_JS) or 0, await c.js(TOTAL_JS)
            body = await c.js(BODY_JS) or ""
            await c.shot("07_search_hit.png")
            check("搜索框可用", r == "ok", r)
            check("搜索缩小结果集", t1 is not None and t1 < base_total, f"{base_total} → {t1}")
            check("命中文字出现在结果里", KW in body, f"卡片 {n1} 张")

            # 2) 无命中 → 空态（不是空白页）
            r = await c.js(TYPE_SEARCH % json.dumps("zzz不存在的关键词zzz"), True)
            await asyncio.sleep(1.5)
            t2 = await c.js(TOTAL_JS)
            body = await c.js(BODY_JS) or ""
            await c.shot("08_search_empty.png")
            check("无命中显示空态", "没有符合条件的选题" in body, f"共 {t2} 条")
            check("空态不显示卡片", (await c.js(CARD_JS) or 0) == 0)

            # 3) 清空还原
            await c.js(TYPE_SEARCH % json.dumps(""), True)
            await asyncio.sleep(1.5)
            n3, t3 = await c.js(CARD_JS) or 0, await c.js(TOTAL_JS)
            await c.shot("09_search_cleared.png")
            check("清空搜索还原全量", t3 == base_total and n3 == base_n, f"{n3} 张 / 共 {t3} 条")

            # 4) Mock 专属：生成后必须落进历史列表（否则「列表」和「生成」两套状态会分叉）
            if MOCK:
                await c.js("(()=>{const b=[...document.querySelectorAll('button')]"
                           ".find(x=>(x.textContent||'').includes('生成选题'));"
                           "if(b){b.click();return 'ok'}return 'no'})()")
                t4, n4 = None, 0
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    t4, n4 = await c.js(TOTAL_JS), await c.js(CARD_JS) or 0
                    if t4 and t4 > base_total:
                        break
                body = await c.js(BODY_JS) or ""
                await c.shot("10_mock_generate.png")
                check("Mock 生成后列表变长", (t4 or 0) > base_total, f"{base_total} → {t4}")
                check("新卡片带「新生成」标记", "新生成" in body, f"卡片 {n4} 张")

            ev, c.events = c.events, []
            for e in ev:
                if e["method"] == "Runtime.exceptionThrown":
                    errs.append("EXC: " + str(e["params"]["exceptionDetails"].get("text")))
                elif e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] == "error":
                    errs.append("LOG: " + e["params"]["entry"]["text"][:160])
                elif e["method"] == "Network.responseReceived":
                    st = e["params"]["response"]["status"]
                    if st >= 400:
                        errs.append(f"HTTP {st} {e['params']['response']['url'][:100]}")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(0.6)
        os.system(f'taskkill /F /FI "CommandLine like \'%_profile2\'" >nul 2>&1')
        shutil.rmtree(profile, ignore_errors=True)

    bad = [x for x in checks if not x["ok"]]
    print(f"\n===== {len(checks) - len(bad)}/{len(checks)} 项通过 =====")
    print("错误/失败请求:", len(errs))
    for e in errs[:6]:
        print("   ", e)
    raise SystemExit(0 if not bad and not errs else 1)


if __name__ == "__main__":
    asyncio.run(main())
