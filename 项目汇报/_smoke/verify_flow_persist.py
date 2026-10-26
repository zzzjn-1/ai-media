# -*- coding: utf-8 -*-
"""流程上下文持久化的浏览器取证（第 2 条）。

覆盖：
  1) 生成完成后刷新 /generate → 版本卡片回来、taskId 不变、有"已恢复"提示
  2) 刷新 /adapt → 内容回来，且**不再发 POST adapt**（恢复优先，不重跑适配）
  3) 生成过程中不得误触发恢复（生成本身不应产生 GET /copy/{id}）
  4) 本地 taskId 指向不存在的任务 → 清指针 + 提示，页面不留死白
  5) 存档 owner 不是当前账号 → 整块丢弃（防串号）

用法：python verify_flow_persist.py
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
PORT = 9337
BASE = os.environ.get("SMOKE_BASE", "http://localhost:5173")
MOCK = os.environ.get("SMOKE_MOCK") == "1"
USER = "demo_seed"
PWD = "Demo@2026"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shots_persist")


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


# 生成页卡片取样：每张卡片一个 textarea，标题是卡片内的首个 .font-medium
CARDS_JS = """
(() => {
  return [...document.querySelectorAll('textarea')].map(t => {
    const card = t.closest('div.bg-white') || t.parentElement;
    const el = card ? card.querySelector('.font-medium') : null;
    return { title: (el ? el.textContent : '').trim().slice(0, 40), len: (t.value || '').length };
  });
})()
"""

FLOW_JS = "(() => { try { return JSON.parse(localStorage.getItem('flow:v1') || 'null') } catch (e) { return 'bad' } })()"

TASK_ID_JS = "(() => { const m = (document.body.innerText||'').match(/任务 ID (\\d+)/); return m ? Number(m[1]) : null })()"

# 适配页：取当前激活平台的正文长度与是否渲染了发布备注
ADAPT_JS = """
(() => {
  const t = document.body.innerText || '';
  return {
    hasNote: t.includes('发布备注'),
    bodyLen: t.length,
    empty: t.includes('该平台暂无适配结果') || t.includes('尚无创作任务'),
    restoredHint: t.includes('已恢复上次生成结果'),
  };
})()
"""


def net_urls(events, method=None, needle=None):
    out = []
    for e in events:
        p = e.get("params", {})
        req = p.get("request") or {}
        url = req.get("url", "")
        if not url:
            continue
        if method and req.get("method") != method:
            continue
        if needle and needle not in url:
            continue
        out.append(url)
    return out


async def wait_for(fn, tries=40, delay=0.5):
    for _ in range(tries):
        v = await fn()
        if v:
            return v
        await asyncio.sleep(delay)
    return await fn()


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

    checks = []
    console_errors, failed_requests = [], []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))
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
            await asyncio.sleep(2.0)

            if MOCK:
                await c.js("localStorage.setItem('token','mock-token');"
                           "localStorage.setItem('username','%s');'ok'" % USER)
                check("登录（Mock 模式：直接写本地 token）", True)
            else:
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

            # 从干净状态开始：清掉可能残留的流程指针
            await c.js("localStorage.removeItem('flow:v1');'ok'")
            c.take_events()

            # ---------- 1) 从选题页带入标题，生成一次 ----------
            await c.cmd("Page.navigate", url=f"{BASE}/topics")
            await wait_for(lambda: c.js("document.querySelectorAll('.group').length"), 40, 0.5)
            picked = await c.js("""
            (() => {
              const btn = [...document.querySelectorAll('button')]
                .find(b => (b.textContent || '').includes('一键选用'));
              if (!btn) return null;
              const card = btn.closest('.group');
              const el = card && card.querySelector('.font-semibold');
              const title = el ? el.textContent.trim() : '';
              btn.click();
              return title;
            })()
            """)
            check("从选题列表选用一条并跳转生成页", bool(picked), str(picked)[:40])
            await asyncio.sleep(2.0)

            await c.cmd("Page.navigate", url=f"{BASE}/generate")
            await asyncio.sleep(1.6)
            topic_val = await c.js("(() => { const i = document.querySelector('input'); return i ? i.value : '' })()")
            check("选题标题已带入生成页", bool(topic_val), str(topic_val)[:40])

            c.take_events()
            gen = await c.js("""
            (() => {
              const b = [...document.querySelectorAll('button')]
                .find(x => (x.textContent || '').includes('一键生成'));
              if (!b) return 'not-found';
              b.click();
              return 'clicked';
            })()
            """)
            check("点击一键生成", gen == "clicked", gen)

            cards = []
            for _ in range(220):  # 最长约 110s（真 LLM 三版 43–49s，留足排队/重试余量）
                await asyncio.sleep(0.5)
                cards = await c.js(CARDS_JS) or []
                done = await c.js("(document.body.innerText||'').includes('生成完成')")
                if len(cards) >= 3 and done:
                    break
            await c.shot("01_generated.png")
            check("生成出 3 张版本卡片", len(cards) >= 3, f"实际 {len(cards)} 张，正文长度 {[x['len'] for x in cards]}")

            # 生成过程中不得触发恢复读取
            gen_events = c.take_events()
            get_copy_during_gen = net_urls(gen_events, "GET", "/api/copy/")
            check("生成期间未误触发恢复读取", len(get_copy_during_gen) == 0,
                  f"GET /api/copy 次数 {len(get_copy_during_gen)}")

            flow_after_gen = await c.js(FLOW_JS)
            tid_after_gen = (flow_after_gen or {}).get("taskId") if isinstance(flow_after_gen, dict) else None
            check("生成后 localStorage 记下 taskId", bool(tid_after_gen), f"taskId={tid_after_gen}")
            titles_before = [x["title"] for x in cards]
            detail_before = await c.js(TASK_ID_JS)
            if MOCK:
                check("Mock 任务 ID 与本地指针一致", detail_before == tid_after_gen,
                      f"页面 {detail_before} / 本地 {tid_after_gen}")

            # ---------- 2) 刷新生成页：卡片应回来 ----------
            c.take_events()
            await c.cmd("Page.reload")
            cards2 = []
            for _ in range(60):
                await asyncio.sleep(0.4)
                cards2 = await c.js(CARDS_JS) or []
                if cards2:
                    break
            await asyncio.sleep(1.0)
            await c.shot("02_generate_after_reload.png")
            titles_after = [x["title"] for x in cards2]
            check("刷新后版本卡片恢复", len(cards2) >= 3, f"实际 {len(cards2)} 张")
            check("恢复的标题与刷新前一致", titles_before[:3] == titles_after[:3],
                  f"{titles_before[:2]} vs {titles_after[:2]}")
            check("正文非空（不是空壳卡片）", all(x["len"] > 80 for x in cards2),
                  f"长度 {[x['len'] for x in cards2]}")
            hint = await c.js("(document.body.innerText||'').includes('已恢复上次生成结果')")
            check("显示「已恢复上次生成结果」提示", hint)
            flow2 = await c.js(FLOW_JS)
            check("taskId 刷新前后不变", isinstance(flow2, dict) and flow2.get("taskId") == tid_after_gen,
                  f"{tid_after_gen} → {(flow2 or {}).get('taskId')}")

            # ---------- 3) 适配页刷新：内容回来且不重跑适配 ----------
            await c.cmd("Page.navigate", url=f"{BASE}/adapt")
            for _ in range(80):
                await asyncio.sleep(0.5)
                st = await c.js(ADAPT_JS) or {}
                if not st.get("empty") and st.get("hasNote"):
                    break
            await c.shot("03_adapt_first.png")
            st = await c.js(ADAPT_JS) or {}
            check("适配页有正文与发布备注", st.get("hasNote") and st.get("bodyLen", 0) > 400,
                  f"bodyLen={st.get('bodyLen')}")

            c.take_events()
            await c.cmd("Page.reload")
            st2 = {}
            for _ in range(80):
                await asyncio.sleep(0.4)
                st2 = await c.js(ADAPT_JS) or {}
                if st2.get("hasNote"):
                    break
            await c.shot("04_adapt_after_reload.png")
            adapt_events = c.take_events()
            check("刷新适配页后内容仍在", st2.get("hasNote") and not st2.get("empty"),
                  f"bodyLen={st2.get('bodyLen')}")
            posts = net_urls(adapt_events, "POST", "/adapt")
            gets = net_urls(adapt_events, "GET", "/api/copy/")
            check("刷新后不再重跑平台适配", len(posts) == 0, f"POST adapt 次数 {len(posts)}")
            if MOCK:
                print("[SKIP] Mock 模式不走网络，跳过「走读取接口」断言")
            else:
                check("刷新后走的是读取接口", len(gets) >= 1, f"GET /api/copy 次数 {len(gets)}")

            # ---------- 4) 指针指向不存在的任务 ----------
            await c.js("(() => { const f = JSON.parse(localStorage.getItem('flow:v1'));"
                       "f.taskId = 999999; localStorage.setItem('flow:v1', JSON.stringify(f)); return 'ok' })()")
            c.take_events()
            await c.cmd("Page.reload")
            await asyncio.sleep(1.8)  # antd message 3s 后消失，要赶在它还在时取样
            await c.shot("05_missing_task.png")
            st3 = await c.js(ADAPT_JS) or {}
            flow3 = await c.js(FLOW_JS)
            check("任务不存在时回到空态（不是一直转圈）", bool(st3.get("empty")), str(st3.get("empty")))
            check("任务不存在时清掉本地指针",
                  isinstance(flow3, dict) and flow3.get("taskId") is None,
                  f"taskId={(flow3 or {}).get('taskId')}")
            body3 = await c.js("document.body.innerText || ''")
            check("对用户有明确提示", "已不存在" in body3 or "清除本地记录" in body3,
                  "提示条/消息可见" if ("已不存在" in body3 or "清除本地记录" in body3) else "未找到提示")

            # ---------- 5) 换账号：存档必须被丢弃 ----------
            await c.cmd("Page.navigate", url=f"{BASE}/generate")
            await asyncio.sleep(2.0)
            # 造一份"别人的"存档
            await c.js("""(() => {
              localStorage.setItem('flow:v1', JSON.stringify({
                taskId: %s, topicTitle: '别人的选题不该出现', topicId: 1,
                targetPlatforms: ['weixin'], owner: 'someone_else'
              }));
              return 'ok';
            })()""" % (json.dumps(tid_after_gen)))
            await c.cmd("Page.reload")
            await asyncio.sleep(2.5)
            await c.shot("06_other_owner.png")
            topic_now = await c.js("(() => { const i = document.querySelector('input'); return i ? i.value : '' })()")
            cards_now = await c.js(CARDS_JS) or []
            flow_now = await c.js(FLOW_JS)
            check("别人的存档不被采用（标题为空）", not (topic_now or "").strip(), repr(topic_now))
            check("别人的存档不被采用（无卡片）", len(cards_now) == 0, f"{len(cards_now)} 张")
            check("存档被重写为当前账号的空状态",
                  isinstance(flow_now, dict) and flow_now.get("owner") == USER,
                  f"owner={(flow_now or {}).get('owner')}")

            # ---------- 汇总 ----------
            for e in c.take_events():
                p = e.get("params", {})
                if e.get("method") == "Runtime.consoleAPICalled":
                    if p.get("type") == "error":
                        console_errors.append(str(p.get("args"))[:200])
                if e.get("method") == "Runtime.exceptionThrown":
                    console_errors.append(str(p.get("exceptionDetails", {}).get("text"))[:200])
                if e.get("method") == "Network.loadingFailed":
                    failed_requests.append(str(p.get("errorText"))[:120])
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    ok_n = sum(1 for _, ok, _ in checks if ok)
    print(f"\n===== {ok_n}/{len(checks)} 通过 =====")
    print("控制台错误：", len(console_errors), console_errors[:3])
    print("失败请求：", len(failed_requests), failed_requests[:3])
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks],
                   "console_errors": console_errors,
                   "failed_requests": failed_requests}, f, ensure_ascii=False, indent=2)
    return 0 if ok_n == len(checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
