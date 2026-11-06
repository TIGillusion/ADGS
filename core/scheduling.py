"""Dependency-ready scheduling: completing A can start C while unrelated B is running."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from core.session_context import submit_context
import os
import posixpath
import re
from pathlib import Path


def validate_new_artifacts(task, workspace=None, parent_permissions=""):
    """Reject existing destinations when permission only allows new artifacts."""
    permissions = str(parent_permissions or "") + "\n" + str(task.get("permissions") or "")
    if not re.search(r"(?:仅|只)允许新建|new files only|create.only", permissions, re.I):
        return
    for name in task.get("write_paths") or []:
        if any(c in name for c in "*?["):
            raise ValueError("仅新建任务必须声明具体新文件路径，不能使用通配符")
        path = Path(name)
        if not path.is_absolute():
            path = Path(workspace or os.getcwd()) / path
        if path.exists():
            raise ValueError(f"仅新建任务的产物路径已存在，必须选择新文件名: {path}")


def write_resources(task, workspace=None):
    """Normalize file/directory claims; unknown writers reserve their workspace."""
    root = str(workspace or os.getcwd()).replace("\\", "/")
    root = posixpath.normpath(root).rstrip("/").casefold()
    permissions = str(task.get("permissions") or "").strip().casefold()
    readonly = (permissions in ("read only", "read-only", "readonly", "只读")
                or permissions.startswith("只读；") or permissions.startswith("只读，")
                or permissions.startswith("只读;")
                or permissions.startswith("不得修改任何文件")
                or permissions.startswith("不修改任何文件"))
    paths = task.get("write_paths")
    if readonly:
        if paths:
            raise ValueError("只读任务不能声明写路径")
        return frozenset()
    if paths is None or paths == []:
        return frozenset([root])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("write_paths 必须是非空路径字符串数组")
    normalized = []
    for path in paths:
        path = path.strip().replace("\\", "/")
        # Glob declarations reserve the static containing directory.
        if any(c in path for c in "*?["):
            prefix = re.split(r"[*?\[]", path, 1)[0]
            path = prefix if prefix.endswith("/") else posixpath.dirname(prefix)
            path = path or "."
        if not (path.startswith("/") or re.match(r"^[a-zA-Z]:/", path)):
            path = posixpath.join(root, path)
        normalized.append(posixpath.normpath(path).rstrip("/").casefold())
    return frozenset(normalized)


def artifact_dir(workspace, run_id: str) -> str:
    """ADGS 产物归置目录：<workspace>/.adgs_artifacts/<run_id>/。

    执行报告、工作日志、交接文档、中间产物统一放这里——不散落工作区根目录，
    不污染用户项目结构（2026-10-24 用户要求）。目录在工作区内，worker 权限天然覆盖；
    runtime 侧通过执行存档的 report 路径集中索引。"""
    root = str(workspace or os.getcwd()).replace("\\", "/").rstrip("/")
    return f"{root}/.adgs_artifacts/{run_id or 'adhoc'}"


def external_resources(task) -> frozenset:
    """外部互斥资源声明（shared_resources）：真实设备/端口/远端服务等。

    write_paths 只能表达工作区文件冲突，两个子任务并行操作同一台物理设备
    会互相打架（2026-10-24 米家实验实测：两个节点各自"先开后关"同一插座，
    时间上侥幸错开）。声明相同资源名的子任务被调度串行化。"""
    values = task.get("shared_resources")
    if values is None or values == []:
        return frozenset()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("shared_resources 必须是非空资源名数组")
    return frozenset("ext:" + v.strip().casefold() for v in values)


def resources_conflict(left, right):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
               for a in left for b in right)


def validate_dependencies(tasks):
    if not isinstance(tasks, list) or not tasks or any(not isinstance(t, dict) for t in tasks):
        raise ValueError("子任务必须是非空对象列表")
    deps = []
    for index, task in enumerate(tasks):
        values = task.get("depends_on", [])
        if not isinstance(values, list) or any(type(v) is not int or v < 0 or v >= len(tasks)
                                              or v == index for v in values):
            raise ValueError(f"子任务 {index} 的依赖非法")
        deps.append(set(values))
    remaining = set(range(len(tasks)))
    done = set()
    while remaining:
        ready = {i for i in remaining if deps[i] <= done}
        if not ready:
            raise ValueError("子任务存在循环依赖")
        done.update(ready)
        remaining.difference_update(ready)
    return deps


def dependency_results(tasks, submit, max_workers, workspace=None):
    deps = validate_dependencies(tasks)
    for task in tasks:
        validate_new_artifacts(task, workspace)
    resources = [write_resources(t, workspace) | external_resources(t) for t in tasks]
    results, pending, active = {}, set(range(len(tasks))), {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        while pending or active:
            for index in sorted(pending.copy()):
                if not deps[index] <= results.keys():
                    continue
                if len(active) >= max(1, max_workers):
                    break
                if any(resources_conflict(resources[index], resources[other])
                       for other in active.values()):
                    continue
                pending.remove(index)
                failed = [d for d in deps[index] if not results[d].get("success")]
                if failed:
                    results[index] = {"success": False, "outcome": "blocked", "unjudged": True,
                                      "name": tasks[index].get("name", ""),
                                      "error": f"依赖任务尚未通过: {failed}"}
                else:
                    def guarded_submit(task_index=index, completed=dict(results)):
                        validate_new_artifacts(tasks[task_index], workspace)
                        return submit(task_index, completed)
                    active[submit_context(pool, guarded_submit)] = index
            if active:
                # Propagate newly blocked chains without waiting on unrelated work.
                if any(deps[i] <= results.keys() and
                       not any(resources_conflict(resources[i], resources[j]) for j in active.values())
                       for i in pending) and len(active) < max(1, max_workers):
                    continue
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    index = active.pop(future)
                    try:
                        results[index] = future.result()
                    except Exception as exc:
                        results[index] = {"success": False, "outcome": "interrupted", "unjudged": True,
                                          "name": tasks[index].get("name", ""), "error": str(exc)}
    return [results[i] for i in range(len(tasks))]
