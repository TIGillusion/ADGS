"""ADGS v3 main.py — CLI 入口（增强交互版）

启动流程：
  1. 设置工作区（--workspace 参数 / 交互式输入 / 复用已保存）
  2. 初始化节点池或触发进化（每轮新对话）
  3. CEO 对话循环 + 快捷指令

快捷指令（类似 hermes 的 /slash 命令）：
  /workspace <path>   切换工作区
  /debug <0-3>        设置调试级别（3=输出所有大模型回复+最详细日志）
  /save [name]        保存当前对话
  /load <name>        恢复对话
  /sessions           列出已保存会话
  /history            查看当前对话历史
  /help               显示帮助
  /quit               退出
"""
import argparse
import sys
from pathlib import Path

# 确保项目根在 sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.config import config
from core.registry import registry
from core.evolution import evolve
from core.ceo import CEO
from utils.chat import set_debug_level, get_debug_level
import backup_training

# 初始统一描述（所有节点平等起点）
INIT_DESCRIBE = ("通用智能体节点：具备完整的任务规划、代码编写、测试验证与"
                 "文档输出能力，可执行各类开发与研究任务。")

HELP_TEXT = """可用快捷指令：
  /workspace <path>   切换工作区（如 /workspace D:\\my_project）
  /debug <0-3>        调试级别：0=关闭 1=工具概要 2=+耗时 3=+完整模型回复+节点hermes输出
  /save [name]        保存当前对话（默认名 auto）
  /load <name>        恢复指定对话
  /sessions           列出所有已保存会话
  /history            查看当前对话历史
  /backup [--all] [--models]  打包当前训练状态（节点种群+技能基因）
  /restore [<zip>]    从备份包恢复训练状态（不填路径会提示输入）
  /help               显示本帮助
  /quit               退出
其他输入直接作为任务描述发给 CEO。"""


def _stdin_pending() -> bool:
    """检测 stdin 是否有已粘贴但尚未读取的输入（多行粘贴支持）。

    Windows 控制台（conhost）下，粘贴多行文本时 input() 只消费第一行，
    剩余行留在控制台输入缓冲，msvcrt.kbhit() 可检测到。
    非 Windows / 管道环境退回 select 检测（管道下通常返回 False，退化为单行）。
    """
    try:
        if sys.platform == "win32":
            import msvcrt
            return bool(msvcrt.kbhit())
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except Exception:
        return False


def read_task_input(prompt: str = "\n> ") -> str:
    """读取一行任务输入；检测到粘贴的多行内容时一次性读完并拼接。

    这样用户可以把一段多行任务描述直接粘贴到终端，作为整体发给 CEO，
    而不是被拆成多行分别当成命令。
    """
    first = input(prompt)
    if not _stdin_pending():
        return first
    lines = [first]
    while _stdin_pending():
        lines.append(input())
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="ADGS v3 — 智能体达尔文进化系统")
    parser.add_argument("--workspace", "-w", type=str, default=None,
                        help="工作区目录（所有节点在此目录内工作）")
    parser.add_argument("--debug", "-d", type=int, default=0,
                        help="调试级别 0-3（3=最详细日志）")
    return parser.parse_args()


def setup_workspace(args) -> str:
    """设置工作区：参数优先 → 已保存复用 → 交互式输入"""
    # 1) 命令行参数
    if args.workspace:
        ws = config.set_workspace(args.workspace)
        print(f"[workspace] 已设置工作区: {ws}")
        return ws

    # 2) 已保存的工作区
    saved = getattr(config, "_workspace", None)
    if saved and Path(saved).exists():
        print(f"[workspace] 复用上次工作区: {saved}")
        return saved

    # 3) 交互式输入
    default = str(config.PROJECT_ROOT / "data" / "workspace")
    try:
        inp = input(f"[workspace] 请输入工作区目录（回车用默认 {default}）: ").strip()
    except (EOFError, KeyboardInterrupt):
        inp = ""
    ws = config.set_workspace(inp if inp else default)
    print(f"[workspace] 已设置工作区: {ws}")
    return ws


def banner():
    print("=" * 56)
    print("  ADGS v3 — 智能体达尔文进化系统")
    print(f"  种群: {registry.count()}/{config.capacity}  |  "
          f"CEO: {config.ceo_model}")
    print(f"  工作区: {config.workspace}  |  调试级别: {get_debug_level()}")
    print("  输入任务描述开始，/help 查看指令")
    print("=" * 56)


