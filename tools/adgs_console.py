"""Attach a visible console to an existing ADGS runtime without restarting it."""
import argparse
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from web.console_log import run_console


def main():
    parser=argparse.ArgumentParser(description='ADGS 实时运行日志（只读，不启动或停止任务）')
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--tail',type=int,default=20,help='启动时回放最近多少条摘要，默认 20')
    args=parser.parse_args()
    if args.tail<0 or args.tail>200:parser.error('--tail 范围为 0–200')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8',errors='replace')
    run_console(state_dir=args.state_dir,tail=args.tail)


if __name__=='__main__':main()
