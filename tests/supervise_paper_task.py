"""监工模式 v2：CEO 提问时代替用户自动回复，持续推动任务直到完成

我不修改任何系统代码/配置，只扮演"用户"角色应答 CEO 的 ask_user。
同一 CEO 实例持续对话（保留上下文）。
"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import config
from core.ceo import CEO

WS = r"D:\user_illusion\test\test\AI论文"

TASK = f"""请自主完成以下论文研究任务（工作区：{WS}）：

【任务目标】
阅读工作区中的论文《忆阻器仿生特性及多层感知机神经网络研究》，理解其研究方向与核心算法，
然后基于论文当前算法提出改进方案，要求：
1. 改进兼容当前工艺/技术（忆阻器器件可实现）
2. 满足论文研究方向
3. 实现与论文同等的 O(1) 推理效率（利用论文的巧妙思路，如查表/闭式解/几何关系等）
4. 需要做大量实验验证，切实完成研究

【论文材料】
- 论文全文：{WS}/汇总/忆阻器仿生特性及多层感知机神经网络研究论文.docx（或 .pdf）
- 分章材料：{WS}/汇总/第一章~第五章/（含图表）
- 代码材料：{WS}/汇总/主代码.zip、{WS}/汇总/ppf仿真.zip（已解压到 主代码_extracted/ 和 ppf仿真_extracted/）

【执行要求】
1. 通读论文，提取：研究方向、算法完整描述、O(1) 推理核心巧思、实验设置、工艺约束
2. 形成技术理解报告（markdown，写入工作区）
3. 提出改进算法方案（原理、兼容工艺、保持 O(1)）
4. 建立 Python 实验环境，实现原算法与改进算法对比实验
5. 输出完整实验结果报告（数据、图表、结论）

【执行策略】
- 分多轮小步骤：每轮只做 1-2 个文件读取/分析，避免超时
- 大文件分章节片段读取，不要一次性全读
- 自主推进，不要频繁提问；可自行决策的问题自行处理
- 产物写入工作区 {WS}/"""

# 用户风格自动应答（依次使用；用完则复用最后一条）
USER_ANSWERS = [
    """确认：1) 工作区路径正确，汇总/ 目录、论文 docx、主代码.zip、ppf仿真.zip 都在；2) 改进方向=保持 O(1) 前提下的精度+鲁棒性；3) 基线=论文原算法；4) 算力有限，MNIST 小规模验证即可。
论文理解和改进方案已完成（工作区已有 改进算法方案.md、论文技术要点笔记.md），不要重复读论文。直接进入实验实现：勘查 Python 环境 → 实现原算法与改进算法对比 → 跑 MNIST 实验 → 输出结果报告。""",
    """论文理解已经充分（要点笔记+改进方案+代码分析都完成了）。现在直接进入实验实现阶段：
1) 勘查 Python 环境（numpy/matplotlib 即可，原论文用 numpy 实现电路仿真）
2) 实现原算法（EP-MLP）与改进算法（DSQ+NLCM 等）的对比实验
3) 用 MNIST 小规模验证（论文同款配置更佳），跑出训练曲线和准确率对比
4) 输出完整实验结果报告（数据+图表+结论）到工作区
不要再重复读论文/读代码了，直接开始写实验代码并运行。""",
    """按你的方案继续，我信任你的拆解。大文件读不动就提取关键部分（摘要、方法、实验、结论）。持续推进到完成实验和报告。""",
]


def main():
    print(f"工作区: {config.set_workspace(WS)}", flush=True)
    from core.registry import registry
    print(f"活动节点: {registry.count()}", flush=True)

    ceo = CEO()
    print("[监工] 任务已提交，CEO 提问时自动应答...\n", flush=True)

    answer_idx = 0
    turn = 0
    t0 = time.time()
    last_reply = ""
    while turn < 30:
        turn += 1
        # 第一轮发任务，后续发用户应答（若上一轮是提问）
        if turn == 1:
            msg = TASK
        else:
            msg = USER_ANSWERS[min(answer_idx, len(USER_ANSWERS) - 1)]
            answer_idx += 1

        print(f"\n{'='*56}\n[轮次 {turn}] → {msg[:60]}...", flush=True)
        try:
            reply = ceo.talk(msg)
        except KeyboardInterrupt:
            print("\n[监工] 被中断", flush=True)
            break
        except Exception as e:
            print(f"\n[监工] CEO 异常: {type(e).__name__}: {e}", flush=True)
            break

        reply = str(reply)
        print(f"[CEO 返回]（累计 {time.time()-t0:.0f}s）\n{reply[:400]}", flush=True)
        last_reply = reply

        # 工作区产物观察（只读）
        md_files = sorted(Path(WS).glob("*.md"))
        print(f"[监工] 工作区 md 产物: {[f.name for f in md_files]}", flush=True)

        # 是否提问
        is_asking = ("提问" in reply) or ("选项" in reply and "？" in reply)
        if not is_asking:
            # CEO 给了实质答复/汇报 —— 继续观察，稍后再推
            print("[监工] CEO 未提问（可能在等节点执行），等待 60s 观察...", flush=True)
            time.sleep(60)

        # 完成判定：检测到"实验结果"类文件（非理解类报告），且最近 10 分钟新增
        import time as _t
        done = False
        for f in Path(WS).rglob("*.md"):
            if any(k in f.name for k in ("实验结果", "实验报告", "accuracy", "对比", "final", "结果分析")):
                if _t.time() - f.stat().st_mtime < 600:
                    print(f"\n[监工] 检测到实验结果产物: {f.name}", flush=True)
                    done = True
                    break
        if done:
            break

    print(f"\n[监工] 观察结束，总耗时 {time.time()-t0:.0f}s", flush=True)
    print("\n=== 最后 CEO 回复 ===", flush=True)
    print(last_reply[:1000], flush=True)
    print("===END===", flush=True)


if __name__ == "__main__":
    main()
