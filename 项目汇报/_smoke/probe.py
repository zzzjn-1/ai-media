# -*- coding: utf-8 -*-
"""T5.1 冒烟：经前端 Vite 代理验证登录 + 各接口连通性。"""
import os, re, json, urllib.request, sys

for k in ("http_proxy", "https_proxy", "all_proxy",
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"

BASE = "http://localhost:5173"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def password():
    src = open(os.path.join(ROOT, "data", "clean_chunk.py"), "rb").read().decode("utf-8", "replace")
    return re.search(r'SEED_USER_PASSWORD\s*=\s*["\']([^"\']*)["\']', src).group(1)


def req(path, data=None, tok=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body,
                               method=method or ("POST" if body else "GET"))
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", str(e)


def main():
    pw = password()
    print("[pw] len=%d codes=%s" % (len(pw), [hex(ord(c)) for c in pw]), flush=True)

    s, b = req("/api/auth/login", {"username": "demo_seed", "password": pw})
    print("[login] %s %s" % (s, b[:160]), flush=True)
    tok = json.loads(b).get("access_token") if s == 200 else None
    if not tok:
        print("!! login failed, abort"); return 1

    checks = ["/api/health", "/api/hotspots?limit=3", "/api/materials?limit=2",
              "/api/persona", "/api/topics?limit=2"]
    for p in checks:
        s2, b2 = req(p, tok=tok)
        print("[get] %-28s %s %s" % (p, s2, b2[:170]), flush=True)

    # 越权检查：无 token 访问应 401
    s3, b3 = req("/api/persona")
    print("[noauth] /api/persona -> %s %s" % (s3, b3[:120]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
