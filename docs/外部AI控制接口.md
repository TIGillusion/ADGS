# ADGS 外部 AI 控制接口 v1

面向拥有本系统使用权限的外部 AI。默认入口：`http://127.0.0.1:8021/api/v1`。前端继续使用 `web3/`，控制接口与前端操作同一个运行时、同一份项目和会话。

## 1. 启动、认证与远程连接

按原方式启动 ADGS，例如 `python -u web_app.py`，或使用现有启动脚本。所有 `/api/v1` 接口（包括发现接口和使用说明）都要求：

```http
Authorization: Bearer <CONTROL_TOKEN>
Content-Type: application/json
```

首次使用自动生成控制令牌，保存在项目根目录下 `.adgs_runtime/control_token`，不会经 API 返回。也可在启动前设置 `ADGS_CONTROL_TOKEN`，至少 32 字符；前端服务和任务宿主必须使用一致的配置。使用自定义 `ADGS_STATE_DIR` 时令牌位于对应状态目录。令牌持有者拥有整个系统的控制权限，包括所有项目、会话和模型设置，只交给可信控制端。

本仓库的 Python 客户端自动读取本机令牌：

```powershell
cd D:\user_illusion\test\ADGS
python tools/adgs_control.py status
python tools/adgs_control.py sessions
```

别的电脑可通过 SSH 隧道接入现有回环服务：

```sh
ssh -N -L 18021:127.0.0.1:8021 user@ADGS_HOST
```

控制端设置 `ADGS_CONTROL_URL=http://127.0.0.1:18021/api/v1`，并通过自己的密钥管理机制设置 `ADGS_CONTROL_TOKEN`。若使用 HTTPS 反向代理，只向外开放 `/api/v1` 路径；旧前端 `/api/*` 是本机兼容接口，不应直接暴露到公网。控制 API 不增加浏览器跨站 CORS 授权。

## 2. 先发现，再控制

`GET /api/v1` 返回版本、能力和实际路由清单；`GET /api/v1/guide` 返回本文。

推荐外部 AI 工作流程：

1. `GET /health` 确认服务，保存 `event_cursor`。
2. `GET /sessions` 找到目标会话，或创建项目和会话。
3. `GET /sessions/{session}` 判断状态；需要完整内容时分页读取消息、图谱与执行记录。
4. 新任务发送 `/messages`；原任务恢复使用 `/resume`。
5. 持续轮询 `/events?session=...&after=...` 和会话状态。
6. 根据实际完成报告、执行状态和产出文件判断结果，不能把 HTTP 202 或 `busy=false` 当成成功完成。

项目共享工作目录；每个会话独立 CEO、历史、执行状态和队列。节点群体、长期记忆和技能为整个系统共享。

## 3. 核心接口

下表路径均相对于 `/api/v1`。

| 方法 | 路径 | 用途与输入 |
| --- | --- | --- |
| GET | `/health` | `busy`、`runs`、全局 `event_cursor`、A2A 就绪状态 `mesh`、事故保护状态 `worker_hold` |
| GET | `/projects` | 项目树与会话列表，项目按创建时间从新到旧。加 `?include_archived=1` 可读取已归档项目和会话 |
| POST | `/projects` | `{"name":"研究项目","workspace":"D:/projects/research"}`；目录需已存在 |
| PATCH | `/projects/{project_id}` | `{"name":"新项目名"}`、`{"archived":true}` 归档或 `{"archived":false}` 恢复。项目含运行中会话时拒绝归档 |
| POST | `/projects/{project_id}/sessions` | `{}` 或 `{"title":"技术研究"}`，创建独立会话 |
| GET | `/sessions` | 展平的可见会话列表，含项目和工作目录 |
| PATCH | `/sessions/{session}` | `{"title":"新标题"}` 或 `{"archived":true}`；运行中不能归档 |
| GET | `/sessions/{session}` | 会话运行状态、原 run_id、队列和消息数量 |
| GET | `/sessions/{session}/messages` | 展示消息；`after=0&limit=100` 按数组偏移分页，limit 最大 200 |
| GET | `/sessions/{session}/messages?format=raw` | 原始 history，含完整工具调用和结果；同样分页 |
| GET | `/sessions/{session}/graph` | 已保存图谱快照；最新变化结合事件读取 |
| GET | `/sessions/{session}/executions` | 执行摘要，最近更新在前；limit 默认 100，最大 500 |
| POST | `/sessions/{session}/messages` | `{"text":"任务或补充内容","mode":"queue"}`；需幂等键 |
| POST | `/sessions/{session}/resume` | `{"execution_id":"原执行ID","instruction":"续接要求"}`；需幂等键 |
| POST | `/sessions/{session}/pause` | `{}`；请求停止本会话决策和执行，保留原任务与队列 |
| GET | `/events?after=0&limit=100&session=...` | 分页读取结构化任务事件，limit 最大 500 |
| GET | `/requests/{idempotency_key}` | 查询该提交的接收回执 |

