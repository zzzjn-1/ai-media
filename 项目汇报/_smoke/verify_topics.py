# -*- coding: utf-8 -*-
"""选题历史列表的浏览器取证：刷新不丢 + 状态筛选 + 分页 + 选用后 topic_id 落库。

用法：python verify_topics.py
前置：后端 8000、前端 http://localhost:5173 已在跑。
"""
import asyncio
import base64
import json
import os
import sqlite3
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
PORT = 9335
BASE = "http://localhost:5173"
USER = "demo_seed"
PWD = "Demo@2026"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shots_topics")
DB = os.path.abspath(os.path.join(HERE, "..", "..", "db", "app.db"))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    return struct.unpack(">II", head[16:24])


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

    async def js(self, expr, await_promise=False):
        r = await self.cmd("Runtime.evaluate", expression=expr,
                           returnByValue=True, awaitPromise=await_promise)
        return r.get("result", {}).get("value")

    async def shot(self, name):
        r = await self.cmd("Page.captureScreenshot", format="png")
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        w, h = png_size(path)
        return path, w, h

    def take_events(self):
        ev, self.events = self.events, []
        return ev


CARD_COUNT_JS = """
(() => {
  const btns = [...document.querySelectorAll('button')]
    .filter(b => /一键选用|再次创作/.test(b.textContent || ''));
  return btns.length;
})()
"""

TOTAL_JS = """
(() => {
  const m = (document.body.innerText || '').match(/共\\s*(\\d+)\\s*条/);
  return m ? Number(m[1]) : null;
})()
"""

CLICK_TEXT_JS = """
(() => {
  const btns = [...document.querySelectorAll('button')]
    .filter(b => (b.textContent || '').includes(%s));
  if (!btns.length) return 'not-found';
  btns[0].click();
  return 'clicked:' + (btns[0].textContent || '').trim().slice(0, 20);
})()
"""


