# -*- coding: utf-8 -*-
"""真实浏览器巡检：六页渲染 + 控制台错误 + 失败请求 + 三层断言。

用法：python verify_run.py [--shot-dir DIR]
前置：后端 8000、前端 http://localhost:5173 已在跑。
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

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
PORT = 9333
BASE = "http://localhost:5173"
USER = "demo_seed"
PWD = "Demo@2026"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots_verify")

PAGES = [
    ("materials", "/materials", "素材库管理"),
    ("hotspots", "/hotspots", "热点"),
    ("topics", "/topics", "选题"),
    ("generate", "/generate", "文案"),
    ("adapt", "/adapt", "平台适配"),
    ("persona", "/persona", "人设"),
]

# 出现即视为页面带错误态
ERR_MARKERS = [
    "加载失败", "请求失败", "生成出错", "Failed to fetch",
    "登录失败", "未登录", "Not Found", "Internal Server Error", "用户名或口令错误",
]


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("未找到 Edge/Chrome")


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

    async def drain(self, seconds):
        """收集事件（不阻塞：超时就退出）"""
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=0.25))
            except asyncio.TimeoutError:
                break
            if "method" in msg:
                self.events.append(msg)

    def take_events(self):
        ev, self.events = self.events, []
        return ev


async def main():
    os.makedirs(OUT, exist_ok=True)
    edge = find_edge()
    profile = os.path.join(OUT, "_profile")
    proc = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
        "--window-size=1440,900", f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等调试端口就绪
    for _ in range(60):
        try:
            targets(PORT)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise SystemExit("Edge 调试端口未就绪")

    report = {"pages": [], "console_errors": [], "failed_requests": []}
    try:
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets(PORT) if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            c = CDP(ws)
            await c.cmd("Page.enable")
            await c.cmd("Runtime.enable")
            await c.cmd("Log.enable")
            await c.cmd("Network.enable")
            await c.cmd("Emulation.setDeviceMetricsOverride",
                        width=1440, height=900, deviceScaleFactor=1, mobile=False)

            # 1) 登录页
            await c.cmd("Page.navigate", url=f"{BASE}/")
            await asyncio.sleep(3)
            shot = os.path.join(OUT, "00_login.png")
            r = await c.cmd("Page.captureScreenshot", format="png")
            open(shot, "wb").write(base64.b64decode(r["data"]))
            w, h = png_size(shot)
            txt = (await c.cmd("Runtime.evaluate", expression="document.body.innerText",
                               returnByValue=True))["result"].get("value", "")
            report["login"] = {"size": [w, h], "has_form": ("登录" in txt or "注册" in txt),
                               "text_head": txt[:80].replace("\n", " | ")}
            c.take_events()

            # 2) 用页面自己的接口登录，写 localStorage（与前端 AuthProvider 同键名）
            js = """
            (async () => {
              const r = await fetch('/api/auth/login', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({username: %r, password: %r})
              });
              const d = await r.json();
              if (d.access_token) {
                localStorage.setItem('token', d.access_token);
                localStorage.setItem('username', %r);
                return 'ok';
              }
              return 'fail:' + JSON.stringify(d);
            })()
            """ % (USER, PWD, USER)
            res = await c.cmd("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
            report["login_api"] = res["result"].get("value")

            # 3) 逐页巡检
            for key, path, marker in PAGES:
                await c.cmd("Page.navigate", url=f"{BASE}{path}")
                # 等渲染：轮询 marker 出现，最长 12s
                body = ""
                for _ in range(24):
                    await asyncio.sleep(0.5)
                    body = (await c.cmd("Runtime.evaluate",
                                        expression="document.body.innerText",
                                        returnByValue=True))["result"].get("value", "") or ""
                    if marker in body:
                        break
                shot = os.path.join(OUT, f"{key}.png")
                r = await c.cmd("Page.captureScreenshot", format="png")
                open(shot, "wb").write(base64.b64decode(r["data"]))
                w, h = png_size(shot)

                ev = c.take_events()
                errs = []
                for e in ev:
                    if e["method"] == "Runtime.exceptionThrown":
                        errs.append("EXC: " + str(e["params"]["exceptionDetails"].get("text")))
                    elif e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                        args = e["params"].get("args", [])
                        errs.append("CONSOLE: " + " ".join(str(a.get("value", a.get("description", "")))[:160] for a in args))
                    elif e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] == "error":
                        errs.append("LOG: " + e["params"]["entry"]["text"][:160])
                    elif e["method"] == "Network.responseReceived":
                        st = e["params"]["response"]["status"]
                        if st >= 400:
                            report["failed_requests"].append(
                                {"page": key, "status": st, "url": e["params"]["response"]["url"][:120]})
                hits = [m for m in ERR_MARKERS if m in body]
                rec = {
                    "page": key, "path": path, "size": [w, h],
                    "marker_ok": marker in body,
                    "error_markers": hits,
                    "console_errors": errs,
                    "text_len": len(body),
                    "sample": body[:120].replace("\n", " | "),
                }
                report["pages"].append(rec)
                report["console_errors"] += [{"page": key, "err": x} for x in errs]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    with open(os.path.join(OUT, "verify_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印结论
    print("== 登录页 ==", report.get("login"))
    print("== 登录接口 ==", report.get("login_api"))
    print()
    ok = True
    for p in report["pages"]:
        bad = (not p["marker_ok"]) or p["error_markers"] or p["console_errors"]
        ok = ok and not bad
        print(f"[{'FAIL' if bad else ' OK '}] {p['page']:<10} {p['size']} text={p['text_len']:>5} "
              f"marker={p['marker_ok']} errs={len(p['console_errors'])} "
              f"errMark={p['error_markers']}")
        if bad:
            print("      ", p["sample"])
            for e in p["console_errors"]:
                print("       -", e)
    print()
    print("== 失败请求 ==", len(report["failed_requests"]))
    for f_ in report["failed_requests"]:
        print("  ", f_)
    print()
    print("总体：", "全部通过" if ok and not report["failed_requests"] else "存在问题")


if __name__ == "__main__":
    asyncio.run(main())
