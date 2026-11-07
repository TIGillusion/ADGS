import type { ExecutionDetail, GraphNodeStatus } from "@/types";

/** Canonical graph states; aliases never become a success by default. */
export function graphStatus(value: unknown): GraphNodeStatus {
  switch (value) {
    case "idle": return "idle";
    case "queued": case "starting": case "waiting": return "waiting";
    case "running": case "executing": return "running";
    case "evaluating": return "evaluating";
    case "delegating": case "waiting_children": return "delegating";
    case "waiting_user": case "awaiting_user": return "waiting_user";
    case "passed": case "completed": case "succeeded": case "done": return "done";
    case "failed": return "failed";
    case "paused": case "interrupted": case "cancelled": case "infra_failure": case "protocol_error": return "interrupted";
    case "evaluation_unavailable": case "unjudged": return "unjudged";
    case "context_overflow": case "blocked": return "blocked";
    default: return "unknown";
  }
}

export function executionGraphStatus(execution: Pick<ExecutionDetail,"state"|"phase"|"waiting_for_children"|"waiting_for_slot"|"runtime_active">): GraphNodeStatus {
  const status = graphStatus(execution.state);
  if (status !== "running" && status !== "waiting" && status !== "delegating" && status !== "evaluating") return status;
  if (execution.runtime_active === false) return "interrupted";
  if (execution.waiting_for_children) return "delegating";
  if (execution.waiting_for_slot || ["queued","waiting_slot"].includes(execution.phase || "")) return "waiting";
  if (["coordinating","delegation"].includes(execution.phase || "")) return "delegating";
  if (["acceptance","evaluation","evaluating"].includes(execution.phase || "")) return "evaluating";
  return status;
}

export type GraphTone = "dim" | "life" | "warn" | "gene" | "pulse" | "ok" | "danger";
export const GRAPH_STATUS: Record<GraphNodeStatus,{label:string;color:string;tone:GraphTone}> = {
  idle:{label:"待命",color:"--canvas-idle",tone:"dim"},
  waiting:{label:"排队",color:"--canvas-waiting",tone:"warn"},
  running:{label:"执行中",color:"--canvas-running",tone:"life"},
  delegating:{label:"等待下属",color:"--canvas-delegating",tone:"gene"},
  evaluating:{label:"验收中",color:"--canvas-evaluating",tone:"pulse"},
  waiting_user:{label:"等待补充",color:"--canvas-waiting",tone:"warn"},
  done:{label:"完成",color:"--canvas-done",tone:"ok"},
  failed:{label:"未通过",color:"--canvas-failed",tone:"danger"},
  interrupted:{label:"中断",color:"--canvas-waiting",tone:"warn"},
  unknown:{label:"待核实",color:"--canvas-idle",tone:"dim"},
  unjudged:{label:"待验收",color:"--canvas-waiting",tone:"warn"},
  blocked:{label:"受阻",color:"--canvas-waiting",tone:"warn"},
};

export const activeGraphStatus = (status: GraphNodeStatus) => ["waiting","running","delegating","evaluating"].includes(status);
