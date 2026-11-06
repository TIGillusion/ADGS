// 剖析 auto-20260828-133107.json 的图谱结构：节点类型分布 / ceo 节点 / 轮次归属
import { readFileSync } from "node:fs";
const f = "D:\\user_illusion\\test\\ADGS\\data\\sessions\\auto-20260828-133107.json";
const d = JSON.parse(readFileSync(f, "utf8"));
const g = d.graph ?? [];
console.log("会话:", d.name, "保存于:", d.saved_at, "history条数:", (d.history ?? []).length, "节点数:", g.length);

const byType = {};
for (const n of g) byType[n.type] = (byType[n.type] ?? 0) + 1;
console.log("\n-- 节点类型分布 --");
console.log(JSON.stringify(byType, null, 1));

// ceo 节点（type/label/id 任一含 ceo）
const ceos = g.filter((n) => n.type === "ceo" || /ceo/i.test(n.label ?? "") || /ceo/i.test(n.id ?? ""));
console.log(`\n-- ceo 相关节点: ${ceos.length} 个 --`);
for (const c of ceos.slice(0, 40)) {
  console.log(`  key=${c.key} | type=${c.type} | label=${c.label} | parent=${c.parent} | status=${c.status}`);
}

// 轮次的 parent 分布
const rounds = g.filter((n) => n.type === "round");
const roundParents = {};
for (const r of rounds) roundParents[r.parent] = (roundParents[r.parent] ?? 0) + 1;
console.log("\n-- 轮次 parent 分布 --");
console.log(JSON.stringify(roundParents, null, 1));

// 轮次 key 的会话前缀（auto-xxx::round-N 格式）
const prefixes = {};
for (const r of rounds) {
  const m = /^([^:]*)::/.exec(r.key);
  const p = m ? m[1] : r.key;
  prefixes[p] = (prefixes[p] ?? 0) + 1;
}
console.log("\n-- 轮次 key 的会话前缀 --");
console.log(JSON.stringify(prefixes, null, 1));

// 悬空 parent（引用的 key 不存在）
const keys = new Set(g.map((n) => n.key));
const dangling = {};
for (const n of g) if (n.parent && !keys.has(n.parent)) dangling[n.parent] = (dangling[n.parent] ?? 0) + 1;
const entries = Object.entries(dangling).sort((a, b) => b[1] - a[1]);
console.log(`\n-- 悬空 parent: ${entries.length} 种（前 15） --`);
for (const [k, c] of entries.slice(0, 15)) console.log(`  ${k}  × ${c}`);

// 各"会话前缀"下挂多少节点（全部类型）
const perPrefix = {};
for (const n of g) {
  // 节点本身带前缀
  let m = /^([^:]*)::/.exec(n.key);
  let p = m ? m[1] : null;
  // 或父链可归因
  if (!p && n.parent) {
    const m2 = /^([^:]*)::/.exec(n.parent);
    p = m2 ? m2[1] : null;
  }
  if (p) perPrefix[p] = (perPrefix[p] ?? 0) + 1;
}
console.log("\n-- 按会话前缀统计节点数 --");
console.log(JSON.stringify(perPrefix, null, 1));
