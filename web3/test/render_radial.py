# -*- coding: utf-8 -*-
"""渲染 test/.radial_dump.json → PNG（楔形扇区 + 旋转节点矩形 + 父子边），目视验证行星布局。"""
import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

D = json.load(open(r"D:\user_illusion\test\ADGS\web3\test\.radial_dump.json", encoding="utf-8"))
laid = {l["key"]: l for l in D["laid"]}
wedges = {w["key"]: w for w in D["wedges"]}

TYPE_COLOR = {"ceo": "#f5c96b", "round": "#8be9ff", "task": "#a5edff", "node": "#7ee0a3"}
fig, ax = plt.subplots(figsize=(15, 15), dpi=110)
ax.set_facecolor("#050a14")
fig.set_facecolor("#050a14")

# 1) 顶层楔形扇区（轮次的角区间，浅色填充）
for w in D["wedges"]:
    if not w["key"].startswith("round-"):
        continue
    theta1 = math.degrees(w["a0"])
    theta2 = math.degrees(w["a1"])
    ax.add_patch(
        mpatches.Wedge((0, 0), w["r"] + 130, theta1, theta2, width=w["r"] + 130,
                       fc="#3a5a8a", alpha=0.10, ec="none", zorder=1)
    )

# 2) 父子边
for l in D["laid"]:
    p = l["parent"]
    if not p:
        continue
    if p not in laid:
        p = "ceo"
    if p not in laid:
        continue
    a, b = laid[p], l
    ax.plot([a["x"], b["x"]], [-a["y"], -b["y"]], color="#4f74b3", lw=1.0, alpha=0.8, zorder=2)

# 3) 节点矩形（随枝向旋转）
for l in D["laid"]:
    col = TYPE_COLOR.get(l["type"], "#9fb4d8")
    tr = (
        matplotlib.transforms.Affine2D().rotate_deg_around(l["x"], -l["y"], -math.degrees(l["rot"]))
        + ax.transData
    )
    box = mpatches.FancyBboxPatch(
        (l["x"] - l["w"] / 2, -(l["y"] + l["h"] / 2)),
        l["w"], l["h"],
        boxstyle="round,pad=0,rounding_size=4",
        fc="#0d1a28", ec=col, lw=1.2, zorder=3,
    )
    box.set_transform(tr)
    ax.add_patch(box)
    ax.text(l["x"], -l["y"], l["key"][:16], ha="center", va="center", color="#dfe9ff",
            fontsize=4.4, zorder=4, rotation=-math.degrees(l["rot"]) if l["type"] != "task" else 0)

ax.set_aspect("equal")
ax.autoscale_view()
ax.set_title("ADGS galaxy layout (wedge packing, rot-aligned)", color="#9fb4d8", fontsize=12)
ax.axis("off")
out_png = r"D:\user_illusion\test\ADGS\web3\test\.radial_render.png"
fig.savefig(out_png, bbox_inches="tight", facecolor=fig.get_facecolor())
out_svg = r"D:\user_illusion\test\ADGS\web3\test\.radial_render.svg"
fig.savefig(out_svg, bbox_inches="tight", facecolor=fig.get_facecolor())
print("saved", out_png, out_svg, "nodes =", len(D["laid"]))
