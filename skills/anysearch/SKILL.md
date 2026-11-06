---
name: anysearch
description: 通用网络搜索工具（anysearch API）。当任务需要查询最新信息、文档、代码示例、教程、新闻或任何实时网络内容时使用。支持 tag（文档/代码/新闻/学术/视频等分类）和 params（按库/框架/主题过滤）。免费可用；内置 IP 限流应对：自动检测并使用系统代理，失败自动切换候选代理。优先调用配套脚本 skills/anysearch/anysearch.py。
---

# AnySearch 网络搜索

通过 anysearch API 执行网络搜索。用于任何需要实时/最新信息的场景：
- 查文档（tag=code.doc）、找代码示例（tag=code.code）
- 查新闻（tag=news）、学术资料（tag=academic）
- 教程、博客、技术方案对比

## 推荐用法：调用配套脚本（自带代理切换）

同目录下有 `anysearch.py`，直接用 Python 调用（它会自动处理代理）：

```bash
# 基本搜索
python <项目根>/skills/anysearch/anysearch.py "搜索关键词"

# 指定分类和过滤
python <项目根>/skills/anysearch/anysearch.py "golang http client" --tag code.doc --params '{"library":"golang"}'

# 指定代理（可选，默认自动检测系统代理）
python <项目根>/skills/anysearch/anysearch.py "rust tutorial" --proxy http://127.0.0.1:10810
```

脚本输出格式：
```
[anysearch] 查询: xxx | tag=xxx | 代理: 自动
[anysearch] 成功（via http://127.0.0.1:10810），N 条结果：
  • 标题
    URL
    摘要
```

## API 直接调用（curl）

```bash
curl -X POST https://api.anysearch.com/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "搜索关键词（建议用英文，结果质量更好）",
    "tag": "code.doc",
    "params": {"library": "golang"},
    "max_results": 10
  }'
```

参数说明：
- `query`（必填）：搜索关键词
- `tag`（可选）：结果分类
  - `code.doc` 文档 | `code.code` 代码 | `code.lib` 库/框架
  - `news` 新闻 | `academic` 学术 | `video` 视频 | `general` 通用
- `params`（可选）：附加过滤，如 `{"library": "golang"}`、`{"language": "python"}`
- `max_results`（可选）：返回条数，默认 10，最大 20

## Python 调用示例

```python
import json, urllib.request

req = urllib.request.Request(
    "https://api.anysearch.com/v1/search",
    data=json.dumps({
        "query": "flask websocket tutorial",
        "tag": "code.doc",
        "params": {"library": "python"},
        "max_results": 5
    }).encode(),
    headers={"Content-Type": "application/json"})

with urllib.request.urlopen(req, timeout=20) as r:
    data = json.loads(r.read().decode())

if data.get("code") == 0:
    for item in data["data"]["results"]:
        print(item["title"])
        print("  ", item["url"])
        print("  ", item.get("snippet", "")[:150])
else:
    print("搜索失败:", data.get("message"))
```

## 响应结构

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "results": [
      {"title": "...", "url": "...", "snippet": "...", "content": "..."}
    ],
    "metadata": {}
  }
}
```

## 注意与踩坑

- **免费使用，无需 API key**（有 key 可加 `Authorization: Bearer *** 提高限额）
- **IP 限流应对**：若直连返回 code != 0（限流/额度满）或连接失败，优先用配套脚本
  `anysearch.py` —— 它自动检测系统代理（如 127.0.0.1:10810）并切换候选代理重试；
  也可手动换代理：`--proxy http://<你的代理>`
- **code.doc / code.code tag 必须带 params.library**（如 `{"library":"python"}`），
  否则返回 400 Bad Request / code=-1
- 中文查询可用，但英文查询结果通常更准确
- 返回 code != 0 时换代理或重试一次
