# ags_651 缺报告后整轮提前停止

真实执行：`2ae78134d64c4abe9876e36f67b09366`，Hermes 会话 `20261105_005447_13de7d`，用户会话 `auto-20261102-214253-1e88a3`。

## 证据与原因

- 事件 3036852 记录进程退出 0 / completed，但没有完整终结报告；进程退出不等于任务完成。
- 检查点总预算为 6000 秒。事件 3036854 宣称进行第一次确认，随后没有新 attempt 启动事件；补报告时执行预算已经耗尽。
- `_execute_attempt` 的早退缺少 `termination_reason`，`_work_merged` 又覆盖之前的 log 并丢掉真实错误，统一报成“确认 1 次后仍无报告”。
- `_unjudged` 把它标为 infra_failure，CEO 因此在 split_tasks 返回后直接 blocked，没继续决定如何恢复；界面只显示“任务结束（blocked）”并隐藏 error。
- 检查点仍保留 18306 字符 work_log 和原 session_id；返回 result 的 work_log 当时为空，未发生工作文件删除。

## 修复与验证

- 确认前检查预算，预算耗尽明确标记 wall_timeout，CEO 可继续调用原执行的 continue_task，不把它当成服务故障直接停止。
- 失败确认保留前序日志、原会话 ID、实际错误和 termination_reason；正常退出但两次仍缺报告返回 unjudged，让 CEO 继续判断和处理。
- blocked/exception 显示“本轮执行暂停，任务尚未完成”及原因；重新载入会话也恢复最后的暂停事件。
- 前端 194 项测试和构建通过。后端相关回归原有 103 项及 5 个子测试通过，修正新测试的桩后，mesh 专项 19 项通过，包含预算耗尽、确认失败保留原因及原成果的回归。
- 确认 busy=false、队列为 0、本次 run 没有活动 worker 后备份当前会话，再重开宿主加载修复（也加载上一轮路由和委派修改）。重开前后会话文件 SHA256 完全一致。
- 备份与校验值保存在 `.adgs_runtime/closure-fix-20261105/`；没有向 CEO 自动发送继续任务。
