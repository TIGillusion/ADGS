"""anysearch 搜索工具（带代理支持）

用法（hermes 节点可直接调用）：
  python anysearch.py "搜索关键词" [--tag code.doc] [--params '{"library":"golang"}'] [--max 10] [--proxy http://127.0.0.1:10810]

代理策略（应对 IP 限流/额度满）：
  1. 自动检测系统代理（注册表 ProxyServer）
  2. 显式 --proxy 参数优先
  3. 代理失败自动切换到下一个候选代理
  4. 全部失败则直连重试
"""
import json
import re
import subprocess
import sys
import urllib.request

API_URL = "https://api.anysearch.com/v1/search"

# 候选代理列表（按优先级；可编辑增删，配合你的代理软件）
PROXY_CANDIDATES = [
    "http://127.0.0.1:10810",   # 系统代理（当前检测到的）
    "http://127.0.0.1:7890",    # Clash 常见端口
    "http://127.0.0.1:7897",    # Clash Verge 常见端口
    "http://127.0.0.1:10809",   # v2rayN 常见端口
    "http://127.0.0.1:8118",    # Privoxy 常见端口
]


def get_system_proxy() -> str:
    """读取 Windows 系统代理（注册表）"""
    try:
        out = subprocess.run(
            ["powershell", "-Command",
             "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings').ProxyServer"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if out and out != "None":
            return "http://" + out.split(";")[0]
    except Exception:
        pass
    return ""


def build_proxy_list(explicit: str = None) -> list:
    """组装候选代理列表：显式 > 系统 > 常见端口"""
    proxies = []
    if explicit:
        proxies.append(explicit)
    sysp = get_system_proxy()
    if sysp and sysp not in proxies:
        proxies.append(sysp)
    for p in PROXY_CANDIDATES:
        if p not in proxies:
            proxies.append(p)
    return proxies


def search(query: str, tag: str = None, params: dict = None,
           max_results: int = 10, proxy: str = None,
           timeout: int = 20) -> dict:
    """执行搜索；自动代理切换。返回 {"ok", "results", "error", "via"}"""
    payload = {"query": query, "max_results": max_results}
    if tag:
        payload["tag"] = tag
    if params:
        payload["params"] = params
    data = json.dumps(payload).encode()

    # 候选代理：显式 > 系统 > 常见端口；最后直连
    proxy_list = build_proxy_list(proxy) + [None]  # None = 直连
    for p in proxy_list:
        try:
            if p:
                handler = urllib.request.ProxyHandler(
                    {"http": p, "https": p})
                opener = urllib.request.build_opener(handler)
            else:
                opener = urllib.request.build_opener()
            req = urllib.request.Request(API_URL, data=data,
                                         headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            if resp.get("code") == 0:
                results = resp["data"]["results"]
                return {"ok": True, "results": results,
                        "via": p or "直连", "error": ""}
            # code != 0（如限流）→ 换代理重试
            return {"ok": False, "results": [], "via": p or "直连",
                    "error": f"code={resp.get('code')} {resp.get('message')}"}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if p:
                print(f"  [anysearch] 代理 {p} 失败: {err[:80]}", file=sys.stderr)
            continue
    return {"ok": False, "results": [], "via": "全部失败", "error": err}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    args = sys.argv[1:]
    query = args[0]
    tag, params, maxn, proxy = None, None, 10, None
    i = 1
    while i < len(args):
        if args[i] == "--tag" and i + 1 < len(args):
            tag = args[i + 1]; i += 2
        elif args[i] == "--params" and i + 1 < len(args):
            try:
                params = json.loads(args[i + 1])
            except Exception:
                params = None
            i += 2
        elif args[i] == "--max" and i + 1 < len(args):
            maxn = int(args[i + 1]); i += 2
        elif args[i] == "--proxy" and i + 1 < len(args):
            proxy = args[i + 1]; i += 2
        else:
            i += 1

    print(f"[anysearch] 查询: {query} | tag={tag} | 代理: {proxy or '自动'}")
    res = search(query, tag, params, maxn, proxy)
    if not res["ok"]:
        print(f"[anysearch] 失败: {res['error']}")
        sys.exit(1)
    print(f"[anysearch] 成功（via {res['via']}），{len(res['results'])} 条结果：")
    for item in res["results"]:
        print(f"  • {item.get('title', '')}")
        print(f"    {item.get('url', '')}")
        snip = item.get("snippet", "") or ""
        if snip:
            print(f"    {snip[:150]}")


if __name__ == "__main__":
    main()
