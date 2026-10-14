# -*- coding: utf-8 -*-
"""T5.1 前端冒烟取证：CDP 驱动无头 Edge，登录 + 六大页面 + 人设 + 新领域筛选。

三层断言（每张截图）：
  1 像素尺寸：真读 PNG IHDR，必须等于目标视口 1440x900
  2 内容语义：正文非空 且 含该页独有标记文本
  3 无错误态：不含错误/失败态特征文案
另加互异断言：各 PNG 的 md5 必须互不相同（防"截图区域错位"式摆设）。
"""
import asyncio, base64, hashlib, json, os, re, sys, time
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "shots")
CDP = "http://127.0.0.1:9222"
ORIGIN = "http://localhost:5173"
VW, VH = 1440, 900

ERR_PAT = ["用户名或密码错误", "Failed to fetch", "请求失败", "Not Found",
           "加载失败", "生成失败", "保存失败", "生成出错", "Uncaught",
           "Cannot read properties", "is not a function"]

for k in ("http_proxy", "https_proxy", "all_proxy",
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"

RESULTS = []
STEPS = []


def password():
    src = open(os.path.join(ROOT, "data", "clean_chunk.py"), "rb").read().decode("utf-8", "replace")
    return re.search(r'SEED_USER_PASSWORD\s*=\s*["\']([^"\']*)["\']', src).group(1)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(26)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


class Browser:
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

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True)
        if r.get("exceptionDetails"):
            return "__JSERR__" + str(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    async def text(self):
        v = await self.js("document.body ? document.body.innerText : ''")
        return v if isinstance(v, str) else ""

    async def goto(self, url):
        await self.cmd("Page.navigate", url=url)

    async def wait_until(self, pred, timeout=20.0, label=""):
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            last = await self.text()
            try:
                if pred(last):
                    return last
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise AssertionError("等待超时(%ss) %s；末次正文尾部=%r" % (timeout, label, last[-200:]))

    async def shot(self, name, marker, extra=(), must_absent=()):
        await asyncio.sleep(0.8)
        r = await self.cmd("Page.captureScreenshot", format="png")
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        w, h = png_size(path)
        body = await self.text()
        url = await self.js("location.pathname")
        md5 = hashlib.md5(open(path, "rb").read()).hexdigest()
        problems = []
        if (w, h) != (VW, VH):
            problems.append("像素尺寸 %dx%d != %dx%d" % (w, h, VW, VH))
        if not body.strip():
            problems.append("正文为空")
        if marker not in body:
            problems.append("缺标记 %r" % marker)
        for e in extra:
            if e not in body:
                problems.append("缺标记 %r" % e)
        hit = [p for p in ERR_PAT if p in body]
        if hit:
            problems.append("出现错误态 %s" % hit)
        for e in must_absent:
            if e in body:
                problems.append("不应出现 %r" % e)
        RESULTS.append({"name": name, "path": path, "url": url, "size": [w, h], "md5": md5,
                        "body_len": len(body), "marker": marker, "problems": problems,
                        "ok": not problems})
        print("[%s] %-34s %s%s" % ("PASS" if not problems else "FAIL", name, marker,
                                   "" if not problems else " | " + "; ".join(problems)), flush=True)
        return not problems

    async def click_text(self, text, timeout=8.0):
        """仅点真交互元素（button/a/li），并按 innerText 最短者取「最深」匹配，
        避免命中正文里提到按钮名的提示 div。"""
        js = """(function(){
          const t = %s, tags = 'button,a,li,[role=button]';
          const vis = e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed';
          const tight = list => {
            const m = list.filter(vis).sort((a,b) =>
              (a.innerText||'').trim().length - (b.innerText||'').trim().length);
            return m[0] || null;
          };
          const pool = [...document.querySelectorAll(tags)];
          let c = tight(pool.filter(e => (e.innerText||'').trim() === t))
               || tight(pool.filter(e => (e.innerText||'').includes(t)));
          if (!c) return 'NOHIT';
          c.click();
          return 'OK:' + c.tagName + ':' + (c.innerText||'').trim().slice(0,18);
        })()""" % json.dumps(text)
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            last = await self.js(js)
            if last and str(last).startswith("OK"):
                return last
            await asyncio.sleep(0.3)
        return last or "NOHIT"

    async def click_href(self, href):
        return await self.js(
            """(function(){const a=document.querySelector('a[href="%s"]');
                if(!a) return 'NOEL'; a.click(); return 'OK';})()""" % href)

    async def type_text(self, selector, text, submit=False):
        ok = await self.js(
            """(function(){const el=document.querySelector(%s);
                if(!el) return 'NOEL'; el.focus(); return 'OK';})()""" % json.dumps(selector))
        if ok != "OK":
            return ok
        await self.cmd("Input.insertText", text=text)
        if submit:
            for t in ("keyDown", "char", "keyUp"):
                await self.cmd("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter",
                               windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
        return "OK"


async def step(b, label, expect_shot, fn):
    """单步隔离：任一步失败不影响后续取证，失败以合成条目记入报告。"""
    before = len(RESULTS)
    t0 = time.time()
    try:
        await fn()
        STEPS.append({"step": label, "ok": True, "seconds": round(time.time() - t0, 2)})
        return True
    except Exception as e:
        if len(RESULTS) == before and expect_shot:
            RESULTS.append({"name": expect_shot, "path": None, "url": None, "size": None,
                            "md5": None, "body_len": 0, "marker": "",
                            "problems": ["步骤异常：%s" % e], "ok": False})
        else:
            RESULTS[-1]["problems"].append("步骤异常：%s" % e)
            RESULTS[-1]["ok"] = False
        STEPS.append({"step": label, "ok": False, "seconds": round(time.time() - t0, 2),
                      "error": str(e)})
        print("[FAIL] %s | %s" % (label, e), flush=True)
        return False


async def main():
    os.makedirs(OUT, exist_ok=True)
    with urllib.request.urlopen(CDP + "/json") as r:
        targets = json.load(r)
    page = next(t for t in targets if t.get("type") == "page")
    pw = password()
    print("[env] pw len=%d codes=%s" % (len(pw), [hex(ord(c)) for c in pw]), flush=True)
    print("[env] browser=%s" % page.get("title"), flush=True)

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        b = Browser(ws)
        await b.cmd("Page.enable")
        await b.cmd("Runtime.enable")
        await b.cmd("Emulation.setDeviceMetricsOverride", width=VW, height=VH,
                    deviceScaleFactor=1, mobile=False)

        async def s0():  # 清鉴权态，回到登录页
            await b.goto(ORIGIN + "/")
            await asyncio.sleep(1.5)
            await b.js("localStorage.clear()")
            await b.goto(ORIGIN + "/")
            await b.wait_until(lambda t: "演示账号" in t, label="登录页")

        async def s1():
            await b.shot("00_login.png", "登录", extra=("用户名", "密码", "演示账号"))

        async def s2():  # UI 驱动真实表单登录
            await b.type_text('input[placeholder="用户名"]', "demo_seed")
            await b.type_text('input[placeholder="密码"]', pw, submit=True)
            await b.wait_until(lambda t: "素材库管理" in t, timeout=25, label="登录后进入工作台")

        async def s3():
            await b.shot("01_materials.png", "素材库管理", extra=("导入素材", "领域", "分块数"))

        async def s4():  # 新领域：素材检索「美妆」
            await b.type_text('input[placeholder="搜索素材（关键词 / 语义）"]', "美妆", submit=True)
            await b.wait_until(lambda t: "美妆" in t, timeout=20, label="素材检索 美妆")
            await b.shot("02_materials_search_meizhuang.png", "美妆", extra=("素材库管理",))

        async def s5():
            await b.click_href("/hotspots")
            await b.wait_until(lambda t: "热点看板" in t, label="热点看板")
            await b.shot("03_hotspots.png", "热点看板", extra=("全部",))

        async def s6():  # 新领域筛选点选：热点 美妆
            assert str(await b.click_text("美妆")).startswith("OK"), "未点中 美妆 筛选按钮"
            await b.wait_until(lambda t: "美妆资讯" in t or "美妆" in t, label="热点 美妆")
            await b.shot("04_hotspots_meizhuang.png", "美妆", extra=("热点看板",))

        async def s7():  # 新领域筛选点选：热点 教育
            assert str(await b.click_text("教育")).startswith("OK"), "未点中 教育 筛选按钮"
            await b.wait_until(lambda t: "教育资讯" in t or "教育" in t, label="热点 教育")
            await b.shot("05_hotspots_jiaoyu.png", "教育", extra=("热点看板",))

        async def s8():
            await b.click_href("/topics")
            await b.wait_until(lambda t: "选题列表" in t, label="选题列表")
            await b.shot("06_topics_initial.png", "选题列表", extra=("生成选题", "领域（可选）"))

        async def s9():  # 真实 POST /api/topics/generate
            assert str(await b.click_text("生成选题")).startswith("OK"), "未点中 生成选题"
            await b.wait_until(lambda t: "一键选用" in t, timeout=30, label="选题生成结果")
            await b.shot("07_topics_generated.png", "一键选用", extra=("选题列表",))

        async def s10():  # 一键选用 → 带入 FlowContext
            assert str(await b.click_text("一键选用")).startswith("OK"), "未点中 一键选用"
            await b.wait_until(lambda t: "文案生成与对比编辑" in t, label="文案生成页")
            v = await b.js("document.querySelectorAll('input')[0].value")
            assert v and str(v).strip(), "选题标题未随流程带入"
            await b.shot("08_generate_params.png", "创作参数", extra=("一键生成", "目标平台（多选）"))

        async def s11():  # SSE 全链路
            assert str(await b.click_text("一键生成")).startswith("OK"), "未点中 一键生成"
            await b.wait_until(lambda t: "版本 1" in t, timeout=90, label="SSE 多版本产物")
            await b.shot("09_generate_result.png", "版本 1", extra=("已检索素材", "一致性"))

        async def s12():
            assert str(await b.click_text("进入平台适配")).startswith("OK"), "未点中 进入平台适配"
            await b.wait_until(lambda t: "平台适配预览" in t, label="平台适配预览")
            await b.wait_until(lambda t: "任务 ID" in t, timeout=20, label="适配任务带入")
            await b.shot("10_adapt.png", "平台适配预览", extra=("任务 ID", "微信公众号"))

        async def s13():
            await b.click_href("/persona")
            await b.wait_until(lambda t: "账号人设" in t, label="账号人设")
            await b.wait_until(lambda t: "美妆" in t and "教育" in t, timeout=20, label="人设回填新领域")
            await b.shot("11_persona.png", "账号人设",
                         extra=("展示名称", "深耕领域", "美妆", "教育"))

        async def s14():  # 路由守卫
            await b.js("localStorage.removeItem('token');localStorage.removeItem('username');")
            await b.goto(ORIGIN + "/materials")
            await b.wait_until(lambda t: "演示账号" in t, label="未登录回落登录页")
            await b.shot("12_guard_noauth.png", "登录", extra=("用户名",),
                         must_absent=("素材库管理",))

        seq = [("清鉴权态并打开登录页", "00_login.png", s0),
               ("登录页截图", "00_login.png", s1),
               ("真实表单登录", "01_materials.png", s2),
               ("素材库管理截图", "01_materials.png", s3),
               ("新领域检索 美妆", "02_materials_search_meizhuang.png", s4),
               ("热点看板截图", "03_hotspots.png", s5),
               ("筛选点选 美妆", "04_hotspots_meizhuang.png", s6),
               ("筛选点选 教育", "05_hotspots_jiaoyu.png", s7),
               ("选题列表截图", "06_topics_initial.png", s8),
               ("生成选题", "07_topics_generated.png", s9),
               ("一键选用带入生成页", "08_generate_params.png", s10),
               ("SSE 多版本生成", "09_generate_result.png", s11),
               ("进入平台适配", "10_adapt.png", s12),
               ("账号人设截图", "11_persona.png", s13),
               ("未登录路由守卫", "12_guard_noauth.png", s14)]
        for label, expect, fn in seq:
            await step(b, label, expect, fn)

    # 互异断言：防"截图区域错位"式摆设截图。
    # 例外：12_guard_noauth 由设计上就应回到与 00_login 同一画面（路由守卫回落），
    # 该对重复是预期结果，单独以"状态断言"（must_absent 素材库管理）作为其有效性证据。
    DUP_OK_PAIRS = {frozenset({"00_login.png", "12_guard_noauth.png"})}
    seen = {}
    for r in RESULTS:
        if not r["md5"]:
            continue
        r["dup_of"] = seen.get(r["md5"])
        if r["md5"] in seen:
            pair = frozenset({seen[r["md5"]], r["name"]})
            for x in RESULTS:
                if x["name"] == r["name"]:
                    if pair in DUP_OK_PAIRS:
                        x["dup_note"] = "与 %s 同画面：路由守卫回落登录页，属预期（已由状态断言佐证）" % seen[r["md5"]]
                    else:
                        x["problems"].append("画面与 %s 完全相同（互异断言失败）" % seen[r["md5"]])
                        x["ok"] = False
        seen[r["md5"]] = r["name"]
    for x in RESULTS:
        if x["name"] in ("00_login.png",) and x.get("dup_of"):
            x["dup_note"] = "登录页共出现 2 次（首屏 + 守卫回落），画面一致属预期"

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "viewport": [VW, VH],
        "browser": "Microsoft Edge 155 headless=new（Chromium 内核）",
        "mode": "VITE_USE_MOCK=false（真实 M3 后端，经 Vite 代理 /api → 127.0.0.1:8000）",
        "total": len(RESULTS),
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "failed": sum(1 for r in RESULTS if not r["ok"]),
        "distinct_md5": len(seen),
        "steps": STEPS,
        "shots": RESULTS,
    }
    with open(os.path.join(HERE, "smoke_report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n==== 汇总 %d/%d 通过；独立画面 %d/%d ====" %
          (summary["passed"], summary["total"], len(seen), len(RESULTS)), flush=True)
    for r in RESULTS:
        if not r["ok"]:
            print("  FAIL %s: %s" % (r["name"], "; ".join(r["problems"])), flush=True)
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
