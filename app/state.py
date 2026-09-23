"""state.py · 运行状态（上传记录 + 执行记录 + 任务）—— 无数据库，纯 JSON 文件。

职责只有四件（别让它长胖）：
    ① **上传记录**：`data/uploads/` 里存了哪些文件、哈希多少、多少行多少列
    ② **执行记录**：每次执行（`/api/execute` 或任务的 run）的**审计凭据** —— execution_id /
       冻结的 Spec / 数据 SHA256 / 代码版本 / 校验结果 / 产出文件 / 耗时
    ③ **任务**（TASK-002D）：固化下来的"报表任务" —— task_id / 冻结的 Spec / 绑定的数据哈希 / 状态
    ④ **文档记录**（TASK-003）：上传的 Word/PDF 提取到了什么 —— doc_id / 字符数 / 块数 /
       大纲 / warnings / 摘要。与①②③是**独立管道**：文档不参与算数，只做提取与摘要

为什么执行记录要记这些：D11「Report Spec 为核心」明确要求
"执行记录必须绑定：spec 版本 / 数据快照 / 模板版本 / **代码版本** / execution_id / **校验结果**"。
出了数字不对的问题，靠这条记录就能回答"当时用的是哪份 spec、哪份数据、哪版代码"。

为什么是 JSON 文件而不是 SQLite/MySQL：
    当前单用户单进程，JSON 够用，而且**人能直接打开看**（排障时一眼看到某次执行的 spec 与哈希）。
    D8 定的存储是 MySQL；**D8 正式推迟到 TASK-007**（2026-09-23 用户拍板，依据
    `D:\\GPT_Project_Reviews\\reviews\\005-task-layer-review.md` + `decisions/003-task-layer.md`）。
    TASK-009 迁 SQLite（依据 `decisions/004-upgrade-gate.md` 第 2 条）。

★ TASK-002 起的分工（Repository 抽象，Gate 决定 004 第 2 条）：
    "**怎么存**"搬去了 `app/repositories/`（json_store → json_repo → base 接口）；
    本文件只留"**存什么**" —— 记录长什么样、状态机怎么走、ID/时间戳怎么生成。
    本文件对外**函数签名、行为、文件格式、路径全部不变**，api.py 一行没改。
    好处：将来换 SQLite，只写一个 `SqlTaskRepository` 实现同一套接口，
    本文件与上层（api.py/engine）都不用动。

并发与原子性（搬去 json_store.py 了，这里不再自己管锁）：
    · 写文件一律走 **临时文件 + os.replace**（同目录 rename 原子），避免半截 JSON 把历史弄坏；
    · 进程内一把 RLock 把"读-改-写"串起来（FastAPI 的同步端点跑在线程池里，会真并发）。
    **单进程够用；多进程会写坏文件** —— 所以启动必须 `--workers 1`（见 scripts/serve.py）。

环境变量覆盖（测试隔离用，都在**调用时**读，所以测试可以先改环境再发请求）：
    SRA_STATE_DIR    执行记录/上传记录/任务 JSON 的目录（默认 `<项目根>/state`）
    SRA_UPLOAD_DIR   上传文件的落盘目录（默认 `<项目根>/data/uploads`）
    SRA_OUTPUT_DIR   产出 xlsx 的目录（默认 `<项目根>/outputs`）
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import uuid
from pathlib import Path

from app import repositories
from app.repositories import json_store
from app.repositories.json_store import (        # 对外保留原名（含私有名），调用方与测试不用改
    CONVERSATIONS_FILE,
    DOCUMENTS_FILE,
    EXECUTIONS_FILE,
    SCHEMA_VERSION,
    TASKS_FILE,
    UPLOADS_FILE,
    LOCK as _LOCK,
    StateError,
    conversations_file,
    doc_dir,
    documents_file,
    executions_file,
    output_dir,
    state_dir,
    tasks_file,
    upload_dir,
    uploads_file,
)

# app/state.py → 向上 2 层 = 项目根（app/engine/*.py 是 parents[2]，别抄错）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 任务状态机（R005 required_change #9）—— **只增不回退**：
#   created  刚建好，还没成功跑过
#   has_run  至少成功执行过一次
# 注意：run **失败不改** task.status —— 失败的是"这一次执行"，不是任务定义本身；
# 失败信息在 run 记录里（status=failed + error），不去污染任务状态（两种事实分开记）。
TASK_STATUS_CREATED = "created"
TASK_STATUS_HAS_RUN = "has_run"
TASK_STATUSES: tuple[str, ...] = (TASK_STATUS_CREATED, TASK_STATUS_HAS_RUN)

# 代码版本缓存：**本进程加载的代码**在进程生命周期内不会变，缓存一次即可
_CODE_VERSION: str | None = None


# ════════════════════════════════════════════════════════════════════════
# 小工具：ID / 时间 / 代码版本
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
    return repositories.uploads().add(record)


def get_upload(file_id: str) -> dict | None:
    """按 file_id 取上传记录（不存在的返回 None，由调用方决定回 404）。"""
    return repositories.uploads().get(file_id)


def list_uploads(limit: int = 50) -> list[dict]:
    return repositories.uploads().list(limit)


def count_uploads() -> int:
    return repositories.uploads().count()


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
    return repositories.executions().add(stored)


def get_execution(execution_id: str) -> dict | None:
    return repositories.executions().get(execution_id)


def list_executions(limit: int = 50) -> list[dict]:
    """执行记录列表（新的在前）。"""
    return repositories.executions().list(limit)


def count_executions() -> int:
    return repositories.executions().count()


def list_task_runs(task_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """**该任务**的执行记录（新的在前）+ 总数。

    为什么不给任务记录里塞一个 run_count 计数：计数是**派生数据**，派生就会漂移
    （删了记录、手工修过文件，计数就对不上）。这里每次直接从执行记录里筛，永远一致。
    """
    return repositories.executions().list_by_task(task_id, limit, offset)


def run_stats_by_task() -> dict[str, dict]:
    """{task_id: {run_count, last_run_at, last_run_status}} —— 一次读盘，供任务列表用。

    只统计**有 task_id 的**执行记录（ad-hoc 的 `/api/execute` 记录不计入任何任务）。
    """
    return repositories.executions().task_run_stats()


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
    now = now_iso()
    record = {
        "task_id": task_id,
        "name": name,
        "status": TASK_STATUS_CREATED,
        "file_id": file_id,
        "source_filename": source_filename,
        "data_sha256": data_sha256,          # 建任务时绑定的数据哈希（run 前会再核对，D11/D10）
        "data_snapshot_match": bool(data_snapshot_match),
        "spec": dict(spec),
        "created_at": now,
        "updated_at": now,
    }
    return repositories.tasks().add(record)


def get_task(task_id: str) -> dict | None:
    return repositories.tasks().get(task_id)


def list_tasks(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """任务列表（新的在前）+ 总数（分页用，R005 required_change #8）。"""
    return repositories.tasks().list(limit, offset)


