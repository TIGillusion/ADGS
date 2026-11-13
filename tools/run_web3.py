"""Launch a source distribution using its two freshly installed virtualenvs."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def hermes_python(root):
    explicit = os.environ.get('ADGS_HERMES_PYTHON')
    if explicit:
        return Path(explicit)
    bundled = root / 'vendor' / 'hermes-agent' / 'venv' / ('Scripts' if os.name == 'nt' else 'bin') / ('python.exe' if os.name == 'nt' else 'python')
    if bundled.is_file():
        return bundled
    executable = Path(shutil.which('hermes') or '')
    if executable.name:
        candidates = [
            executable.parent / ('python.exe' if os.name == 'nt' else 'python'),
            executable.parent.parent / 'bin' / 'python',
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return bundled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check installation files without starting services')
    parser.add_argument('--local', action='store_true', help='Run tasks inside the web process')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    scripts = 'Scripts' if os.name == 'nt' else 'bin'
    python = 'python.exe' if os.name == 'nt' else 'python'
    backend = root / '.venv' / scripts / python
    native = hermes_python(root)
    required = [backend, native, native.parent.parent.parent / 'hermes_cli/main.py',
                root / 'config/AI_config.json', root / 'web3/dist/index.html']
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print('Installation incomplete. See INSTALL.md. Missing:\n' + '\n'.join(missing), file=sys.stderr)
        return 1
    if args.check:
        print('Installation files present. This check does not call model APIs.')
        return 0
    env = dict(os.environ, ADGS_HERMES_PYTHON=str(native.absolute()),
               ADGS_HERMES_RESIDENT=os.environ.get('ADGS_HERMES_RESIDENT', '1'),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env['PATH'] = str(native.absolute().parent) + os.pathsep + env.get('PATH', '')
    command = [str(backend), '-u', str(root / 'web_app.py')]
    if args.local:command.append('--local')
    try:
        return subprocess.call(command, cwd=root, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
