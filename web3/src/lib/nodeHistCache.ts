// ============================================================
// 节点磁盘历史日志缓存（NodeDrawer 抽屉回退用）— 模块级，跨抽屉打开/关闭保留
//
// 值 null = 已确认磁盘无该节点日志（避免重复请求）。但该结论有时效性：
// 节点之后执行任务会产生新日志，null 与旧尾部都必须失效——store 在节点
// 活动事件（graph.dispatch / node.status / node.eval）时调 nodeHistInvalidate。
// version + 订阅：抽屉开着时缓存被失效，组件能立即重查（不必关开抽屉）。
// ============================================================

export interface NodeHist {
  hermes: string[] | null;
  agentlog: string[] | null;
  ts: number;
  truncated: boolean;
}

const cache = new Map<string, NodeHist | null>();
let version = 0;
const listeners = new Set<() => void>();

export function nodeHistGet(nodeId: string): NodeHist | null | undefined {
  return cache.get(nodeId);
}

export function nodeHistSet(nodeId: string, v: NodeHist | null): void {
  cache.set(nodeId, v);
  if (cache.size > 200) cache.delete(cache.keys().next().value!);
}

/** 节点有新活动：磁盘日志结论过期，清缓存并唤醒订阅组件重查 */
export function nodeHistInvalidate(nodeId: string): void {
  if (!cache.delete(nodeId)) return;
  version += 1;
  for (const fn of listeners) fn();
}

export function nodeHistSubscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function nodeHistVersion(): number {
  return version;
}