def mark_task_has_run(task_id: str) -> dict | None:
    """任务成功跑过一次 → 置 `has_run`（幂等；**只增不回退**）。

    失败不调用本函数：失败的是这次执行，不是任务定义（见 TASK_STATUSES 的说明）。
    """
    return repositories.tasks().set_status(task_id, TASK_STATUS_HAS_RUN, now_iso())


def count_tasks() -> int:
    return repositories.tasks().count()


# ════════════════════════════════════════════════════════════════════════
# ④ 文档记录（TASK-003：Word/PDF 上传 + 文本提取 + 结构化摘要）
# ════════════════════════════════════════════════════════════════════════
def record_document(
    *,
    doc_id: str,
    filename: str,
    stored_path: str,
    suffix: str,
    size_bytes: int,
    sha256: str,
    chars: int,
    blocks: int,
    block_unit: str,
    title: str,
    outline: list[str],
    warnings: list[str],
    extra: dict | None = None,
) -> dict:
    """落盘一条文档记录（提取结果随之冻结）并返回它。

    为什么把提取结果直接存进记录，而不是每次现读原文：提取是**确定性的**（同一份文件永远是
    同一段文本），重算只会白花时间；而且记录里留着当时提取到的 `chars`，将来发现"某份 PDF
    第 3 页没提到字"，翻记录就能看到当时就报过这个 warning —— 审计要的就是这个。
    `text` 本身**不存进 JSON**（一份文档的全文可能几百 KB，塞进共享的状态文件会让它越来越难读），
    落盘的是原文文件本身，取全文时按 `stored_path` 重新提取。
    """
    record = {
        "doc_id": doc_id,
        "filename": filename,
        "stored_path": stored_path,
        "suffix": suffix,
        "size_bytes": int(size_bytes),
        "sha256": sha256,
        "chars": int(chars),
        "blocks": int(blocks),
        "block_unit": block_unit,
        "title": title,
        "outline": list(outline),
        "warnings": list(warnings),
        "extra": dict(extra or {}),
        "summary": None,                     # 摘要按需生成（点了才做），生成后写回这里
        "created_at": now_iso(),
    }
    return repositories.documents().add(record)


