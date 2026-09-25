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


class ConversationRepository(ABC):
    """自然语言问答记录（`state/conversations.json`）：TASK-004 的"真问真答"凭据。

    为什么它值得单独一个仓库（而不是塞进 ExecutionRepository）：
      执行记录（ExecutionRepository）回答的是"**按冻结 Spec 跑了一次报表**"，
      而对话回答的是"**用户用大白话问了一句，Agent 解析成什么 Intent、调了哪个工具、
      算出了什么事实、LLM 组成了什么话**"。后者的核心是**可追溯**：
      事后要能核对"当时那个数字是哪个工具、哪个区间算出来的"。
      两条记录的字段几乎没有交集，塞一起必然一半恒为空（理由同 DocumentRepository）。
    """

    @abstractmethod
    def add(self, record: dict) -> dict:
        """登记一条对话（插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, conversation_id: str) -> dict | None:
        """按 conversation_id 取；不存在返回 None（由调用方决定回 404）。"""

    @abstractmethod
    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """分页列表（新的在前）+ 总数。"""

    @abstractmethod
    def count(self) -> int:
        """对话记录总数。"""


class AccountRepository(ABC):
    """本地账号（`state/accounts.json`）：注册进来的账号 + 密码的**校验值**。

    为什么单独一个接口（而不是塞进别的仓库）：
        账号的键是**账号名本身**（不是 uuid 主键），查的是"这个账号在不在、密码对不对"；
        产出它的那一层是 `app/accounts.py`（校验规则 + 摘要算法都在那里），
        本层只认下面这几个动作，**不认识"密码"两个字的含义** ——
        `pwd_salt` / `pwd_hash` 在它眼里就是两个普通字符串。

    ⚠️ 摘要与比对**不在这里**：本层不做任何校验（与上面几个接口同一条边界），
       所以它拿不到明文密码，也不该拿到。
    """

    @abstractmethod
    def add(self, record: dict) -> dict:
        """登记一个账号（插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, username: str) -> dict | None:
        """按账号名取；不存在返回 None。

        **账号名大小写不敏感**（"Tangyu" 与 "tangyu" 视为同一个账号）：
        否则 `Tangyu` 与 `tangyu` 会变成两个账号，用户自己都分不清登的是哪个。
        """

    @abstractmethod
    def count(self) -> int:
        """账号总数（首次使用引导用：一个账号都没有时要提示"先注册一个"）。"""

    @abstractmethod
    def all(self) -> list[dict]:
        """全部账号（新的在前）。

        ⚠️ 出参是**落盘的原始记录**（里面有盐与校验值）—— 它只给 `app/accounts.py` 用，
        那里有 `public()` 白名单负责裁剪。任何 HTTP 响应都不许直接用它。
        """

    @abstractmethod
    def set_last_login(self, username: str, logged_in_at: str) -> dict | None:
        """刷新该账号的 `last_login_at`（**值没变就不写盘**，与 set_status 同一约定）。"""

    @abstractmethod
    def set_status(self, username: str, status: str, reviewed_by: str,
                   reviewed_at: str) -> dict | None:
        """改账号状态并记下审批人与时间（**状态没变就不写盘**，幂等）。"""


class DatasetRepository(ABC):
    """数据集登记表（`state/datasets.json`）：STEP A 引入的**数据源身份**。

    为什么它必须存在（评审的硬条件）：一个数字从哪来，必须答得出来 ——
    "基于哪个数据源、哪套口径、哪次导入"。所以每个 Dataset 记的是
    `dataset_id / source_file / file_hash / row_count / columns / date_range /
     imported_at / metric_definition / analysis_enabled`。

    与 UploadRepository 的区别：上传记录回答"文件收进来了吗"（一次收件），
    数据集回答"这是一个可被引用的数据源吗"（带口径、带时间范围、可被分析结果引用的身份）。
    文件字节仍然复用上传的落盘件（`stored_path`/`file_hash` 指过去），不重复存一份。

    与 DocumentRepository 的区别：文档是**非结构化输入**（Word/PDF → 文本），
    数据集是**结构化输入**（Excel/CSV → 可查询的表）。两条管道语义不同，不合并。
    """

    @abstractmethod
    def add(self, record: dict) -> dict:
        """登记一个数据集（插到最前）；返回落盘的那条。"""

    @abstractmethod
    def get(self, dataset_id: str) -> dict | None:
        """按 dataset_id 取；不存在返回 None（由调用方决定回 404）。"""

    @abstractmethod
    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """分页列表（新的在前）+ 总数。"""

    @abstractmethod
    def count(self) -> int:
        """数据集总数。"""


# 便于测试/未来扩展统一遍历（新增仓库时记得加进来）
REPOSITORY_INTERFACES: tuple[type, ...] = (
    UploadRepository,
    TaskRepository,
    ExecutionRepository,
    DocumentRepository,
    ConversationRepository,
    DatasetRepository,
    AccountRepository,
)

__all__ = [
    "UploadRepository",
    "TaskRepository",
    "ExecutionRepository",
    "DocumentRepository",
    "ConversationRepository",
    "DatasetRepository",
    "AccountRepository",
    "REPOSITORY_INTERFACES",
    "Callable",
]
