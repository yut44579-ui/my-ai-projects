"""base.py · Repository 抽象接口（TASK-002，Gate 决定 004 第 2 条）。

为什么要有这一层（而不是直接调 state.py）：
    现在存储是 **JSON 文件**；TASK-009（行动计划）要迁 **SQLite**（用户/CRUD/状态/时间）。
    如果 API 层直接跟"JSON 文件"绑死，迁移那天就得改遍所有调用点。
    把"怎么存"收进接口后面：业务层只认下面这三个接口，
    将来 `SqlTaskRepository` 实现同一套方法即可替换，上层（api.py / state.py）一行不动。

边界（刻意的，防止这层长胖）：
    · 接口**只做持久化 + 直接查询**（按 id 取、分页列表、计数、按任务筛执行记录、状态置位）。
    · **不做业务校验**（指标目录、日期顺序、数据哈希闸门 —— 那是 api.py / engine 的事）。
    · **不生成 ID、不生成时间戳**（那是 state.py 的事，且它们与业务语义绑在一起）。
    · 记录里的字段原样进原样出 —— 接口不认识"金额""rows_in_range"这些字段的含义。

本 TASK 只做抽象 + JSON 实现，**不迁数据库**（TASK-009 才迁 SQLite）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class UploadRepository(ABC):
    """上传记录（`state/uploads.json`）：文件落盘后登记的那一条。"""

    @abstractmethod
    def add(self, record: dict) -> dict:
        """登记一条上传记录（插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, file_id: str) -> dict | None:
        """按 file_id 取；不存在返回 None（由调用方决定回 404）。"""

    @abstractmethod
    def list(self, limit: int = 50) -> list[dict]:
        """上传记录列表（新的在前）。"""

    @abstractmethod
    def count(self) -> int:
        """上传记录总数。"""


class TaskRepository(ABC):
    """任务（`state/tasks.json`）：固化下来的报表任务（含冻结 Spec）。"""

    @abstractmethod
    def add(self, record: dict) -> dict:
        """落盘一条任务（含冻结 Spec，插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, task_id: str) -> dict | None:
        """按 task_id 取；不存在返回 None。"""

    @abstractmethod
    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """分页列表（新的在前）+ 总数。"""

    @abstractmethod
    def count(self) -> int:
        """任务总数。"""

    @abstractmethod
    def set_status(self, task_id: str, status: str, updated_at: str) -> dict | None:
        """把任务状态置为 status 并刷新 updated_at（**状态没变就不写盘**，幂等）。

        只改 status / updated_at 两个字段，其余原样 —— 状态机的合法取值由 state.py 定义。
        """


class ExecutionRepository(ABC):
    """执行记录（`state/executions.json`）：D11 的审计凭据（成功与失败都记）。"""

    @abstractmethod
    def add(self, record: dict) -> dict:
        """落盘一条执行记录（插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, execution_id: str) -> dict | None:
        """按 execution_id 取；不存在返回 None。"""

    @abstractmethod
    def list(self, limit: int = 50) -> list[dict]:
        """执行记录列表（新的在前）。"""

    @abstractmethod
    def count(self) -> int:
        """执行记录总数。"""

    @abstractmethod
    def list_by_task(self, task_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """**某个任务**的执行记录（新的在前）+ 总数。"""

    @abstractmethod
    def task_run_stats(self) -> dict[str, dict]:
        """`{task_id: {run_count, last_run_at, last_run_status}}`（一次读盘，供任务列表用）。

        没有 task_id 的 ad-hoc 执行记录（`POST /api/execute`）不计入任何任务。
        """


class DocumentRepository(ABC):
    """文档记录（`state/documents.json`）：TASK-003 上传的 Word/PDF（含提取结果与摘要）。

    与 UploadRepository 的关系：**两套独立的输入管道**。
        表格（xlsx/csv）→ UploadRepository，最终喂给 metrics/executor 算数；
        文档（docx/pdf）→ DocumentRepository，只做"提取文本 + 结构化摘要"，不参与算数。
    分成两个接口而不是给 UploadRepository 加字段：文档没有"多少行多少列"，表格没有"多少页"，
    塞进同一条记录里必然一半字段恒为空 —— 那种"看着统一其实处处 if"的设计不要。
    """

    @abstractmethod
    def add(self, record: dict) -> dict:
        """登记一条文档记录（含提取结果，插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, doc_id: str) -> dict | None:
        """按 doc_id 取；不存在返回 None（由调用方决定回 404）。"""

    @abstractmethod
    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """分页列表（新的在前）+ 总数。"""

    @abstractmethod
    def count(self) -> int:
        """文档记录总数。"""

    @abstractmethod
    def set_summary(self, doc_id: str, summary: dict) -> dict | None:
        """把摘要结果写回该文档记录（没有变化返回原记录，不写盘 —— 与 set_status 同一约定）。"""


# 便于测试/未来扩展统一遍历（新增仓库时记得加进来）
REPOSITORY_INTERFACES: tuple[type, ...] = (
    UploadRepository,
    TaskRepository,
    ExecutionRepository,
    DocumentRepository,
)

__all__ = [
    "UploadRepository",
    "TaskRepository",
    "ExecutionRepository",
    "DocumentRepository",
    "REPOSITORY_INTERFACES",
    "Callable",
]