def get_document(doc_id: str) -> dict | None:
    """按 doc_id 取文档记录（不存在返回 None，由调用方回 404）。"""
    return repositories.documents().get(doc_id)


def list_documents(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """文档列表（新的在前）+ 总数。"""
    return repositories.documents().list(limit, offset)


def count_documents() -> int:
    return repositories.documents().count()


def set_document_summary(doc_id: str, summary: dict) -> dict | None:
    """把生成的摘要写回文档记录（内容没变不写盘）。"""
    return repositories.documents().set_summary(doc_id, summary)


# ════════════════════════════════════════════════════════════════════════
# ⑤ 对话记录（TASK-004：自然语言问答的全链路留痕）
# ════════════════════════════════════════════════════════════════════════
def record_conversation(conversation: dict) -> dict:
    """落盘一条对话记录并返回它。

    记录的形状由 `app/ai/service.py` 的 `_record()` **一处**决定（问题 / Intent / 工具 /
    事实 / 回答 / 闸门），这里只负责"存"。为什么不在 state.py 里再拼一遍字段：
    那样同一个形状就有了两个定义，改一个忘一个 → 落盘的东西和页面显示的东西对不上。
    """
    return repositories.conversations().add(conversation)


def get_conversation(conversation_id: str) -> dict | None:
    """按 conversation_id 取（不存在返回 None，由调用方回 404）。"""
    return repositories.conversations().get(conversation_id)


def list_conversations(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """对话列表（新的在前）+ 总数。"""
    return repositories.conversations().list(limit, offset)


def count_conversations() -> int:
    return repositories.conversations().count()


# ════════════════════════════════════════════════════════════════════════
# 健康检查概览
# ════════════════════════════════════════════════════════════════════════
def state_summary() -> dict:
    """给 /api/health 用的一眼概览（不抛错：状态文件坏了健康检查也要能回答）。"""
    try:
        return {
            "state_dir": str(state_dir()),
            "schema_version": SCHEMA_VERSION,
            "uploads": count_uploads(),
            "executions": count_executions(),
            "tasks": count_tasks(),
            "readable": True,
        }
    except StateError as exc:
        return {"state_dir": str(state_dir()), "readable": False, "error": str(exc)}


__all__ = [
    "PROJECT_ROOT",
    "SCHEMA_VERSION",
    "UPLOADS_FILE",
    "EXECUTIONS_FILE",
    "TASKS_FILE",
    "DOCUMENTS_FILE",
    "CONVERSATIONS_FILE",
    "TASK_STATUS_CREATED",
    "TASK_STATUS_HAS_RUN",
    "TASK_STATUSES",
    "StateError",
    "state_dir",
    "upload_dir",
    "doc_dir",
    "output_dir",
    "uploads_file",
    "executions_file",
    "tasks_file",
    "documents_file",
    "conversations_file",
    "new_id",
    "now_iso",
    "code_version",
    "record_upload",
    "get_upload",
    "list_uploads",
    "count_uploads",
    "record_execution",
    "get_execution",
    "list_executions",
    "count_executions",
    "list_task_runs",
    "run_stats_by_task",
    "record_task",
    "get_task",
    "list_tasks",
    "mark_task_has_run",
    "count_tasks",
    "record_document",
    "get_document",
    "list_documents",
    "count_documents",
    "set_document_summary",
    "record_conversation",
    "get_conversation",
    "list_conversations",
    "count_conversations",
    "state_summary",
]
