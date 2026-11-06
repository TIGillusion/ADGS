// 污染文件修复前置分析：
// 1. 列出无 "会话::" 前缀的节点（本会话自己的内容候选）
// 2. 验证每个前缀的节点是否在该前缀对应的会话文件里有冗余副本
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const dir = "D:\\user_illusion\\test\\ADGS\\data\\sessions";
const d = JSON.parse(readFileSync(join(dir, "auto-20260828-133107.json"), "utf8"));
const g = d.graph;
console.log("history 条数:", (d.history ?? []).length, "| saved_at:", d.saved_at);

// 1. 无前缀节点
const unprefixed = g.filter((n) => !/^([^:]*)::/.test(n.key ?? ""));
console.log(`\n-- 无前缀节点: ${unprefixed.length} --`);
for (const n of unprefixed) console.log(`  ${n.type} key=${n.key} parent=${n.parent} label=${n.label}`);

// 2. 前缀 → 对应会话文件里是否有同 key（剥前缀后）节点
const prefixes = new Map();
for (const n of g) {
  const m = /^([^:]*?)::/.exec(n.key ?? "");
  if (!m) continue;
  const p = m[1];
  if (!prefixes.has(p)) prefixes.set(p, new Set());
  prefixes.get(p).add(n.key);
}
console.log(`\n-- 前缀冗余校验（${prefixes.size} 个前缀）--`);
let allRedundant = true;
for (const [p, keys] of [...prefixes.entries()].sort()) {
  const fp = join(dir, `${p}.json`);
  if (!existsSync(fp)) {
    console.log(`  ${p}: 无对应会话文件! ${keys.size} 个节点仅在污染文件中`);
    allRedundant = false;
    continue;
  }
  const fd = JSON.parse(readFileSync(fp, "utf8"));
  const fkeys = new Set((fd.graph ?? []).map((n) => n.key));
  // 剥前缀后比对
  const missing = [...keys].filter((k) => !fkeys.has(k.replace(/^[^:]*?::/, "")));
  if (missing.length) {
    console.log(`  ${p}: 文件存在但缺 ${missing.length} 个节点（前 ${Math.min(3, missing.length)}: ${missing.slice(0, 3)}）`);
    allRedundant = false;
  }
}
console.log(allRedundant ? "✅ 所有前缀数据在各自会话文件中都有副本 → 污染文件完全冗余，可安全清空" : "⚠️ 存在无副本数据，需保留备份");

// 3. 本会话自己的节点
const own = g.filter((n) => (n.key ?? "").startsWith("auto-20260828-133107::"));
console.log(`\n-- 本会话 (auto-20260828-133107) 自己的节点: ${own.length} --`);
for (const n of own) console.log(`  ${n.type} key=${n.key} parent=${n.parent}`);
