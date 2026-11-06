"""ADGS v3 全局配置单例 — 加载 AI_config.json + settings.yaml"""
import json
import sys
import threading
from pathlib import Path
import yaml

# 确保项目根在 sys.path（模块被 core/ 或直接运行时）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

# 运行时调优白名单（2026-09-28 web 系统设置）：settings_overrides.json 只允许
# 覆盖这些 section.key，其余字段改了也不生效（防误写破坏种群参数）。
TUNING_WHITELIST = {
    "pressure": {"tau"},
    "router": {"sim_weight", "score_weight", "explore_beta", "top_k", "temperature"},
    "agent": {"max_workers", "vllm_concurrency", "max_delegation_depth"},
    "hermes": {"worker_timeout", "inactivity_timeout"},
}


class CONFIG:
    def __init__(self, debug: int = 3):
        self.DEBUG = debug
        self.PROJECT_ROOT = PROJECT_ROOT
        self.DATA_DIR = DATA_DIR
        self.AI_config_path = CONFIG_DIR / "AI_config.json"
        self.settings_path = CONFIG_DIR / "settings.yaml"
        self.settings_overrides_path = CONFIG_DIR / "settings_overrides.json"
        self.workspace_path = DATA_DIR / "workspace.json"
        self._workspace = None
        # 模型配置热更新锁（set_models 与 load/读取并发保护）
        self._ai_lock = threading.RLock()
        self.load()
        self.load_workspace()
        # 2026-08-30 P3：启动时确保工作区就位（未设置则落盘一次）——
        # 此后 workspace property 只读，不再每次读取都触发写盘
        # （GET /api/state 等只读接口意外落盘 workspace.json 的副作用）
        _ = self.workspace

    # ---------------- 工作区 ----------------

    def load_workspace(self):
        """加载已保存的工作区路径"""
        try:
            if self.workspace_path.exists():
                import json
                data = json.loads(self.workspace_path.read_text(encoding="utf-8"))
                self._workspace = data.get("path") or None
        except Exception:
            self._workspace = None

    def set_workspace(self, path: str) -> str:
        """设置并保存工作区（确保目录存在，返回规范化路径）"""
        import json
        from pathlib import Path as P
        p = P(path).resolve()
        p.mkdir(parents=True, exist_ok=True)
        self._workspace = str(p)
        self.workspace_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_path.write_text(
            json.dumps({"path": self._workspace}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return self._workspace

    @property
    def workspace(self) -> str:
        """当前工作区（未设置时返回默认路径；只读，不落盘——
        初始化由 __init__ 启动时保证；显式切换走 set_workspace）"""
        from core.session_context import runtime_workspace
        scoped = runtime_workspace.get()
        if scoped:
            return scoped
        if self._workspace:
            return self._workspace
        return str(PROJECT_ROOT / "data" / "workspace")

    def load(self):
        """加载模型配置 + 种群参数"""
        with open(self.AI_config_path, "r", encoding="utf-8") as f:
            self.AI_config = json.load(f)
        with open(self.settings_path, "r", encoding="utf-8") as f:
            self.settings = yaml.safe_load(f)
        # web 系统设置的调优覆盖（存在才合并；必须在属性解析前）
        self._load_overrides()

        # --- 模型分组 ---
        ceo = self.AI_config["ceo"]
        brain = self.AI_config["brain"]
        worker = self.AI_config["worker"]

        # CEO（唯一用户入口，强推理）
        self.ceo_url = ceo["url"]
        self.ceo_model = ceo["model"]
        self.ceo_key = ceo["key"]

        # 节点智能体（Leader）
        self.brain_url = brain["url"]
        self.brain_model = brain["model"]
        self.brain_key = brain["key"]

        # hermes 执行模型
        self.worker_url = worker["url"]
        self.worker_model = worker["model"]
        self.worker_key = worker["key"]

        # --- 种群参数 ---
        pop = self.settings["population"]
        self.initial_size = pop["initial_size"]
        self.capacity = pop["capacity"]
        self.reproduction_rate = pop["reproduction_rate"]
        self.whiteboard_ratio = pop["whiteboard_ratio"]
        self.recovery_coef = pop["recovery_coef"]

        pres = self.settings["pressure"]
        self.tau = pres["tau"]

        crow = self.settings["crowding"]
        self.crowding_sigma = crow["sigma"]
        self.sim_threshold = crow["sim_threshold"]
        self.crowding_penalty = crow.get("penalty", 0.5)  # 拥挤度影响系数（0.5=减半）

        # 力量三：分数进淘汰选择（相对弱度抗通胀；0=旧行为纯拥挤度）
        elim = self.settings.get("elimination", {})
        self.elimination_score_weight = float(elim.get("score_weight", 0.0))

        # 力量四：承载量随种群能力扩张（方案 A）+ 新节点动态初始分
        cg = self.settings.get("capacity_growth", {})
        self.capacity_growth_enabled = bool(cg.get("enabled", False))
        self.capacity_growth_slope = float(cg.get("slope", 1.0))
        self.capacity_max = int(cg.get("max", 200))
        self.capacity_min = int(cg.get("min", self.capacity))
        self.newborn_score_percentile = float(cg.get("newborn_score_percentile", 0.60))
        self._capacity_ema = None  # 承载量 EMA（跨轮平滑）

        rt = self.settings["router"]
        self.top_k = rt["top_k"]
        self.temperature = rt["temperature"]
        # Fit decides eligibility first; historical quality is a bounded correction.
        self.router_sim_weight = float(rt.get("sim_weight", 3.0))
        self.router_score_weight = float(rt.get("score_weight", 0.2))
        # PUCT 探索系数（2026-09-28：高分节点垄断任务→冷启动节点接不到任务
        # 成长不起来）。加成按 tasks_done 衰减，且不超过 1+beta。
        # 旧配置无字段时默认 0.15；设 0 关闭探索回退纯利用。
        self.router_explore_beta = float(rt.get("explore_beta", 0.15))

        ag = self.settings["agent"]
        self.correction_limit = ag["correction_limit"]
        self.rework_limit = ag["rework_limit"]
        self.max_workers = ag["max_workers"]
        # vLLM 服务器并发上限：排队是正常现象不是错误。生效并行度 =
        # min(max_workers, vllm_concurrency) —— 把排队从 vLLM 层挪到 ADGS 派发层，
        # 避免 hermes 等响应时无输出被无活动超时误杀（实测 600s Inactivity 误杀）。
        # 旧配置无此字段时退回 max_workers（不限制）。
        self.vllm_concurrency = ag.get("vllm_concurrency") or self.max_workers
        self.max_workers_effective = min(self.max_workers, self.vllm_concurrency)
        # 节点间递归委派最大深度（防无限拆分；到顶强制自己执行）。.get 兼容旧配置
        self.max_delegation_depth = ag.get("max_delegation_depth", 5)
        # 节点大脑合并（A2A，2026-10-22 设计）：worker(hermes) 直接承担拆分/委派决策，
        # 经 MCP 工具与其他节点协作；关闭时走旧 AGENT.decide 决策路径（可回退）。
        self.node_merged_brain = bool(ag.get("merged_brain", False))

        hm = self.settings["hermes"]
        self.worker_timeout = hm["worker_timeout"]
        self.inactivity_timeout = hm["inactivity_timeout"]
        # 长任务超时（宽松值；任务书含长文/大规模关键词时自动使用）
        self.long_task_timeout = hm.get("long_task_timeout", 7200)

        sc = self.settings["scoring"]
        self.ema_alpha = sc["ema_alpha"]
        self.describe_max_len = sc["describe_max_len"]
        # 评分增量系数：节点智能体打分(-10~10) × coef 累加旧分
        self.delta_coef = float(sc.get("delta_coef", 0.2))

        cp = self.settings["compress"]
        self.ctx_window = cp["ctx_window"]
        self.compress_trigger = cp["trigger_ratio"]
        self.compress_budget = cp["budget_ratio"]
        # 上下文窗口（2026-08-30 修复）：默认直接使用 settings.yaml 预写值
        # （probe_context=false 不再自动探测——探测 force 忽略缓存、vLLM 不通时
        # 每次启动卡 ~6 分钟、DeepSeek 白烧 token）。需要重测时手动跑
        # utils/context_probe.py（__main__ 入口）并把结果写回 settings.yaml。
        self.ceo_ctx_window = cp.get("ceo_ctx_window") or self.ctx_window
        self.brain_ctx_window = cp.get("brain_ctx_window") or self.ctx_window
        self.worker_ctx_window = cp.get("worker_ctx_window") or self.ctx_window
        if cp.get("probe_context", False):
            try:
                from utils.context_probe import probe_context_window
                p = probe_context_window(self.ceo_url, self.ceo_model, self.ceo_key,
                                         force=True)
                if p:
                    self.ceo_ctx_window = p
                p = probe_context_window(self.brain_url, self.brain_model, self.brain_key,
                                         force=True)
                if p:
                    self.brain_ctx_window = p
                p = probe_context_window(self.worker_url, self.worker_model, self.worker_key,
                                         force=True)
                if p:
                    self.worker_ctx_window = p
                # 向后兼容：ctx_window = CEO 上下文（compress.py 用于 CEO 历史压缩）
                self.ctx_window = self.ceo_ctx_window
            except Exception:
                pass  # 探测失败保留配置值，不阻塞启动
        # CEO compression uses its role window whether or not probing is enabled.
        self.ctx_window = self.ceo_ctx_window

    def set_models(self, changes: dict) -> dict:
        """运行时更新模型配置（内存即时生效 + AI_config.json 落盘，重启保留）

        2026-09-27 web 模型配置入口。changes 形如
          {"ceo": {"model": ..., "url": ..., "key": ...},
           "brain": {...}, "worker": {...}}
        每组/每字段均可省略，省略 = 保持不变；key 传空 = 保持旧 key。
        只接受 model/url/key 三个字段（其余字段如 worker.context_length 不动）。

        返回实际生效的 {"ceo": {"model","url"}, ...}（不含 key，可直接回给前端）。
        """
        allowed = ("model", "url", "key")
        applied = {}
        with self._ai_lock:
            for group in ("ceo", "brain", "worker"):
                ch = changes.get(group)
                if not isinstance(ch, dict):
                    continue
                entry = {f: str(ch[f]).strip() for f in allowed
                         if ch.get(f) and str(ch[f]).strip()}
                if not entry:
                    continue
                cur = self.AI_config[group]
                cur.update(entry)
                applied[group] = {"model": cur["model"], "url": cur["url"]}
            if not applied:
                return {}
            # 同步内存属性（与 load() 保持一致；读取方按单属性读，GIL 原子）
            self.ceo_url, self.ceo_model, self.ceo_key = (
                self.AI_config["ceo"]["url"], self.AI_config["ceo"]["model"],
                self.AI_config["ceo"]["key"])
            self.brain_url, self.brain_model, self.brain_key = (
                self.AI_config["brain"]["url"], self.AI_config["brain"]["model"],
                self.AI_config["brain"]["key"])
            self.worker_url, self.worker_model, self.worker_key = (
                self.AI_config["worker"]["url"], self.AI_config["worker"]["model"],
                self.AI_config["worker"]["key"])
            self._persist_ai_config()
        return applied

    def _persist_ai_config(self):
        """原子写回 AI_config.json（含 API key，文件已被 .gitignore 排除）"""
        self.AI_config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.AI_config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.AI_config, ensure_ascii=False, indent=4),
                       encoding="utf-8")
        tmp.replace(self.AI_config_path)

    # ---------------- 运行/调优参数（2026-09-28 web 系统设置） ----------------

    def _load_overrides(self):
        """把 settings_overrides.json 的白名单键合并进 self.settings。

        独立覆盖文件而非改写 settings.yaml：①不毁 yaml 里的手写注释；
        ②git 不入库（用户本地调优，不入版本库）；③避免与手工编辑/
        并发会话互相覆盖整份文件。仅在 load()（启动/refresh）时合并一次。
        """
        try:
            if not self.settings_overrides_path.exists():
                return
            ov = json.loads(self.settings_overrides_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[config] settings_overrides.json 读取失败（忽略）: {e}")
            return
        if not isinstance(ov, dict):
            return
        for sec, keys in TUNING_WHITELIST.items():
            src = ov.get(sec)
            dst = self.settings.get(sec)
            if not isinstance(src, dict) or not isinstance(dst, dict):
                continue
            for k in keys:
                v = src.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    dst[k] = v

    def _persist_overrides(self):
        """白名单调优参数原子写盘（重启保留；仅写与默认不同的意图集合——
        直接整份白名单写出，简单且幂等）"""
        ov = {}
        for sec, keys in TUNING_WHITELIST.items():
            dst = self.settings.get(sec) or {}
            kv = {k: dst[k] for k in keys
                  if isinstance(dst.get(k), (int, float))
                  and not isinstance(dst.get(k), bool)}
            if kv:
                ov[sec] = kv
        self.settings_overrides_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_overrides_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.settings_overrides_path)

    def set_tuning(self, runtime: dict, advanced: dict):
        """运行时更新调优参数（内存即时生效 + settings_overrides.json 落盘）。

        runtime: max_workers（全局并行上限；同时写 max_workers/vllm_concurrency
                 → max_workers_effective，信号量由调用方 resize）、
                 max_delegation_depth、worker_timeout、inactivity_timeout
        advanced: tau（生存压力）、sim_weight/score_weight（路由：专精/得分）、
                  explore_beta（探索系数）、top_k、temperature
        全部字段每轮进化/每次派发实时读 config 属性，改完即对下一轮/下一任务
        生效，无需重启。返回 (changed_runtime, changed_advanced)。
        """
        changed_r, changed_a = {}, {}
        with self._ai_lock:
            if "max_workers" in runtime:
                n = int(runtime["max_workers"])
                self.settings["agent"]["max_workers"] = n
                self.settings["agent"]["vllm_concurrency"] = n
                self.max_workers = n
                self.vllm_concurrency = n
                self.max_workers_effective = n
                changed_r["max_workers"] = n
            if "max_delegation_depth" in runtime:
                n = int(runtime["max_delegation_depth"])
                self.settings["agent"]["max_delegation_depth"] = n
                self.max_delegation_depth = n
                changed_r["max_delegation_depth"] = n
            if "worker_timeout" in runtime:
                n = int(runtime["worker_timeout"])
                self.settings["hermes"]["worker_timeout"] = n
                self.worker_timeout = n
                changed_r["worker_timeout"] = n
            if "inactivity_timeout" in runtime:
                n = int(runtime["inactivity_timeout"])
                self.settings["hermes"]["inactivity_timeout"] = n
                self.inactivity_timeout = n
                changed_r["inactivity_timeout"] = n
            if "tau" in advanced:
                v = float(advanced["tau"])
                self.settings["pressure"]["tau"] = v
                self.tau = v
                changed_a["tau"] = v
            for k in ("sim_weight", "score_weight", "explore_beta"):
                if k in advanced:
                    v = float(advanced[k])
                    self.settings["router"][k] = v
                    setattr(self, f"router_{k}", v)
                    changed_a[k] = v
            if "top_k" in advanced:
                n = int(advanced["top_k"])
                self.settings["router"]["top_k"] = n
                self.top_k = n
                changed_a["top_k"] = n
            if "temperature" in advanced:
                v = float(advanced["temperature"])
                self.settings["router"]["temperature"] = v
                self.temperature = v
                changed_a["temperature"] = v
            if changed_r or changed_a:
                self._persist_overrides()
        return changed_r, changed_a

    def refresh(self):
        self.load()


config = CONFIG()
