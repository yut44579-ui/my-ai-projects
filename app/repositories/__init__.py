"""repositories · 存储访问层（TASK-002 · Repository 抽象）。

分层（自上而下，只能往下依赖）：

    api.py / engine / scripts          业务逻辑（不知道数据存在哪）
        ↓
    app/state.py                       领域状态：记录长什么样、状态机、ID/时间戳
        ↓
    app/repositories/base.py           抽象接口（UploadRepository / TaskRepository / ExecutionRepository）
        ↓
    app/repositories/json_repo.py      JSON 实现（当前唯一实现）
        ↓
    app/repositories/json_store.py     读/写一个 JSON 文件（原子写 + 锁 + schema_version）

用法（state.py 就是这么用的）：

    from app import repositories

    repositories.uploads().add(record)
    repositories.tasks().get(task_id)
    repositories.executions().task_run_stats()

为什么用**模块级惰性单例**而不是每次 new 一个：仓库实例本身无状态（真正的状态在文件里，
路径也是每次调用才解析环境变量），共用一个实例省得在热点路径上反复构造；
但测试要隔离时可以直接 `JsonTaskRepository(path_getter=...)` 造一个专属实例 —— 接口没锁死。

TASK-009 迁 SQLite 时：新增 `sql_repo.py`，把下面三个工厂的返回值换掉即可，state.py 一行不动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

# 先 import json_store（最底层），再 import json_repo（依赖它）—— 顺序别颠倒，避免包初始化半截
from app.repositories import json_store
from app.repositories.base import (
    REPOSITORY_INTERFACES,
    DocumentRepository,
    ExecutionRepository,
    TaskRepository,
    UploadRepository,
)
from app.repositories.json_repo import (
    JsonDocumentRepository,
    JsonExecutionRepository,
    JsonTaskRepository,
    JsonUploadRepository,
)
from app.repositories.json_store import LOCK, SCHEMA_VERSION, StateError

# ════════════════════════════════════════════════════════════════════════
# 工厂（想换实现/换文件，只改这三个函数）
# ════════════════════════════════════════════════════════════════════════
def build_upload_repository(path_getter: Callable[[], Path] | None = None) -> UploadRepository:
    """造一个上传记录仓库（path_getter 不传 = 用环境变量解析出的默认路径）。"""
    return JsonUploadRepository(path_getter)


def build_task_repository(path_getter: Callable[[], Path] | None = None) -> TaskRepository:
    return JsonTaskRepository(path_getter)


def build_execution_repository(path_getter: Callable[[], Path] | None = None) -> ExecutionRepository:
    return JsonExecutionRepository(path_getter)


def build_document_repository(path_getter: Callable[[], Path] | None = None) -> DocumentRepository:
    """造一个文档仓库（TASK-003：Word/PDF 上传记录 + 提取结果 + 摘要）。"""
    return JsonDocumentRepository(path_getter)


# ════════════════════════════════════════════════════════════════════════
# 默认实例（惰性构造一次，全局共用）
# ════════════════════════════════════════════════════════════════════════
_uploads: UploadRepository | None = None
_tasks: TaskRepository | None = None
_executions: ExecutionRepository | None = None
_documents: DocumentRepository | None = None


def uploads() -> UploadRepository:
    """上传记录仓库（`state/uploads.json`）。"""
    global _uploads
    if _uploads is None:
        _uploads = build_upload_repository()
    return _uploads


def tasks() -> TaskRepository:
    """任务仓库（`state/tasks.json`）。"""
    global _tasks
    if _tasks is None:
        _tasks = build_task_repository()
    return _tasks


def executions() -> ExecutionRepository:
    """执行记录仓库（`state/executions.json`）。"""
    global _executions
    if _executions is None:
        _executions = build_execution_repository()
    return _executions


def documents() -> DocumentRepository:
    """文档仓库（`state/documents.json`，TASK-003）。"""
    global _documents
    if _documents is None:
        _documents = build_document_repository()
    return _documents


__all__ = [
    "UploadRepository",
    "TaskRepository",
    "ExecutionRepository",
    "DocumentRepository",
    "REPOSITORY_INTERFACES",
    "JsonUploadRepository",
    "JsonTaskRepository",
    "JsonExecutionRepository",
    "JsonDocumentRepository",
    "build_upload_repository",
    "build_task_repository",
    "build_execution_repository",
    "build_document_repository",
    "uploads",
    "tasks",
    "executions",
    "documents",
    "json_store",
    "LOCK",
    "SCHEMA_VERSION",
    "StateError",
]
