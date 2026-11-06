"""Start a scoped Windows worker after the terminal host assigns its job."""
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    import msvcrt
    import win32api
    import win32con
    import win32event
    import win32process
    import win32security

    gate = Path(sys.argv[1])
    deadline = time.monotonic() + 30
    while not gate.is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError("受限执行器未完成进程树绑定")
        time.sleep(0.05)

    argv = sys.argv[2:]
    current = win32api.GetCurrentProcess()
    source = win32security.OpenProcessToken(current, win32con.TOKEN_ALL_ACCESS)
    token = win32security.CreateRestrictedToken(source, 1, [], [], [])
    low_sid = win32security.ConvertStringSidToSid("S-1-16-4096")
    win32security.SetTokenInformation(token, win32security.TokenIntegrityLevel, (low_sid, 32))
    actual_sid, _ = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
    if win32security.ConvertSidToStringSid(actual_sid) != "S-1-16-4096":
        raise RuntimeError("Hermes 低完整性令牌检查失败")

    startup = win32process.STARTUPINFO()
    startup.dwFlags = win32con.STARTF_USESTDHANDLES
    handles = []
    try:
        for key, stream in (("hStdInput", sys.stdin), ("hStdOutput", sys.stdout), ("hStdError", sys.stderr)):
            handle = win32api.DuplicateHandle(
                current, msvcrt.get_osfhandle(stream.fileno()), current, 0, True,
                win32con.DUPLICATE_SAME_ACCESS)
            handles.append(handle)
            setattr(startup, key, handle)
        process, thread, _, _ = win32process.CreateProcessAsUser(
            token, argv[0], subprocess.list2cmdline(argv), None, None, True,
            0x08000000 | win32con.CREATE_UNICODE_ENVIRONMENT, dict(os.environ), os.getcwd(), startup)
        thread.Close()
        try:
            win32event.WaitForSingleObject(process, win32event.INFINITE)
            return win32process.GetExitCodeProcess(process)
        finally:
            process.Close()
    finally:
        for handle in handles:
            handle.Close()
        token.Close()
        source.Close()


if __name__ == "__main__":
    sys.exit(main())
