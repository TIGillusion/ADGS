import type { GraphState } from "@/lib/graph";
import { flowFromLegacy } from "@/lib/graphFlow";
import { ceoGraphStatus } from "@/lib/ceo";
import { activeGraphStatus, executionGraphStatus } from "@/lib/executionState";
import type { GraphNode, StateSnapshot } from "@/types";

/** Snapshots confirm state without creating instructions, message traffic or fake events. */
export function reconcileGraphSnapshot(graph: GraphState, snapshot: StateSnapshot): GraphState {
  const states = new Map(snapshot.executions?.map(record=>[record.execution_id,record]));
  const observed = snapshot.snapshot_at || 0;
  const flow = graph.flow ?? flowFromLegacy(graph);
  const update = (n:GraphNode):GraphNode => {
    if(n.type==="ceo" && snapshot.ceo) {
      const old=n.extra.ceo_activity, incoming=snapshot.ceo;
      if(old?.execution_id===incoming.execution_id && (old?.updated_at||0)>(incoming.updated_at||0)) return n;
      return {...n,status:ceoGraphStatus(incoming.phase),extra:{...n.extra,ceo_activity:incoming,execution_id:incoming.execution_id||undefined,attempt_id:incoming.attempt_id}};
    }
    if(n.type!=="node" || (observed>0 && observed<(n.extra.statusTs||0))) return n;
    const record=states.get(n.extra.execution_id || "");
    if(record && record.node_id!==n.id) return n;
    const status=record && record.node_id===n.id ? executionGraphStatus(record)
      : snapshot.executions && !snapshot.busy && activeGraphStatus(n.status) ? "unknown" : n.status;
    if(!record && status===n.status) return n;
    const extra={...n.extra,statusTs:observed || n.extra.statusTs};
    if(status==="done") extra.passed=true;
    else if(status==="failed") extra.passed=false;
    else delete extra.passed;
    return {...n,status,extra};
  };
  const nodes=flow.nodes.map(update);
  const byKey=new Map(nodes.map(n=>[n.key,n]));
  // Only the latest input of a reused execution receives its current outcome.
  const activeBatches=new Map<string,string>();
  for(const n of nodes) if(n.type==="node") activeBatches.set(n.key,n.extra.active_batch || [...flow.links].reverse().find(l=>l.to===n.key)?.from || n.parent || "");
  const projected=nodes.map(n=>!n.extra.contracts ? n : {...n,extra:{...n.extra,contracts:n.extra.contracts.map(c=>{
    const child=c.instanceKey ? byKey.get(c.instanceKey) : undefined;
    return child && activeBatches.get(child.key)===n.key ? {...c,status:child.status} : c;
  })}});
  return {...graph,nodes:graph.nodes.map(update),flow:{...flow,nodes:projected}};
}
