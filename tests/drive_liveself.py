"""通过 ADGS CEO 对话驱动：创建 B 站风格直播网站

工作区: D:\\user_illusion\\test\\live-self
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ceo import CEO

ceo = CEO()

task = """请直接开始执行，不要提问。在 D:/user_illusion/test/live-self 工作区创建一个 B 站风格的直播网站，功能完整：

【总体目标】
一个对标 B 站直播的网站：用户浏览直播间列表、进入直播间观看 OBS 推流的实时视频、发送弹幕和聊天。

【技术栈】
- Python Flask（后端 API + 页面渲染）
- WebSocket（弹幕/聊天实时通信，用 flask-sock 或 websockets）
- hls.js（浏览器播放 HLS 视频流）
- SQLite（用户/直播间数据，可用 json 文件代替）
- 媒体流由外部 MediaMTX 提供（RTMP→HLS，已配置，HLS 地址 http://localhost:8888/live/stream/index.m3u8），网站只需播放

【必须实现的功能】
1. 直播间列表页（index）：显示所有直播间（封面占位、标题、主播名、在线人数、是否直播中）
2. 直播间页面（/room/<id>）：hls.js 播放器 + 弹幕层（飘屏弹幕）+ 右侧聊天列表
3. 弹幕系统：WebSocket 实时收发弹幕，弹幕显示为飘屏 + 聊天列表两种形式
4. 主播端（/broadcast）：显示直播状态、推流地址提示（rtmp://localhost:1935/live + 密钥）、在线人数
5. 用户系统（简化）：无需注册，输入昵称即可进入直播间发弹幕
6. 在线人数统计：WebSocket 连接数统计
7. 直播状态检测：定时检测 HLS 的 m3u8 文件是否可访问，决定显示"直播中/未开播"

【文件结构】
live-self/
├── app.py            # Flask 入口（含 WebSocket）
├── room.html         # 直播间页面模板
├── index.html        # 直播间列表页
├── broadcast.html    # 主播端页面
├── static/style.css  # 样式（B站风格深色主题，参考 B 站直播的红色 #fb7299 主色）
├── static/player.js  # hls.js 播放器封装
├── static/danmaku.js # 弹幕系统前端
├── rooms.json        # 直播间数据（预置 3 个直播间）
└── requirements.txt  # 依赖

【验收标准】
1. Flask 服务可启动，端口 8080
2. index 页面列出预置的 3 个直播间
3. 直播间页面能加载 hls.js 播放器（指向 http://localhost:8888/live/stream/index.m3u8）
4. WebSocket 弹幕：两个浏览器标签页互发弹幕可见
5. 弹幕飘屏 + 聊天列表双显示
6. 主播端显示推流地址和直播状态
7. 深色 B 站风格 UI

【权限】
只允许在 D:/user_illusion/test/live-self 目录下创建文件。"""
reply = ceo.talk(task)
print(reply)
print("\n=== workflow ===")
for w in ceo.workflow_log:
    print(" ", w)
