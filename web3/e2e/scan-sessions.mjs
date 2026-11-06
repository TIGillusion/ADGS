// 扫描所有会话文件的图谱污染情况（旧前端总览 dump 特征：多个 ceo 节点 / session:: 前缀 key）
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const dir = "D:\\user_illusion\\test\\ADGS\\data\\sessions";
const rows = [];
for (const f of readdirSync(dir).sort()) {
  if (!f.endsWith(".json")) continue;
  let d;
  try {
    d = JSON.parse(readFileSync(join(dir, f), "utf8"));
  } catch {
    rows.push({ f, err: "parse-fail" });
    continue;
  }
  const g = d.graph ?? [];
  const ceos = g.filter((n) => n.type === "ceo");
  const nonCanonicalCeos = ceos.filter((n) => n.key !== "ceo");
  const prefixed = g.filter((n) => typeof n.key === "string" && n.key.includes("::"));
  const prefixes = new Set();
  for (const n of g) {
    const m = /^([^:]*?)(?=::)/.exec(n.key ?? "");
    if (m) prefixes.add(m[1]);
  }
  rows.push({
    f: f.replace(".json", ""),
    nodes: g.length,
    ceos: ceos.length,
    foreignCeos: nonCanonicalCeos.length,
    prefixed: prefixed.length,
    sessions: prefixes.size,
    dirty: g.length >= 5 && (nonCanonicalCeos.length > 0 || prefixes.size > 1),
  });
}
const dirty = rows.filter((r) => r.dirty);
console.log(`共 ${rows.length} 个会话文件，被污染的 ${dirty.length} 个：\n`);
console.log("file | 节点 | ceo数 | 前缀key | 涉及会话");
for (const r of rows.filter((r) => r.dirty || r.err)) {
  if (r.err) {
    console.log(`${r.f} 解析失败`);
    continue;
  }
  console.log(`${r.f} | ${r.nodes} | ${r.ceos} | ${r.prefixed} | ${r.sessions}`);
}
console.log("\n干净（无外部 ceo、无前缀）的文件数:", rows.filter((r) => !r.dirty && !r.err).length);
