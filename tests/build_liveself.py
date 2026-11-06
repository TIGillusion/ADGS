"""ADGS 节点派发：构建 live-self 直播网站（3 子任务并行）

工作区: D:\\user_illusion\\test\\live-self
"""
import sys, json, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from config.prompts import render_task_md
from core.registry import registry
from core.router import route
from core.node import NODE

WS = "D:/user_illusion/test/live-self"

# ---------- 3 个子任务 ----------
TASKS = [
    # 子任务 1: Flask 后端 + WebSocket
    {
        "name": "flask-backend",
        "overall": "B站风格直播网站后端",
        "goal": f"创建 {WS}/app.py：Flask 应用，端口 8080。路由：GET / 渲染 index.html、GET /room/<room_id> 渲染 room.html、GET /broadcast 渲染 broadcast.html、GET /api/rooms 返回直播间 JSON、GET /api/status 检测直播状态。WebSocket 端点 /ws：客户端连接后加入房间，收到 {json.dumps({'type':'danmaku','room':1,'user':'xxx','text':'xxx'})} 格式消息时广播给同房间所有人。rooms.json 存 3 个直播间。",
        "requirements": [
            "Flask 应用 app.py，端口 8080",
            "路由: / /room/<id> /broadcast /api/rooms /api/status",
            "WebSocket /ws 弹幕广播（flask-sock）",
            "rooms.json 预置3个直播间",
            "在线人数统计（连接数）",
        ],
        "acceptance": [
            "python app.py 可启动",
            "/api/rooms 返回 3 个直播间",
            "/ws 弹幕可广播",
        ],
        "deliverables": ["app.py", "rooms.json", "requirements.txt"],
    },
    # 子任务 2: 前端页面模板
    {
        "name": "frontend-pages",
        "overall": "B站风格直播网站前端页面",
        "goal": f"创建 {WS}/templates/index.html、room.html、broadcast.html：深色 B 站风格（主色 #fb7299 粉色）。index.html 展示直播间卡片列表（封面占位、标题、主播、在线人数、直播中标记），点击进入 /room/1。room.html 含 hls.js 播放器视频区 + 弹幕飘屏层 + 右侧聊天列表 + 底部输入框（昵称+弹幕），引用 /static/player.js 和 /static/danmaku.js。broadcast.html 显示推流地址提示（rtmp://localhost:1935/live + 密钥 stream）和直播状态。",
        "requirements": [
            "templates/index.html 直播间列表",
            "templates/room.html 播放器+弹幕+聊天",
            "templates/broadcast.html 主播端",
            "深色主题 B站风格（#fb7299 主色）",
            "元素ID约定: #video, #danmaku-layer, #chat-list, #danmaku-input, #nickname-input, #send-btn",
        ],
        "acceptance": [
            "三个模板文件存在",
            "room.html 含 hls.js 播放器代码",
            "深色 B站风格样式",
        ],
        "deliverables": ["templates/index.html", "templates/room.html", "templates/broadcast.html"],
    },
    # 子任务 3: 静态资源
    {
        "name": "static-assets",
        "overall": "B站风格直播网站静态资源",
        "goal": f"创建 {WS}/static/style.css、player.js、danmaku.js：style.css 深色主题（背景 #0f1011，主色 #fb7299），布局：顶部导航栏、视频区16:9、弹幕层绝对定位覆盖视频、聊天列表右侧300px、输入框底部。player.js 用 hls.js（CDN）播放 http://localhost:8888/live/stream/index.m3u8，暴露 window.Player.init(videoEl)。danmaku.js 实现 WebSocket 连接 /ws、发送和接收弹幕、飘屏动画（从右向左滚动）、聊天列表追加、在线人数更新，暴露 window.Danmaku.init(roomId, nickname)。",
        "requirements": [
            "static/style.css 深色B站主题",
            "static/player.js hls.js 封装",
            "static/danmaku.js WebSocket 弹幕+飘屏",
            "飘屏动画从右向左",
            "聊天列表+在线人数",
        ],
        "acceptance": [
            "三个静态文件存在",
            "player.js 可被 room.html 引用",
            "danmaku.js 飘屏+聊天双显示",
        ],
        "deliverables": ["static/style.css", "static/player.js", "static/danmaku.js"],
    },
]


def build_task_md(t: dict) -> str:
    return render_task_md(
        t["overall"], t["goal"], t["requirements"], t["acceptance"],
        t["deliverables"], f"只允许修改工作区 {WS} 内的文件",
        report_path=f"{WS}/report_{t['name']}.md")


def run_one(t: dict):
    task_md = build_task_md(t)
    print(f"\n[派发] {t['name']} 路由中...", flush=True)
    node = route(task_md, reg=registry)
    if not node:
        return {"name": t["name"], "error": "无可用节点"}
    print(f"  → {node.id} 执行 {t['name']}", flush=True)
    t0 = time.time()
    try:
        r = NODE(node.id).work(task_md, upper="ceo")
    except Exception as e:
        return {"name": t["name"], "error": str(e)}
    print(f"  ✓ {t['name']} 耗时{time.time()-t0:.0f}s success={r.get('success')}", flush=True)
    return {"name": t["name"], "success": r.get("success"),
            "eval": r.get("evaluation", {}).get("score"),
            "node": node.id, "time": round(time.time() - t0)}


def main():
    print(f"工作区: {WS}", flush=True)
    print(f"活动节点: {registry.count()}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_one, t): t["name"] for t in TASKS}
        for fut in as_completed(futs):
            results.append(fut.result())
    print("\n=== 结果 ===", flush=True)
    for r in results:
        print(r, flush=True)
    print("===END===", flush=True)


if __name__ == "__main__":
    main()