`phase` 的含义：`idle` 空会话、`running` 工作中、`pausing` 正在收尾暂停、`awaiting_input` 等待回答、`paused` 未完成但当前未运行、`completed` CEO 已结束原任务。是否达标仍需看完成报告的具体状态与执行结果。

消息接口默认 `mode=queue`：等当前任务完全结束才处理。`mode=interrupt` 为批次边界引导：等已派发工作汇报后让 CEO 重新决策，不终止正在做的子任务。

当健康检查返回 `worker_hold: true` 时，普通消息提交返回 `recovery_chat: true`。CEO 根据已保存的上下文答复，消息不会加入执行队列。可用 `chat.recovery_user` 和 `chat.recovery_reply` 事件跟踪对话。引导请求返回 HTTP 409。

暂停不同于引导：`/pause` 会给活动执行发取消请求，CEO 在模型请求或当前派发返回后的检查点停止。响应 `202` 仅说明请求已接收，必须继续查状态，直到 `busy=false`。暂停保留普通队列及引导队列，暂停收尾不会自动消费它们。

历史数组会随着新增和上下文压缩变化，`messages.after` 不是稳定事件游标。实时跟踪应使用 `/events` 返回的 `next_cursor`；需要重建完整历史时从偏移 0 重新读取。

## 4. 发送和恢复的幂等语义

启动工作必须带请求头：

```http
Idempotency-Key: research-sessionA-resume-001
```

同一路径、相同 JSON、相同键重试会返回同一回执，响应头包含 `Idempotent-Replay: true`，不会重复启动。不同请求共用一个键返回 409。键为 1–128 个字母、数字或 `_.:-`。

`/requests/{key}` 的 `state` 可为 `accepted`（提交已接收）、`rejected`（提交被拒绝）或 `unknown`（尚未记录提交结果）。查询接口自己的 `ok` 只表示查到了回执。

极端情况下服务可能在提交途中崩溃，API 会保留未知状态，返回 `409 acceptance_unknown`。先查 `/requests/{key}`、会话状态、原执行与事件，核对任务是否已启动。不能仅因网络超时换一个键重发，这可能重复执行。明确收到未接受的 4xx 后，修正请求或等待条件改变，再为新的提交使用新键。

成功提交示例：

```json
{
  "ok": true,
  "queued": false,
  "session": "chat-...",
  "execution_id": "original-execution-id",
  "previous_hermes_session_id": "original-hermes-session-id"
}
```

HTTP 202 表示后台接收，不表示任务完成。排队回执包含 `queue_id`，可用于编辑、撤销或提升为引导。

## 5. 从原中断处恢复

先读取 `/sessions/{session}/executions`，选取原始未完成执行，再发送：

