# -*- coding: utf-8 -*-
"""A2A 边对端无实例时画虚拟对端锚点(基于当前实际文本)。"""
from pathlib import Path

p = Path("src/lib/graphCanvas.ts")
s = p.read_text(encoding="utf-8")

old = """    const resolved: { edge: A2aEdge; a: { x: number; y: number }; b: { x: number; y: number }; pk: string; selHit: boolean }[] = [];
    for (const edge of edges) {
      const fromKey = a2aEndpointKey(this.opts.getGraph(), edge.fromNodeId);
      const toKey = a2aEndpointKey(this.opts.getGraph(), edge.toNodeId);
      if (!fromKey || !toKey) continue;
      const a = this.anim.get(fromKey);
      const b = this.anim.get(toKey);
      if (!a || !b) continue;
      resolved.push({ edge, a, b, pk: [fromKey, toKey].sort().join(" "), selHit: sel != null && (fromKey === sel || toKey === sel) });
      pairTotal.set(resolved[resolved.length - 1].pk, (pairTotal.get(resolved[resolved.length - 1].pk) ?? 0) + 1);
    }"""
new = """    const resolved: { edge: A2aEdge; a: { x: number; y: number }; b: { x: number; y: number }; pk: string; selHit: boolean }[] = [];
    const virtualAnchors: { x: number; y: number; id: string }[] = [];
    // 端点解析:有实例用实例位置;无实例(委派对端不在当前 CEO 树)→ 稳定伪随机虚拟锚点,
    // 让跨任务的 A2A 委派关系依然可见(2026-10-23 用户需求)
    const anchorOf = (nodeId: string, near: { x: number; y: number } | null): { x: number; y: number; virtual: boolean } => {
      const key = a2aEndpointKey(this.opts.getGraph(), nodeId);
      const pos = key ? this.anim.get(key) : undefined;
      if (pos) return { x: pos.x, y: pos.y, virtual: false };
      let h = 0;
      for (const ch of nodeId) h = (h * 31 + ch.charCodeAt(0)) | 0;
      const ang = ((Math.abs(h) % 360) * Math.PI) / 180;
      const r = 170 + (Math.abs(h) % 110);
      const cx = near ? near.x : 640;
      const cy = near ? near.y : 300;
      return { x: cx + Math.cos(ang) * r, y: cy + Math.sin(ang) * r, virtual: true };
    };
    for (const edge of edges) {
      const fromKey = a2aEndpointKey(this.opts.getGraph(), edge.fromNodeId);
      const toKey = a2aEndpointKey(this.opts.getGraph(), edge.toNodeId);
      const first = anchorOf(edge.fromNodeId, null);
      const second = anchorOf(edge.toNodeId, { x: first.x, y: first.y });
      if (fromKey == null || !this.anim.get(fromKey)) {
        virtualAnchors.push({ x: first.x, y: first.y, id: edge.fromNodeId });
      }
      if (toKey == null || !this.anim.get(toKey)) {
        virtualAnchors.push({ x: second.x, y: second.y, id: edge.toNodeId });
      }
      const a = { x: first.x, y: first.y };
      const b = { x: second.x, y: second.y };
      if (Math.hypot(b.x - a.x, b.y - a.y) < 8) continue;
      resolved.push({ edge, a, b, pk: [edge.fromNodeId, edge.toNodeId].sort().join(" "), selHit: sel != null && (fromKey === sel || toKey === sel) });
      pairTotal.set(resolved[resolved.length - 1].pk, (pairTotal.get(resolved[resolved.length - 1].pk) ?? 0) + 1);
    }"""
assert old in s, "resolved anchor missing"
s = s.replace(old, new)

old3 = """        ctx.fillStyle = escalated ? "rgba(244,158,83,0.95)" : "rgba(62,207,127,0.9)";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, mid.x, mid.y);
      }
      ctx.restore();
    }
  }"""
new3 = """        ctx.fillStyle = escalated ? "rgba(244,158,83,0.95)" : "rgba(62,207,127,0.9)";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, mid.x, mid.y);
      }
      ctx.restore();
    }
    // 虚拟对端锚点：不在当前任务树的委派参与方(小圆点 + 节点 id)
    const seen = new Set<string>();
    for (const v of virtualAnchors) {
      if (seen.has(v.id)) continue;
      seen.add(v.id);
      ctx.save();
      ctx.beginPath();
      ctx.arc(v.x, v.y, 7, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(24,30,42,0.95)";
      ctx.fill();
      ctx.strokeStyle = "rgba(139,233,255,0.7)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.font = "600 10px ui-monospace, monospace";
      ctx.fillStyle = "rgba(139,233,255,0.85)";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(v.id, v.x, v.y + 11);
      ctx.restore();
    }
  }"""
assert old3 in s, "tail anchor missing"
s = s.replace(old3, new3)
p.write_text(s, encoding="utf-8")
print("patched")
