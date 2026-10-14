# -*- coding: utf-8 -*-
"""核查：素材列表 vs 检索路径的 chunk_count 字段。"""
import json, os, re, urllib.request

for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
B = "http://localhost:5173"

pw = re.search(r'SEED_USER_PASSWORD\s*=\s*["\']([^"\']*)["\']',
               open(os.path.join(ROOT, "data", "clean_chunk.py"), "rb").read().decode("utf-8", "replace")).group(1)
r = urllib.request.Request(B + "/api/auth/login",
                           data=json.dumps({"username": "demo_seed", "password": pw}).encode(),
                           method="POST")
r.add_header("Content-Type", "application/json")
tok = json.loads(urllib.request.urlopen(r, timeout=20).read())["access_token"]


def g(p):
    q = urllib.request.Request(B + p)
    q.add_header("Authorization", "Bearer " + tok)
    return json.loads(urllib.request.urlopen(q, timeout=20).read())


a = g("/api/materials?limit=3")
print("无 q（列表）:", [(m["title"][:18], m.get("chunk_count")) for m in a["results"]])
print("  keys=", sorted(a["results"][0].keys()))
b = g("/api/materials?q=%E7%BE%8E%E5%A6%86&limit=3")
print("q=美妆:", [(m["title"][:18], m.get("chunk_count"), m.get("score")) for m in b["results"]])
print("  keys=", sorted(b["results"][0].keys()))
print("  count=", b.get("count"), "total=", b.get("total"), "mode=", b.get("mode"), "topk=", b.get("topk"))
