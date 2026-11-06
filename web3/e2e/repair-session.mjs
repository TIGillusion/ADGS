// 数据修复：auto-20260828-133107.json 总览污染清理
// 步骤：① 备份原文件 → ② 仅保留本会话自身节点（无 "::" 前缀；
// 本会话名前缀的非 ceo 节点剥前缀保留）③ 原子写回 ④ 校验父引用闭合
// 运行前提：后端已重启（新拒写防护生效）且该会话无任务在执行。
import { readFileSync, writeFileSync, copyFileSync, renameSync, existsSync } from "node:fs";
import { join } from "node:path";

const dir = "D:\\user_illusion\\test\\ADGS\\data\\sessions";
const name = "auto-20260828-133107";
const path = join(dir, `${name}.json`);
const bak = path + ".bak-20260903";

if (!existsSync(path)) { console.error("文件不存在"); process.exit(1); }
if (existsSync(bak)) { console.error("备份已存在，中止（防重复覆盖）"); process.exit(1); }

const d = JSON.parse(readFileSync(path, "utf8"));
const before = d.graph ?? [];
const ownPrefix = name + "::";

const kept = [];
for (const n of before) {
  const k = n?.key;
  if (typeof k !== "string" || !k) continue;
  if (k.includes("::")) {
    if (k.startsWith(ownPrefix) && n.type !== "ceo") {
      // 本会话名::前缀的残留（旧前端自身残留）：剥前缀保留
      const m = { ...n, key: k.slice(ownPrefix.length) };
      if (m.parent && m.parent.startsWith(ownPrefix)) m.parent = m.parent.slice(ownPrefix.length);
      kept.push(m);
    }
    // 外部会话前缀（含 xxx::ceo）→ 丢弃（各自会话文件有副本）
  } else {
    kept.push(n);
  }
}

// 父引用闭合校验：每个保留节点的 parent 必须为 null 或在保留集内
const keptKeys = new Set(kept.map((n) => n.key));
const dangling = kept.filter((n) => n.parent != null && n.parent !== "ceo" && !keptKeys.has(n.parent));
if (dangling.length) {
  console.error(`中止：${dangling.length} 个保留节点父引用悬空:`, dangling.map((n) => `${n.key}→${n.parent}`).slice(0, 5));
  process.exit(1);
}

const dry = process.argv.includes("--dry-run");
if (dry) {
  console.log(`[dry-run] 不写盘。`);
  console.log(`   当前: ${before.length} 节点 → 保留: ${kept.length}（丢弃 ${before.length - kept.length} 个外部会话节点）`);
  console.log(`   保留键:`, kept.map((n) => n.key).join(", "));
  process.exit(0);
}

// ① 备份 ② 写回（tmp + rename 原子替换，与后端 atomic_write_session 同语义）
copyFileSync(path, bak);
d.graph = kept;
const tmp = path + ".tmp";
writeFileSync(tmp, JSON.stringify(d, null, 2), "utf8");
renameSync(tmp, path);

console.log(`✅ 修复完成`);
console.log(`   备份: ${bak} (${before.length} 节点)`);
console.log(`   保留: ${kept.length} 节点（丢弃 ${before.length - kept.length} 个外部会话节点）`);
console.log(`   history: ${(d.history ?? []).length} 条（未动）`);
console.log(`   保留键:`, kept.map((n) => n.key).join(", "));