```http
POST /api/v1/sessions/auto-20261102-214253-1e88a3/resume
Authorization: Bearer <CONTROL_TOKEN>
Idempotency-Key: original-task-resume-001
Content-Type: application/json

{
  "execution_id": "2ae78134d64c4abe9876e36f67b09366",
  "instruction": "保留已有推导和文件，先补齐中断执行的报告与独立验收，再继续原目标剩余工作。"
}
```

此接口把恢复要求交给本会话 CEO，明确要求调用 `continue_task`，沿用原 execution_id、节点、profile 和已有 Hermes 会话。如果执行记录能证明故障发生在启动前，且没有持久终端尝试记录，可在同一 execution_id 下首次启动 Hermes。没有上述证据、执行归属不符、执行已完成或会话仍在运行时会拒绝。

`worker_hold=true` 表示事故恢复保护已启用。恢复和引导请求通常返回 409。Windows 运行时可由本地运维在事故保护下核准一个原执行 ID。该节点使用低完整性进程和隔离的 profile 副本，退出后发布核准目录下的新产物。普通消息仅用于与 CEO 对话，读取历史和执行记录仍可用。

省略 execution_id 时选择本会话最近更新、有 Hermes 会话且未完成的执行；正式控制建议显式指定。此便利选取最多检查最近 500 个执行。

本机客户端等价用法：

```powershell
python tools/adgs_control.py resume auto-20261102-214253-1e88a3 --execution 2ae78134d64c4abe9876e36f67b09366 --key original-task-resume-001
```

恢复后核对原 execution_id 出现新的 attempt，且 profile 一致。常驻模式下以 `attempts[].continuation` 区分：`start` 为首次启动，`continue` 为复用存活的原进程，`resume` 为进程确实退出后恢复已有历史。执行的 `phase` 表示当前调度阶段，不能仅凭它推断是否重启过进程。Hermes 后续压缩可能派生新 session_id，此时应按原执行及会话谱系跟踪。

正常发新消息、补充要求或返工，不需要反复调用 `/resume`，也不要模拟终端逐字键入。使用本会话的 `/messages`（默认 `queue`，需要在当前批次结果后引导时用 `interrupt`），由 CEO 调用 `continue_task`，或由负责人调用 `continue_delegation`。运行时会完整、原子地交接消息；活着的原进程自动继续，只有退出或故障时才恢复历史。`/resume` 仍用于空闲会话中断后的显式恢复请求。

`GET /executions/{execution_id}` 额外返回 `hermes_process_alive`、`hermes_process_state` 和 `hermes_process_pid`。状态 `working` 表示处理本轮，`waiting_dispatch` 表示 CEO 已提交决策并等待调度结果，`waiting_input` 表示本轮完成后仍保持进程，`exited` 表示进程已退出。PID 是 Windows 启动进程的 PID，不一定等于原生 Python 子进程 PID。进程空闲存活不等于业务忙，不占工作名额；某个 attempt 完成也不等于整个 CEO 任务完成。请同时核对会话 `busy/task_done`、队列和执行状态，不要用进程是否存在单独判断任务完成。

## 6. 队列、日志与产出物

| 方法 | 路径 | 输入 / 结果 |
| --- | --- | --- |
| GET | `/queue?session={session}` | 该会话待处理队列 |
| PUT | `/queue/{queue_id}` | `{"text":"修改后的完整消息"}` |
| DELETE | `/queue/{queue_id}` | 撤销尚未处理的消息 |
| POST | `/queue/{queue_id}/guide` | `{}`；将同一消息提升为引导，不重复发送 |
| GET | `/executions/{execution_id}` | 执行检查点、任务书、结果、attempts 和结构化事件 |
| POST | `/executions/{execution_id}/cancel` | `{}`；只取消该执行，不等于暂停整个 CEO 会话 |
| GET | `/attempts/{attempt_id}/terminal` | SSE，支持 `?after={seq}` 或 `Last-Event-ID`，回放终端输出 |
| GET | `/attempts/{attempt_id}/download?format=text` | 下载终端记录；也支持 `format=vt` |
| GET | `/delegations?node=ags_651&limit=200` | 节点间委派与复核档案 |
| GET | `/sessions/{session}/files?path=.adgs_artifacts/...` | 目录则返回分页清单，文件则下载；也接受工作区内绝对路径 |

