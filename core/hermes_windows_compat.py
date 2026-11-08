"""Local Windows compatibility for the installed Hermes file-search adapter."""
import functools
from pathlib import Path


def install_file_search_compat():
    from tools.environments.local import LocalEnvironment, _IS_WINDOWS, _msys_to_windows_path
    if not _IS_WINDOWS:
        return
    from tools.file_operations_search import SearchMixin
    original = SearchMixin._resolve_command
    if getattr(original, '_adgs_windows_rg', False):
        return

    @functools.wraps(original)
    def resolve(self, command):
        path = original(self, command)
        if command == 'rg' and path and isinstance(self.env, LocalEnvironment) and not path.lower().endswith('.exe'):
            # Git Bash can resolve an extensionless Linux binary shipped beside
            # the Windows rg.exe. Native file tools must use the Windows sibling.
            executable = Path(_msys_to_windows_path(path) + '.exe')
            if executable.is_file():
                path = str(executable)
                self._rg_resolution_cache['rg'] = path
        return path

    resolve._adgs_windows_rg = True
    SearchMixin._resolve_command = resolve
