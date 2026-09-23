"""state.py · 运行状态落盘（上传记录 + 执行记录 + 任务）—— 无数据库，纯 JSON 文件。

职责只有三件（别让它长胖）：
    ① **上传记录**：`data/uploads/` 里存了哪些文件、哈希多少、多少行多少列
    ② **执行记录**：每次执行（`/api/execute` 或任务的 run）的**审计凭据** —— execution_id /
       冻结的 Spec / 数据 SHA256 / 代码版本 / 校验结果 / 产出文件 / 耗时
    ③ **任务**（TASK-002D）：固化下来的"报表任务" —— task_id / 冻结的 Spec / 绑定的数据哈希 / 状态

为什么执行记录要记这些：D11「Report Spec 为核心」明确要求
"执行记录必须绑定：spec 版本 / 数据快照 / 模板版本 / **代码版本** / execution_id / **校验结果**"。
出了数字不对的问题，靠这条记录就能回答"当时用的是哪份 spec、哪份数据、哪版代码"。

为什么是 JSON 文件而不是 SQLite/MySQL：
    当前单用户单进程，JSON 够用，而且**人能直接打开看**（排障时一眼看到某次执行的 spec 与哈希）。
    D8 定的存储是 MySQL；**D8 正式推迟到 TASK-007**（2026-09-23 用户拍板，依据
    `D:\\GPT_Project_Reviews\\reviews\\005-task-layer-review.md` + `decisions/003-task-layer.md`）。
    **本文件就是"可替换的存储边界"**：api.py 只调用下面这些函数，不碰文件格式；
    TASK-007 换 MySQL 时改写本文件的实现即可（函数签名保持不变），上层一行不动。

并发与原子性（这是本文件唯一需要小心的地方）：
    · 写文件一律走 **临时文件 + os.replace**（同目录 rename 原子），
      避免进程被杀时留下半截 JSON 把历史记录全弄坏；
    · 进程内再加一把 RLock，把"读-改-写"串起来（FastAPI 的同步端点跑在线程池里，会真并发）。
    **单进程够用；多进程会写坏文件** —— 所以启动必须 `--workers 1`（见 scripts/serve.py），
    多进程安全由 TASK-007 落库时解决，这里不做假装安全的分布式锁。
    schema_version：三个文件都带顶层 `schema_version`，将来换存储/改结构时能识别旧文件。

环境变量覆盖（测试隔离用，收敛在 `state_dir()` / `upload_dir()` / `output_dir()` 三个函数里，
**每次调用都重新读环境变量**，所以测试可以先改环境再发请求）：
    SRA_STATE_DIR    执行记录/上传记录/任务 JSON 的目录（默认 `<项目根>/state`）
    SRA_UPLOAD_DIR   上传文件的落盘目录（默认 `<项目根>/data/uploads`）
    SRA_OUTPUT_DIR   产出 xlsx 的目录（默认 `<项目根>/outputs`）
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

# app/state.py → 向上 2 层 = 项目根（app/engine/*.py 是 parents[2]，别抄错）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
_DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
_DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"

# 落盘文件名（固定名，便于人肉查看）
UPLOADS_FILE = "uploads.json"
EXECUTIONS_FILE = "executions.json"
TASKS_FILE = "tasks.json"

# 落盘格式版本（R005 required_change #11）：三个 JSON 都写顶层 schema_version。
# 为什么现在就要：换 TASK-007 的 MySQL 时，手里可能还有旧版本的 JSON 记录，
# 有版本号才能判断"这份文件我认不认得"，而不是靠猜字段猜出来的。
SCHEMA_VERSION = 1

# 任务状态机（R005 required_change #9）—— **只增不回退**：
#   created  刚建好，还没成功跑过
#   has_run  至少成功执行过一次
# 注意：run **失败不改** task.status —— 失败的是"这一次执行"，不是任务定义本身；
# 失败信息在 run 记录里（status=failed + error），不去污染任务状态（两种事实分开记）。
TASK_STATUS_CREATED = "created"
TASK_STATUS_HAS_RUN = "has_run"
TASK_STATUSES: tuple[str, ...] = (TASK_STATUS_CREATED, TASK_STATUS_HAS_RUN)

# 进程内串行化：保护"读-改-写"三个动作不被别的线程插进来
_LOCK = threading.RLock()

# 代码版本缓存：**本进程加载的代码**在进程生命周期内不会变，缓存一次即可
_CODE_VERSION: str | None = None


class StateError(RuntimeError):
    """状态文件损坏/不可读写。

    刻意**不静默降级**（不返回空列表假装没记录）：执行记录是审计凭据，
    悄悄丢掉历史等于审计链断了。宁可报错让人来修。
    """


# ════════════════════════════════════════════════════════════════════════
# 路径（三个函数都在**调用时**读环境变量 —— 测试才能靠改环境做隔离）
# ════════════════════════════════════════════════════════════════════════
def state_dir() -> Path:
    """执行记录/上传记录 JSON 所在目录。"""
    return Path(os.environ.get("SRA_STATE_DIR") or _DEFAULT_STATE_DIR)


def upload_dir() -> Path:
    """上传文件的落盘目录（不存在则创建）。"""
    path = Path(os.environ.get("SRA_UPLOAD_DIR") or _DEFAULT_UPLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir() -> Path:
    """产出 xlsx 的目录（不存在则创建）。"""
    path = Path(os.environ.get("SRA_OUTPUT_DIR") or _DEFAULT_OUTPUT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_file() -> Path:
    return state_dir() / UPLOADS_FILE


def executions_file() -> Path:
    return state_dir() / EXECUTIONS_FILE


def tasks_file() -> Path:
    return state_dir() / TASKS_FILE


# ════════════════════════════════════════════════════════════════════════
# 小工具：ID / 时间 / 原子写 / 代码版本
# ════════════════════════════════════════════════════════════════════════
def new_id(prefix: str) -> str:
    """生成一个带前缀的短 ID，如 'f_9c1a2b3d4e5f'（file_id）/ 'x_...'（execution_id）。

    12 位十六进制 = 48 位随机，单用户量级下碰撞概率可忽略；比完整 uuid4 好在**能念出来**。
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    """当前时间（本地时区 + 偏移），如 '2026-09-23T15:04:05+08:00'。"""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def code_version() -> str:
    """运行中代码的版本标识：git 短哈希（有未提交改动则加 `+dirty`），取不到就 'unknown'。

    为什么必须记进执行记录：同一份 Spec 在不同版本的代码上可能算出不同数字，
    审计时要能回答"这次执行跑的是哪版代码"（D11）。
    """
    global _CODE_VERSION
    if _CODE_VERSION is None:
        _CODE_VERSION = _detect_code_version()
    return _CODE_VERSION


