---
name: mijia-home
description: 米家(Mi Home)生态智能设备云控制技能。通过小米云 API 完成设备发现、miSpec 属性读写与读回验证，适用于任何米家设备（插座、灯、传感器、开关等），设备无关、协议通用。凡任务涉及米家/小米智能设备、智能家居控制、设备状态查询、插座开关、传感器读取时使用。
---

# 米家(Mi Home)智能设备云控制

通过小米云（xiaomiio）API 控制米家生态设备。**设备无关**：不依赖任何具体设备型号，
统一走「设备发现 → miSpec 属性读写 → 读回验证」通用协议。

## 快速上手

配套脚本（纯标准库，无需安装依赖）：

```bash
python D:\user_illusion\test\ADGS\skills\mijia-home\mijia.py <命令> [参数] [--json]
```

凭据与环境配置已就绪：`D:\user_illusion\test\ADGS\skills\mijia-home\mijia_config.json`
（账号、服务地址都在里面，**不要向用户索要凭据，一切以此配置为准**；也不要把密码打进报告）。

命令一览（`--json` 可得结构化输出）：

```bash
# 1) 验证登录链路
python D:\...\mijia.py login

# 2) 设备盘点：名称/型号/did/在线状态
python D:\...\mijia.py list

# 3) 读属性（可一次读多个 siid/piid）
python D:\...\mijia.py read 测试插座 2 1
python D:\...\mijia.py read 客厅人体存在传感器 2 1 2 2

# 4) 写属性（value 支持 true/false/数字/字符串）
python D:\...\mijia.py write 测试插座 2 1 true

# 5) 插座/灯快捷开关（= 写 siid2/piid1，自带 前读→写→读回 三段证据）
python D:\...\mijia.py quick 测试插座 on
python D:\...\mijia.py quick 测试插座 off

# 6) 逃生舱：任意已签名 POST（如新接口路径）
python D:\...\mijia.py api /app/home/deviceapi '{"command":"read","did":"1187034261","miSpec":"[{\"siid\":2,\"piid\":1}]"}'
```

设备参数可用**设备名（子串匹配）或 did**；名字有歧义时报错并列出候选，此时改用 did。

## 通用方法论（任何米家设备都按这套来）

1. **先发现**：`list` 拿到设备名、型号(model)、did、在线状态。只对在线设备操作。
2. **找属性**：米家新协议用 `siid`(服务)/`piid`(属性) 二元组定位设备属性：
   - 常见速查（先试，不对再探）：
     - 插座/开关/灯的**电源开关**：`siid=2, piid=1`，布尔值（true=开）
     - 传感器数值属性：一般分布在 `siid=2~4, piid=1~3`，逐个 `read` 探测
   - `read` 返回 `code:-1001 unknown property` 只是"这个位置没属性"，**不是失败**，换下一个 siid/piid 继续探。
   - 陌生型号：用 anysearch 技能搜 `<model> miSpec siid piid` 或 "Xiaomi Home <model> spec" 查属性表。
3. **写前先读**：改状态前记录当前值（这是证据链第一段）。
4. **写后必读回**：任何写操作（write/quick）之后必须再 `read` 一次同属性；`quick` 命令已自动带前读+读回。
5. **如实汇报**：报告里引用接口原始 JSON 返回，不要转述、更不要编造。
   任何一步失败（登录失败/401/设备离线/属性不存在）如实写进报告并标 blocked，**禁止在没有读回证据的情况下声称操作成功**。

## 常见问题

- **`未找到 mijia_config.json`**：用上面的绝对路径直接调用脚本（配置在脚本同目录）。
- **`登录失败(serviceLoginAuth2)`**：配置里的账号密码不对——检查 mijia_config.json，不要自己猜密码。
- **HTTP 401 unauthorized**：登录会话没建立成功。先跑 `login` 单独验证；本脚本每次进程会自动重登，一般不会出现。
- **登录会话缓存**：脚本会在配置同目录自动维护 `mijia_session.json`（复用 serviceToken，避免频繁登录被真实服务限流；过期自动重登）。排障时可用环境变量 `MIJIA_NO_CACHE=1` 绕过缓存。
- **写返回 code:-1002 类型不符**：布尔属性必须给 true/false（不是 "on"/1）。
- **设备离线 code:-2**：如实汇报设备离线，不要伪造状态。

## 从仿真切到真实米家账号

当前配置可能指向本机仿真环境（`account_base`/`api_base` 为 127.0.0.1）。切换真实环境：
编辑 `mijia_config.json`：`mode` 改 `cloud`、`username`/`password` 填真实小米账号、
删除 `account_base`/`api_base`（默认即官方 `account.xiaomi.com` / `api.io.mi.com`）。
登录算法按小米云公开三步流程实现，首次接真实账号先 `login` → `list` 验证链路，
属性结构以真实接口返回为准（可能与仿真略有差异，一律原样引用返回值）。

## 安全与边界

- 只操作任务书点名的设备；不要顺手开关无关设备。
- 凭据只在配置文件里，不写进报告/日志/交付物。
- 传感器类设备只读不写；对拿不准的属性先 read 探测。
