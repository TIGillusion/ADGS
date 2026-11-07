import type { GraphState } from "@/lib/graph";

export const FLOW_SIGNAL_MS = 3000;
/** Particles represent recent durable message events, never a queued job's lifetime. */
export function edgeSignal(graph: GraphState, from: string, to: string, now: number, live: boolean) {
  const signal = graph.flow?.signals?.[`${from}\u0000${to}`];
  if (!live || !signal) return null;
  const age = now - signal.ts * 1000;
  return age >= 0 && age < FLOW_SIGNAL_MS ? {...signal, age} : null;
}