文件接口只读，解析符号链接/路径后仍必须在该会话工作目录内，越界返回 403。目录分页支持 after/limit，limit 最大 200。产出物说明可能含括号注释和多个文件的组合文字，应先提取真实文件路径；不能把整句说明当文件名。

`/events` 提供 `chat.*`、`node.*`、`graph.*`、`a2a.*`，含 `id/type/ts/session/data`。终端原始字节走 attempt SSE，避免巨量终端输出淹没控制事件。取 `has_more=true` 时继续翻页，否则记录 next_cursor，稍后再次查询。游标是全局递增 ID，可以有间隔。

## 7. 全局节点与参数

| 方法 | 路径 | 输入 / 说明 |
| --- | --- | --- |
| GET | `/nodes` | 活动和存档节点、技能信息 |
| GET | `/nodes/{node_id}` | 节点详情 |
| GET | `/nodes/{node_id}/logs?lines=400` | 历史日志，lines 最大 2000；实时执行优先用 attempt 日志 |
| POST | `/nodes/{node_id}/describe` | `{"describe":"准确的专长描述"}` |
| POST | `/nodes/{node_id}/skills/install` | `{"from_node":"ags_123","name":"skill-name"}` |
| POST | `/nodes/{node_id}/skills/remove` | `{"name":"skill-name"}` |
| POST | `/nodes/{node_id}/revive` | `{}`；运行中受原有全局管理保护限制 |
| POST | `/evolve` | `{}`；触发进化，忙碌时拒绝 |
| GET / POST | `/settings` | 查询或更新运行/调优参数 |
| GET / POST | `/models` | 查询或更新 CEO / brain / worker 模型；不返回密钥 |

参数更新示例：

```json
{"advanced":{"sim_weight":3.0,"score_weight":0.2},"runtime":{"max_workers":4}}
```

模型更新按需提交 `{"ceo":{"model":"MODEL_NAME","url":"MODEL_URL","key":"NEW_KEY"}}`，其他组为 brain/worker。省略字段保留原值，key 为空也保留原值。已有 worker profile 保留各自模型，新建 profile 使用更新后的配置。

## 8. Python 调用示例

客户端仅依赖 Python 标准库。外部 AI 可以直接复用：

```python
from tools.adgs_control import ControlClient

client = ControlClient()  # 本机自动读取控制令牌，也可显式传 url/token
health = client.request("GET", "/health")
cursor = health["event_cursor"]
session = "auto-20261102-214253-1e88a3"
receipt = client.request(
    "POST", f"/sessions/{session}/resume",
    {"execution_id": "2ae78134d64c4abe9876e36f67b09366"},
    key="original-task-resume-001",
)
events = client.request("GET", f"/events?session={session}&after={cursor}&limit=100")
status = client.request("GET", f"/sessions/{session}")
```

通用 CLI 支持 JSON 文本或 `@JSON文件`：

```powershell
python tools/adgs_control.py request GET /settings
python tools/adgs_control.py request POST /sessions/SESSION/pause --body '{}'
python tools/adgs_control.py events --session SESSION --after 12345 --limit 100
```

CLI 的 request 命令面向 JSON 接口，文件/SSE 使用支持流式响应的 HTTP 客户端；令牌放请求头，不放 URL。不要把令牌、模型 key 打印到聊天或提交到 Git。

## 9. 错误与状态判断

控制接口错误通常为 `{"ok":false,"error":{"code":"...","message":"..."}}`。401 为认证失败，400 为参数错误，403 为文件越界，404 为资源不存在，409 为运行冲突/幂等冲突，503 为服务暂不可用。宿主启动或代理暂不可用时也可能返回旧格式的字符串 error；控制端应兼容。