async def main():
    os.makedirs(OUT, exist_ok=True)
    edge = find_edge()
    profile = os.path.join(OUT, "_profile")
    proc = subprocess.Popen([
        edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
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

    report = {"checks": [], "console_errors": [], "failed_requests": []}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
        print(("[OK] " if ok else "[FAIL] ") + name + ("  " + str(detail) if detail else ""))

    try:
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets(PORT) if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            c = CDP(ws)
            for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
                await c.cmd(m)
            await c.cmd("Emulation.setDeviceMetricsOverride",
                        width=1440, height=900, deviceScaleFactor=1, mobile=False)

            await c.cmd("Page.navigate", url=f"{BASE}/")
            await asyncio.sleep(2.5)
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
              return 'fail';
            })()
            """ % (USER, PWD, USER)
            check("登录并写入 token", (await c.js(js, True)) == "ok")
            c.take_events()

            # ---- 1) 首屏：历史列表 ----
            await c.cmd("Page.navigate", url=f"{BASE}/topics")
            n = 0
            for _ in range(30):
                await asyncio.sleep(0.4)
                n = await c.js(CARD_COUNT_JS) or 0
                if n:
                    break
            total = await c.js(TOTAL_JS)
            await c.shot("01_list.png")
            check("首屏渲染历史选题卡片", n > 0, f"卡片 {n} 张")
            check("显示服务端总数", bool(total), f"共 {total} 条")
            check("首屏分页条数=12", n == 12, f"实际 {n}")

            cards_before = await c.js(
                "[...document.querySelectorAll('.group .font-semibold')].map(e=>e.textContent.trim()).slice(0,3)")
            check("读到卡片标题样本", bool(cards_before), str(cards_before)[:80])

            # ---- 2) 刷新后仍在（本次改造的核心） ----
            await c.cmd("Page.reload")
            n2 = 0
            for _ in range(30):
                await asyncio.sleep(0.4)
                n2 = await c.js(CARD_COUNT_JS) or 0
                if n2:
                    break
            cards_after = await c.js(
                "[...document.querySelectorAll('.group .font-semibold')].map(e=>e.textContent.trim()).slice(0,3)")
            await c.shot("02_after_reload.png")
            check("刷新后列表仍在", n2 == n, f"刷新前 {n} → 刷新后 {n2}")
            check("刷新后顺序一致", cards_before == cards_after, str(cards_after)[:80])

            # ---- 3) 筛选「已选用」 ----
            r = await c.js(CLICK_TEXT_JS % json.dumps("已选用"))
            await asyncio.sleep(1.4)
            n3 = await c.js(CARD_COUNT_JS) or 0
            tags = await c.js(
                "(()=>{const b=document.body.innerText||'';return (b.match(/已选用/g)||[]).length})()")
            await c.shot("03_filter_selected.png")
            check("点击「已选用」筛选生效", r.startswith("clicked"), r)
            check("筛选结果全部为已选用", n3 > 0 and tags >= n3, f"卡片 {n3}，标签出现 {tags} 次")

            # ---- 4) 回到全部 + 加载更多 ----
            await c.js(CLICK_TEXT_JS % json.dumps("全部"))
            await asyncio.sleep(1.2)
            n4 = await c.js(CARD_COUNT_JS) or 0
            r = await c.js(CLICK_TEXT_JS % json.dumps("加载更多"))
            await asyncio.sleep(1.6)
            n5 = await c.js(CARD_COUNT_JS) or 0
            await c.shot("04_load_more.png")
            check("加载更多追加成功", r.startswith("clicked") and n5 > n4, f"{n4} → {n5}")

            # ---- 5) 选用第一条 → 生成页带入标题，且 copy_tasks.topic_id 落库 ----
            # 注意：已选用的卡片按钮文案是「再次创作」，只认「一键选用」会取到第二张卡 →
            # 这里从被点的那颗按钮反查它所在的卡片标题，保证断言对齐的是同一条选题
            picked = await c.js("""
            (() => {
              const btn = [...document.querySelectorAll('button')]
                .find(b => (b.textContent || '').includes('一键选用'));
              if (!btn) return {clicked: false};
              const card = btn.closest('.group');
              const el = card && card.querySelector('.font-semibold');
              const title = el ? el.textContent.trim() : '';
              btn.click();
              return {clicked: true, title};
            })()
            """)
            r = 'clicked' if picked and picked.get("clicked") else "not-found"
            title = (picked or {}).get("title", "")
            await asyncio.sleep(1.8)
            url = await c.js("location.pathname")
            val = await c.js("(()=>{const i=document.querySelector('input');return i?i.value:null})()")
            await c.shot("05_generate_prefilled.png")
            check("选用后跳到生成页", url == "/generate", url)
            check("标题已带入生成页", val == title, f"输入框={str(val)[:40]} / 卡片={str(title)[:40]}")

            r = await c.js(CLICK_TEXT_JS % json.dumps("一键生成"))
            print("   点击生成:", r)
            versions = 0
            for _ in range(120):
                await asyncio.sleep(1)
                versions = await c.js(
                    "(()=>{const t=document.body.innerText||'';return (t.match(/版本\\s*\\d/g)||[]).length})()") or 0
                done = await c.js("(document.body.innerText||'').includes('生成完成')"
                                  "|| (document.body.innerText||'').includes('耗时')")
                if versions >= 3 and done:
                    break
            await c.shot("06_generate_done.png")
            check("生成页拿到 3 个版本", versions >= 3, f"匹配 {versions} 处")

            con = sqlite3.connect(DB)
            row = con.execute(
                "SELECT id,topic_id,created_at FROM copy_tasks ORDER BY id DESC LIMIT 1").fetchone()
            tid = con.execute("SELECT id,title FROM topics WHERE title=?", (title,)).fetchone()
            con.close()
            check("最新文案任务已关联选题 id", row and row[1] is not None,
                  f"task {row[0]} → topic_id={row[1]}")
            check("关联的是同一条选题", bool(tid) and row[1] == tid[0],
                  f"topic {tid[0] if tid else None}")

            ev = c.take_events()
            for e in ev:
                if e["method"] == "Runtime.exceptionThrown":
                    report["console_errors"].append(
                        "EXC: " + str(e["params"]["exceptionDetails"].get("text")))
                elif e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] == "error":
                    report["console_errors"].append(
                        "CONSOLE: " + " ".join(str(a.get("value", a.get("description", "")))[:160]
                                               for a in e["params"].get("args", [])))
                elif e["method"] == "Log.entryAdded" and e["params"]["entry"]["level"] == "error":
                    report["console_errors"].append("LOG: " + e["params"]["entry"]["text"][:160])
                elif e["method"] == "Network.responseReceived":
                    st = e["params"]["response"]["status"]
                    if st >= 400:
                        report["failed_requests"].append(
                            {"status": st, "url": e["params"]["response"]["url"][:120]})
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(0.6)
        os.system(f'taskkill /F /FI "CommandLine like \'%{os.path.basename(OUT)}\'" >nul 2>&1')
        try:
            import shutil
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass

    ok = sum(1 for x in report["checks"] if x["ok"])
    bad = [x for x in report["checks"] if not x["ok"]]
    print(f"\n===== {ok}/{len(report['checks'])} 项通过 =====")
    print("控制台错误:", len(report["console_errors"]))
    for x in report["console_errors"][:6]:
        print("   ", x)
    print("失败请求:", len(report["failed_requests"]))
    for x in report["failed_requests"][:6]:
        print("   ", x)
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    sys.exit(0 if not bad and not report["console_errors"] and not report["failed_requests"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
