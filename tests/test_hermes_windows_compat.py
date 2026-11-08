import sys
from types import ModuleType, SimpleNamespace

from core.hermes_windows_compat import install_file_search_compat


def test_local_windows_rg_uses_exe_sibling_without_changing_other_backends(tmp_path, monkeypatch):
    root = tmp_path/'space directory';root.mkdir()
    extensionless = root/'rg';extensionless.write_bytes(b'ELF')
    exe = root/'rg.exe';exe.write_bytes(b'MZ')
    class Local: pass
    class Search:
        def _resolve_command(self, command): return self.path
    module = ModuleType('tools.environments.local')
    module.LocalEnvironment = Local;module._IS_WINDOWS = True
    module._msys_to_windows_path = lambda p: str(extensionless) if p == '/c/test/rg' else p
    monkeypatch.setitem(sys.modules, 'tools.environments.local', module)
    monkeypatch.setitem(sys.modules, 'tools.file_operations_search', SimpleNamespace(SearchMixin=Search))
    install_file_search_compat();once=Search._resolve_command;install_file_search_compat()
    assert Search._resolve_command is once
    obj=Search();obj.env=Local();obj.path='/c/test/rg';obj._rg_resolution_cache={}
    assert obj._resolve_command('rg') == str(exe)
    assert obj._rg_resolution_cache['rg'] == str(exe)
    assert obj._resolve_command('git') == '/c/test/rg'
    obj.env=object()
    assert obj._resolve_command('rg') == '/c/test/rg'
    obj.env=Local();obj.path=str(root/'missing')
    assert obj._resolve_command('rg') == str(root/'missing')