网络超时后先查询回执/状态并用同一个幂等键重试。暂时 blocked、unjudged 或 paused 表示工作未完成，要读取实际原因和原执行证据；不得向最终用户报告“任务成功”。

CEO 的 finish.status=answered 表示基于已有信息的答复已结束，没有新的业务执行；不能把它当作新任务验收通过。此类会话 phase 也会是 completed，控制端应同时读取最终事件状态和当前 run 的执行记录。

Worker 连续上下文返工可调用 MCP continue_delegation(parent_execution_id, delegation_id, instruction)，使用原 delegation_id 等待和重新判定，保留 execution/profile/Hermes session。它与 CEO 的 continue_task 对应；独立新上下文仍用 delegate_to_node。内部调试端点为 POST /internal/mesh/delegations/{delegation_id}/continue，沿用 Mesh 令牌，不是 /api/v1 的控制令牌。外部控制 AI 通常通过 messages 给 CEO 具体返工指令，由 CEO/负责人决定续接。

执行详情中的 `waiting_for_children` 表示当前正在等待并归还执行名额；`pending_delegation_reviews` 是该负责人尚未复核的 Mesh 委派 ID 列表。下属 `passed` 仍不代表上级工作完成：负责人需要读取汇报、调用 `report_delegation_verdict` 并完成任务书剩余步骤。未结束或未复核的下属会使父执行保持 `unjudged`，不会作为模型服务故障，也不应重派成新实例。待复核义务在重启后保留，续接原负责人即可继续。`wait_delegation` 显示的本轮派发经过时间包括排队，不是执行超时预算。

如果控制端需要直接读取交付清单，请在任务书中明确写 `DELIVERABLES.txt 必须是纯文件路径清单`：每个非空行一个现有文件的路径，以执行工作区解析相对路径，不混入标题、说明或目录。机械验收会拒绝不存在的文件、目录项、重复路径和空清单，模型的通过结论不能覆盖这些缺项。未明确要求纯路径、或后续明确允许行内说明的旧合同保持兼容。

## 10. 本次实测记录（2026-11-05）

本次通过实际运行中的 `127.0.0.1:8021` 控制 API 接续了用户指定的上一项任务：

| 核对项 | 结果 |
| --- | --- |
| 认证 | 不带令牌请求 `/health` 返回 401；正确认证可发现接口并读取项目、会话和执行 |
| 重启前后历史 | 原会话 187 条历史记录，保存文件 SHA-256 一致 |
| 目标会话 | `auto-20261102-214253-1e88a3` |
| 原 execution_id | `2ae78134d64c4abe9876e36f67b09366` |
| 幂等键 | `control-resume-20261105-ags651-001`，同键重试回放同一回执，无重复派发 |
| CEO 行为 | 对原执行调用 `continue_task`，先盘点已有成果，再补齐中断报告与复核 |
| 新 attempt_id | `088337d1f83543eeb9a775f81fa7b33f` |
| 恢复阶段 | 原执行的 `phase=resume`、`state=running` |
| 节点 / profile | 均沿用 `ags_651` |
| Hermes 会话 | 沿用 `20261105_005447_13de7d` |

这是“原任务已实际恢复运行”的验证，原数学研究任务的最终结果仍需后续报告与验收。不要重复提交本节恢复请求；接手的控制端应先读取会话状态、事件和原执行。测试回执与详细记录保存在本机 `.adgs_runtime/control-api-20261105/`，未包含控制令牌。

代码验证覆盖认证在代理前生效、提交幂等与崩溃窗口、恢复归属与完成状态、事件游标竞争、文件越界、项目会话创建、暂停与队列保留、模型密钥脱敏及原有 CEO/队列行为；相关 80 项测试与 5 个子测试通过，web3 生产构建通过。
