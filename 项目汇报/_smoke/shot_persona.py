# -*- coding: utf-8 -*-
"""人设页取证：验证种子人设展示名已从占位说明改为真人设名（一格观察）。

断言：① 输入框回显 = 期望展示名；② 定位/口吻/领域一并回填；
      ③ 页面无错误态文案；④ 截图 PNG 尺寸 = 1440x900。
用法：python 项目汇报/_smoke/shot_persona.py
"""
import asyncio
import base64
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

import websockets

PORT = 9338
BASE = os.environ.get("SMOKE_BASE", "http://localhost:5173")
USER = "demo_seed"
PWD = "Demo@2026"
EXPECT_NAME = "一格观察"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shots_persona")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
ERR_PAT = ["加载失败", "保存失败", "请求失败", "Not Found", "Failed to fetch", "Uncaught"]


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("未找到 Edge/Chrome")


def targets(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
        return json.load(r)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    return struct.unpack(">II", head[16:24])


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0

    async def cmd(self, method, **params):
        self.i += 1
        mid = self.i
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    async def js(self, expr, await_promise=False):
        r = await self.cmd("Runtime.evaluate", expression=expr,
                           returnByValue=True, awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    async def shot(self, name):
        r = await self.cmd("Page.captureScreenshot", format="png")
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path, png_size(path)


async def wait_for(fn, tries=40, delay=0.5):
    for _ in range(tries):
        v = await fn()
        if v:
            return v
        await asyncio.sleep(delay)
    return await fn()


def kill_edge(profile):
    """只杀自己启动的实例（按命令行里的 profile 路径精确匹配）"""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
          "'*%s*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % profile)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    os.makedirs(OUT, exist_ok=True)
    profile = os.path.join(OUT, "_profile")
    proc = subprocess.Popen([
        find_edge(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
        "--window-size=1440,900", f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(60):
        try:
            targets(PORT)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise SystemExit("Edge 调试端口未就绪")

    ok_all = True
    try:
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets(PORT) if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            c = CDP(ws)
            for m in ("Page.enable", "Runtime.enable"):
                await c.cmd(m)
            await c.cmd("Emulation.setDeviceMetricsOverride",
                        width=1440, height=900, deviceScaleFactor=1, mobile=False)

            await c.cmd("Page.navigate", url=f"{BASE}/")
            await asyncio.sleep(2.0)

            login = """
            (async () => {
              const r = await fetch('/api/auth/login', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({username: %r, password: %r})
              });
              const d = await r.json();
              if (!d.access_token) return 'fail';
              localStorage.setItem('token', d.access_token);
              localStorage.setItem('username', %r);
              return 'ok';
            })()
            """ % (USER, PWD, USER)
            print("[登录]", await c.js(login, True))

            await c.cmd("Page.navigate", url=f"{BASE}/persona")
            await wait_for(lambda: c.js("!!document.querySelector('#display_name')"), 40, 0.5)
            await asyncio.sleep(1.0)

            vals = await c.js("""
            (() => {
              const g = s => { const el = document.querySelector(s); return el ? el.value : null };
              const t = document.body.innerText || '';
              return {
                display_name: g('#display_name'),
                positioning: g('#positioning'),
                tone: g('#tone'),
                body: t.slice(0, 400),
                disabled: !!(document.querySelector('#display_name') || {}).disabled,
              };
            })()
            """) or {}

            def check(name, cond, detail=""):
                nonlocal ok_all
                ok_all = ok_all and bool(cond)
                print(("[OK] " if cond else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))

            check("展示名回显 = %s" % EXPECT_NAME, vals.get("display_name") == EXPECT_NAME,
                  vals.get("display_name"))
            check("定位已回填", bool((vals.get("positioning") or "").strip()), vals.get("positioning"))
            check("口吻已回填", bool((vals.get("tone") or "").strip()), vals.get("tone"))
            check("表单可编辑（非 loading 态）", vals.get("disabled") is False, vals.get("disabled"))
            body = vals.get("body") or ""
            check("页面无错误态文案", not any(p in body for p in ERR_PAT),
                  [p for p in ERR_PAT if p in body])
            check("旧占位名已消失", "示例素材归属账号" not in body)

            path, (w, h) = await c.shot("persona_seed.png")
            check("截图 1440x900", (w, h) == (1440, 900), "%dx%d" % (w, h))
            print("[截图]", path)

            rows = await c.js("""
            [...document.querySelectorAll('.ant-select-selection-item')]
              .map(e => (e.textContent || '').trim())
            """) or []
            check("领域回填 5 项", len(rows) == 5, rows)

    finally:
        try:
            proc.kill()
        except Exception:
            pass
        kill_edge(profile.replace("\\", "\\\\"))
        time.sleep(0.5)
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", profile],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\n结果：" + ("全部通过" if ok_all else "存在失败项"))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    asyncio.run(main())
