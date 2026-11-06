"""ADGS 进化动力学仿真 — 验证种群收敛（不触碰真实 registry / profile）

模拟 20 轮进化：初始 10 节点 → 每轮繁衍 R=5 → 淘汰按压力函数
检验：种群是否收敛到 K=50，淘汰数量曲线是否合理
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.registry import Node, Registry
from core.evolution import (base_pressure, elimination_count,
                            plan_newborns, select_eliminations,
                            _default_describe)

# 用临时 registry，避免污染 data/registry.json
tmp_path = Path(tempfile.mktemp(suffix="_sim.json"))
reg = Registry(path=tmp_path)
for i in range(10):
    reg.add(Node(f"sim_{i+1}", _default_describe()))

print(f"{'轮':>3} {'种群':>5} {'压力':>8} {'淘汰目标':>6} {'繁衍':>4} {'淘汰':>4} {'新种群':>6}")
history = [reg.count()]
for rnd in range(1, 21):
    n = reg.count()
    pressure = base_pressure(n)
    R = 5  # 固定繁衍数

    # 繁衍（dry：只算计划，不建 profile）
    plans = plan_newborns(reg.active())
    for p in plans:
        reg.add(Node(reg.next_id(), _default_describe(),
                     birth_type=p["birth_type"], parents=p.get("parents", [])))

    # 淘汰（E 基于繁衍前种群）
    E = elimination_count(n)
    elims = select_eliminations(reg.active(), E)
    for e in elims:
        reg.archive_node(e.id)

    n_after = reg.count()
    history.append(n_after)
    print(f"{rnd:>3} {n:>5} {pressure:>8.4f} {elimination_count(n):>6} "
          f"{R:>4} {len(elims):>4} {n_after:>6}")

print("\n最终种群:", history[-1], "| 目标 K=50")
print("收敛判定:", "✅ 收敛到 50±3" if 47 <= history[-1] <= 53 else "⚠️ 未收敛")
