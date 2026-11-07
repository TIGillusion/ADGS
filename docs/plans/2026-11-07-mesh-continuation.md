# 下属原会话返工的实测缺口

真实测试中的理论审查需要在原下属上下文中补正。现有 CEO 有 continue_task，而 MCP 只提供 delegate_to_node，失败判定还提示重新委派，不能满足“连续上下文返工保留节点实例、只增加下属轮次”的既定要求。

补充 continue_delegation(parent_execution_id, delegation_id, instruction)：原父执行续接自己的原委派，保留 delegation、execution、profile、Hermes session、任务书和已有结果；新增 attempt 和输入轮次。当前正在执行或排队的同一子执行不能重复恢复；父取消向下联动，名额仍由同一调度器管理。新上下文独立工作继续使用 delegate_to_node。

MCP、内部控制端点和协调者角色入口均需支持；继续委派使用原 wait/verdict 接口。既往结果与判定保存在原记录及事件中。本批并行恢复共享父批次，父轮次不变，子输入轮次增加，Web3 使用既有 graph.instruction/context_mode=continue 投影。

验证覆盖原 ID/会话恢复、越过原父执行拒绝、运行中重复拒绝、无原上下文不猜测、资源互斥、排队与父取消、事件身份和图谱单实例；最后用真实理论修正复测。
