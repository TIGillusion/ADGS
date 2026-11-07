import type { CeoActivity, GraphNodeStatus } from "@/types";

export const CEO_PHASES: Record<string, string> = {
  idle: "空闲", deciding: "制定计划", inspecting: "只读了解项目",
  waiting_children: "等待下属汇报", reviewing: "审阅汇报", reporting: "汇报进展",
  awaiting_user: "等待你的回复", completed: "本轮完成", answered: "答复完成", blocked: "受阻待恢复",
  failed: "任务未通过", paused: "已暂停", interrupted: "中断待恢复",
};
export function ceoPhaseLabel(activity?: CeoActivity | null, busy = false): string {
  return CEO_PHASES[activity?.phase || ""] || (activity?.phase ? "阶段待核实" : busy ? "协调任务" : "空闲");
}
export function ceoGraphStatus(phase?: string): GraphNodeStatus {
  if (phase === "waiting_children") return "delegating";
  if (["deciding", "inspecting", "reviewing", "reporting"].includes(phase || "")) return "running";
  if (phase === "completed" || phase === "answered") return "done";
  if (phase === "blocked" || phase === "failed") return phase;
  if (phase === "paused" || phase === "interrupted") return "interrupted";
  if (phase === "awaiting_user") return "waiting_user";
  return !phase || phase === "idle" ? "idle" : "unknown";
}