def _detect_code_version() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
        )
        if head.returncode != 0:
            return "unknown"
        revision = head.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
        )
        return f"{revision}+dirty" if status.stdout.strip() else revision
    except (OSError, subprocess.SubprocessError):
        # 没装 git / 不是 git 仓库都不该让接口挂掉，降级成 'unknown'（信息缺失但不骗人）
        return "unknown"


def _read_json(path: Path, default: Any) -> Any:
    """读 JSON；文件不存在返回 default（首次运行的正常情况），损坏则抛 StateError。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(
            f"状态文件读取失败：{path}\n  {type(exc).__name__}: {exc}\n"
            f"  处理：修好或移走该文件（不要删了当没发生过 —— 那是执行记录的审计凭据）"
        ) from exc


def _write_json(path: Path, payload: Any) -> None:
    """原子写：先写临时文件再 os.replace，避免半截 JSON 覆盖掉好文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _records(path: Path, key: str) -> list[dict]:
    """取记录列表（兼容 {'<key>': [...]} 与裸列表两种形态，防止手改坏了）。"""
    payload = _read_json(path, {key: []})
    if isinstance(payload, list):                 # 裸列表（人肉编辑过）
        return payload
    records = payload.get(key)
    if not isinstance(records, list):
        raise StateError(f"状态文件结构不对：{path} 里 {key!r} 不是列表")
    return records


def _write_records(path: Path, key: str, records: list[dict]) -> None:
    """写记录列表（统一带上顶层 schema_version）。

    所有写盘都走这一个函数 —— 免得将来加了新文件却漏了版本号（漏了就没法识别旧格式）。
    """
    _write_json(path, {"schema_version": SCHEMA_VERSION, key: records})


# ════════════════════════════════════════════════════════════════════════
# ① 上传记录
# ════════════════════════════════════════════════════════════════════════
def record_upload(
    *,
    file_id: str,
    filename: str,
    stored_path: str,
    size_bytes: int,
    sha256: str,
    rows: int,
    column_names: list[str],
) -> dict:
    """落盘一条上传记录并返回它（含 created_at）。"""
    record = {
        "file_id": file_id,
        "filename": filename,
        "stored_path": stored_path,
        "size_bytes": int(size_bytes),
        "sha256": sha256,
        "rows": int(rows),
        "columns": len(column_names),
        "column_names": list(column_names),
        "created_at": now_iso(),
    }
    with _LOCK:
        records = _records(uploads_file(), "uploads")
        records.insert(0, record)                 # 新的在前
        _write_records(uploads_file(), "uploads", records)
    return record


def get_upload(file_id: str) -> dict | None:
    """按 file_id 取上传记录（不存在的返回 None，由调用方决定回 404）。"""
    with _LOCK:
        for record in _records(uploads_file(), "uploads"):
            if record.get("file_id") == file_id:
                return record
    return None


def list_uploads(limit: int = 50) -> list[dict]:
    with _LOCK:
        return _records(uploads_file(), "uploads")[:limit]


# ════════════════════════════════════════════════════════════════════════
# ② 执行记录（D11 的审计凭据）
# ════════════════════════════════════════════════════════════════════════
def record_execution(record: dict) -> dict:
    """落盘一条执行记录（成功/失败都记 —— 失败也是审计信息，见 D10）。

    调用方（api.py）负责把 execution_id / spec / 哈希 / 校验结果 / 耗时填好；
    本函数只补 created_at 并持久化，**不改调用方给的任何字段**（免得记录下来的是加工过的值）。
    """
    stored = dict(record)
    stored.setdefault("created_at", now_iso())
    with _LOCK:
        records = _records(executions_file(), "executions")
        records.insert(0, stored)
        _write_records(executions_file(), "executions", records)
    return stored


