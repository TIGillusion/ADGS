import time
import os
from datetime import datetime

import sys
if sys.platform == "win32":
    os.system('color')

def get_date():
    local_time = time.localtime()
    formatted_date = time.strftime("%Y-%m-%d", local_time)
    return formatted_date

def get_detail_date():
    # 获取当前日期和时间
    now = datetime.now()
    # 格式化日期和时间
    formatted_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_datetime

class LOGGER():
    def __init__(self, level = 5, log_path = None):
        self.level = level
        self.color_dict = {
            "green": "\033[92m",
            "red": "\033[91m",
            "yellow": "\033[93m",
            "blue": "\033[94m",
            "magenta": "\033[95m",
            "cyan": "\033[96m",
            "gray": "\033[90m",
            "white": "\033[97m",
            "reset": "\033[0m"
        }
        self.debug_color = self.color_dict["gray"]
        if not log_path:
            log_path = "./log"
        self.log_path = f"{log_path}/{get_date()}.log"
        if not os.path.exists(self.log_path):
            if not os.path.exists("./log"):
                os.mkdir("./log")
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write("")
        self.new_log()

    def color_print(self, color, string):
        print(color+f"[{get_detail_date()}]"+string+self.color_dict["reset"])

    def add_log(self, string):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(string+"\n")

    def debug_print(self, string, debug_level):
        if debug_level <= self.level:
            self.color_print(self.debug_color, f"<{debug_level}>"+string)
            self.add_log(string)

    def new_log(self):
        self.add_log(f"\n\n\n\n\n========={get_detail_date()}=========\n")

    def info(self, string):
        self.color_print(self.color_dict["blue"], string)
        self.add_log(string)

    def error(self, string):
        self.color_print(self.color_dict["red"], string)
        self.add_log(string)
    
    def warning(self, string):
        self.color_print(self.color_dict["yellow"], string)
        self.add_log(string)

        
logger = LOGGER()

if __name__ == "__main__":
    
    logger.debug_print("这是灰色的日志信息", 5)