def handle_command(cmd: str, args: list, ceo: CEO) -> bool:
    """处理快捷指令。返回 True=继续，False=退出"""
    if cmd == "/quit" or cmd in ("q", "quit", "exit"):
        print("再见")
        return False

    if cmd == "/help":
        print(HELP_TEXT)

    elif cmd == "/workspace":
        if not args:
            print(f"当前工作区: {config.workspace}")
            print("用法: /workspace <路径>")
        else:
            ws = config.set_workspace(args[0])
            print(f"已切换工作区: {ws}")

    elif cmd == "/debug":
        if not args:
            print(f"当前调试级别: {get_debug_level()}（0-3）")
        else:
            try:
                lv = int(args[0])
                set_debug_level(lv)
                print(f"调试级别已设为 {get_debug_level()}"
                      + ("（最详细：输出所有大模型回复）" if lv >= 3 else ""))
            except ValueError:
                print("用法: /debug <0-3>")

    elif cmd == "/save":
        name = args[0] if args else "auto"
        print(ceo.save_conversation(name))

    elif cmd == "/load":
        if not args:
            print("用法: /load <会话名>（/sessions 查看已有）")
        else:
            print(ceo.load_conversation(args[0]))

    elif cmd == "/sessions":
        print(ceo.list_sessions())

    elif cmd == "/history":
        print(ceo.show_history())

    elif cmd == "/backup":
        print("[backup] 打包当前训练状态...")
        try:
            out = backup_training.do_backup(
                all_profiles=("--all" in args),
                include_models=("--models" in args),
                full_profile=("--full-profile" in args))
            print(f"[backup] 完成 -> {out}")
        except Exception as e:
            print(f"[backup] 失败: {type(e).__name__}: {e}")

    elif cmd == "/restore":
        # 有参数则拼成路径（支持含空格），否则交互式索要
        if args:
            path = " ".join(args).strip().strip('"').strip("'")
        else:
            path = input("[restore] 请输入备份 zip 路径: ").strip().strip('"').strip("'")
        if not path:
            print("[restore] 未输入路径，已取消")
        else:
            print(f"[restore] 从 {path} 恢复中...")
            try:
                summary = backup_training.do_restore(path)
                registry.load()  # 重新加载注册表，让新种群立即生效
                print(f"[restore] 完成，当前活动节点: {registry.count()}/{config.capacity}")
                print("[restore] 若后续路由或进化异常，请重启 main.py")
            except Exception as e:
                print(f"[restore] 失败: {type(e).__name__}: {e}")

    else:
        print(f"未知指令: {cmd}（/help 查看所有指令）")

    return True


def main():
    args = parse_args()
    setup_workspace(args)
    set_debug_level(args.debug)

    # 首次运行初始化节点池
    if registry.count() == 0:
        registry.ensure_initial(INIT_DESCRIBE)
        print(f"[init] 初始节点池创建完成: {registry.count()} 个节点")
    else:
        # 每轮新对话：先触发繁衍淘汰
        report = evolve()
        print(f"[进化] 繁衍 {len(report['births'])} 个, "
              f"淘汰 {len(report['eliminations'])} 个, "
              f"种群 {report['n_before']} → {report['n_after']}")

    banner()
    ceo = CEO()

    while True:
        try:
            line = read_task_input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not line:
            continue

        # 2026-08-30 P2-19：裸 q/quit/exit 直接退出（此前会被当任务发给 CEO）
        if line.lower() in ("q", "quit", "exit"):
            print("再见")
            break

        # 快捷指令
        if line.startswith("/"):
            parts = line.split()
            cmd, cmd_args = parts[0], parts[1:]
            if not handle_command(cmd, cmd_args, ceo):
                break
            continue

        print("\n[CEO 处理中...]")
        try:
            reply = ceo.talk(line)
        except KeyboardInterrupt:
            print("\n（已中断当前处理，可继续输入）")
            continue
        except Exception as e:
            print(f"\n[异常] {type(e).__name__}: {e}")
            continue
        print("\n" + reply)


if __name__ == "__main__":
    main()