def get_execution(execution_id: str) -> dict | None:
    with _LOCK:
        for record in _records(executions_file(), "executions"):
            if record.get("execution_id") == execution_id:
                return record
    return None


def list_executions(limit: int = 50) -> list[dict]:
    """执行记录列表（新的在前）。"""
    with _LOCK:
        return _records(executions_file(), "executions")[:limit]


def count_executions() -> int:
    with _LOCK:
        return len(_records(executions_file(), "executions"))


def list_task_runs(task_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """**该任务**的执行记录（新的在前）+ 总数。

    为什么不给任务记录里塞一个 run_count 计数：计数是**派生数据**，派生就会漂移
    （删了记录、手工修过文件，计数就对不上）。这里每次直接从执行记录里筛，永远一致。
    单用户量级下这个扫描的代价可以忽略。
    """
    with _LOCK:
        matched = [
            record for record in _records(executions_file(), "executions")
            if record.get("task_id") == task_id
        ]
    return matched[offset:offset + limit], len(matched)


def run_stats_by_task() -> dict[str, dict]:
    """{task_id: {run_count, last_run_at, last_run_status}} —— 一次读盘，供任务列表用。

    只统计**有 task_id 的**执行记录（ad-hoc 的 `/api/execute` 记录不计入任何任务）。
    """
    stats: dict[str, dict] = {}
    with _LOCK:
        records = _records(executions_file(), "executions")
    for record in records:
        task_id = record.get("task_id")
        if not task_id:
            continue
        entry = stats.setdefault(task_id, {"run_count": 0, "last_run_at": None, "last_run_status": None})
        entry["run_count"] += 1
        if entry["last_run_at"] is None:               # 记录新的在前，第一条就是最近一次
            entry["last_run_at"] = record.get("created_at")
            entry["last_run_status"] = record.get("status")
    return stats


# ════════════════════════════════════════════════════════════════════════
# ③ 任务（TASK-002D：D9/D11"首次理解 → 人工确认 → 固化成任务"里的那个"任务"）
# ════════════════════════════════════════════════════════════════════════
def record_task(
    *,
    task_id: str,
    name: str,
    file_id: str,
    source_filename: str,
    data_sha256: str,
    spec: dict,
    data_snapshot_match: bool,
) -> dict:
    """落盘一条任务（含**冻结的 Spec**）并返回它。

    spec 存 `ReportSpec.to_dict()` 的产物（纯 JSON），不存对象 ——
    落盘的东西必须能原样读回来，且**执行时用它、不重新解析**（D9：固化后不再调 LLM）。
    """
    record = {
        "task_id": task_id,
        "name": name,
        "status": TASK_STATUS_CREATED,
        "file_id": file_id,
        "source_filename": source_filename,
        "data_sha256": data_sha256,          # 建任务时绑定的数据哈希（run 前会再核对，D11/D10）
        "data_snapshot_match": bool(data_snapshot_match),
        "spec": dict(spec),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with _LOCK:
        records = _records(tasks_file(), "tasks")
        records.insert(0, record)                    # 新的在前
        _write_records(tasks_file(), "tasks", records)
    return record


def get_task(task_id: str) -> dict | None:
    with _LOCK:
        for record in _records(tasks_file(), "tasks"):
            if record.get("task_id") == task_id:
                return record
    return None


def list_tasks(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """任务列表（新的在前）+ 总数（分页用，R005 required_change #8）。"""
    with _LOCK:
        records = _records(tasks_file(), "tasks")
    return records[offset:offset + limit], len(records)


def mark_task_has_run(task_id: str) -> dict | None:
    """任务成功跑过一次 → 置 `has_run`（幂等；**只增不回退**）。

    失败不调用本函数：失败的是这次执行，不是任务定义（见 TASK_STATUSES 的说明）。
    """
    with _LOCK:
        records = _records(tasks_file(), "tasks")
        for record in records:
            if record.get("task_id") != task_id:
                continue
            if record.get("status") != TASK_STATUS_HAS_RUN:
                record["status"] = TASK_STATUS_HAS_RUN
                record["updated_at"] = now_iso()
                _write_records(tasks_file(), "tasks", records)
            return record
    return None


def count_tasks() -> int:
    with _LOCK:
        return len(_records(tasks_file(), "tasks"))


def state_summary() -> dict:
    """给 /api/health 用的一眼概览（不抛错：状态文件坏了健康检查也要能回答）。"""
    try:
        return {
            "state_dir": str(state_dir()),
            "schema_version": SCHEMA_VERSION,
            "uploads": len(_records(uploads_file(), "uploads")),
            "executions": count_executions(),
            "tasks": count_tasks(),
            "readable": True,
        }
    except StateError as exc:
        return {"state_dir": str(state_dir()), "readable": False, "error": str(exc)}
