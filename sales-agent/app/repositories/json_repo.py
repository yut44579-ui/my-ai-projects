"""json_repo.py · Repository 的 **JSON 文件实现**（当前唯一的实现）。

每个 Json*Repository = 一个 `JsonCollection`（哪个文件、哪个 key）+ 一点点领域查询。
**行为与 TASK-002 之前的 state.py 完全一致**（同一套原子写、同一把锁、同样的排序与分页），
所以这次重构对 `/api/*` 的响应、对落盘文件的内容**没有任何可见变化** —— 只是"谁来调这些文件"变了。

TASK-009 迁 SQLite 时，新增 `sql_repo.py` 实现 base.py 里同样的接口即可，本文件原样保留
（旧 JSON 数据仍可读，迁移脚本按需导一次）。
"""

from __future__ import annotations

import hmac
import time
from pathlib import Path
from typing import Callable

from app.repositories import json_store
from app.repositories.base import (
    AccountRepository,
    ConversationRepository,
    DatasetRepository,
    DocumentRepository,
    ExecutionRepository,
    TaskRepository,
    UploadRepository,
)
from app.repositories.json_store import JsonCollection


class _JsonRepositoryBase:
    """三个 Json 实现共用的构造：**路径用 getter 传进来**（每次调用才解析 → 环境变量改了立刻生效）。"""

    def __init__(self, collection: JsonCollection) -> None:
        self._collection = collection

    @property
    def collection(self) -> JsonCollection:
        return self._collection

    def path(self) -> Path:
        """当前实际读写的文件路径（排查/测试用）。"""
        return self._collection.path()


# ════════════════════════════════════════════════════════════════════════
# ① 上传记录
# ════════════════════════════════════════════════════════════════════════
class JsonUploadRepository(_JsonRepositoryBase, UploadRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.uploads_file, "uploads"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, file_id: str) -> dict | None:
        return self._collection.find(lambda record: record.get("file_id") == file_id)

    def list(self, limit: int = 50) -> list[dict]:
        return self._collection.all()[:limit]

    def count(self) -> int:
        return self._collection.count()


# ════════════════════════════════════════════════════════════════════════
# ② 任务
# ════════════════════════════════════════════════════════════════════════
class JsonTaskRepository(_JsonRepositoryBase, TaskRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.tasks_file, "tasks"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, task_id: str) -> dict | None:
        return self._collection.find(lambda record: record.get("task_id") == task_id)

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        records = self._collection.all()
        return records[offset:offset + limit], len(records)

    def count(self) -> int:
        return self._collection.count()

    def set_status(self, task_id: str, status: str, updated_at: str) -> dict | None:
        def mutate(record: dict) -> bool:
            if record.get("status") == status:
                return False                       # 状态没变 → 不写盘（幂等，也不刷 mtime）
            record["status"] = status
            record["updated_at"] = updated_at
            return True

        return self._collection.update_first(
            lambda record: record.get("task_id") == task_id, mutate
        )


# ════════════════════════════════════════════════════════════════════════
# ③ 执行记录
# ════════════════════════════════════════════════════════════════════════
class JsonExecutionRepository(_JsonRepositoryBase, ExecutionRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.executions_file, "executions"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, execution_id: str) -> dict | None:
        return self._collection.find(lambda record: record.get("execution_id") == execution_id)

    def list(self, limit: int = 50) -> list[dict]:
        return self._collection.all()[:limit]

    def count(self) -> int:
        return self._collection.count()

    def list_by_task(self, task_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """该任务的执行记录 + 总数。

        为什么每次扫全表现算，而不是任务里存 run_count：计数是**派生数据**，派生就会漂移
        （删了记录、手工修过文件，计数就对不上）。单用户量级下这个扫描代价可以忽略。
        """
        matched = self._collection.filter(lambda record: record.get("task_id") == task_id)
        return matched[offset:offset + limit], len(matched)

    def task_run_stats(self) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for record in self._collection.all():
            task_id = record.get("task_id")
            if not task_id:
                continue                            # ad-hoc 执行（/api/execute）不属于任何任务
            entry = stats.setdefault(
                task_id, {"run_count": 0, "last_run_at": None, "last_run_status": None}
            )
            entry["run_count"] += 1
            if entry["last_run_at"] is None:        # 记录新的在前，第一条就是最近一次
                entry["last_run_at"] = record.get("created_at")
                entry["last_run_status"] = record.get("status")
        return stats


# ════════════════════════════════════════════════════════════════════════
# ④ 文档（TASK-003：Word/PDF 上传 + 提取结果 + 摘要）
# ════════════════════════════════════════════════════════════════════════
class JsonDocumentRepository(_JsonRepositoryBase, DocumentRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.documents_file, "documents"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, doc_id: str) -> dict | None:
        return self._collection.find(lambda record: record.get("doc_id") == doc_id)

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        records = self._collection.all()
        return records[offset:offset + limit], len(records)

    def count(self) -> int:
        return self._collection.count()

    def set_summary(self, doc_id: str, summary: dict) -> dict | None:
        """把摘要写回记录。

        `mutate` 只在**摘要真的变了**（内容不同）时返回 True —— 同一个文档反复点"生成摘要"
        不该每次都刷新文件 mtime（与 TaskRepository.set_status 的"没变就不写"同一约定）。
        """
        def mutate(record: dict) -> bool:
            if record.get("summary") == summary:
                return False
            record["summary"] = summary
            return True

        return self._collection.update_first(lambda record: record.get("doc_id") == doc_id, mutate)


# ════════════════════════════════════════════════════════════════════════
# ⑤ 对话（TASK-004：自然语言问答 —— 问题 / Intent / 工具 / 事实 / 回答）
# ════════════════════════════════════════════════════════════════════════
class JsonConversationRepository(_JsonRepositoryBase, ConversationRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.conversations_file, "conversations"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, conversation_id: str) -> dict | None:
        return self._collection.find(
            lambda record: record.get("conversation_id") == conversation_id
        )

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        records = self._collection.all()
        return records[offset:offset + limit], len(records)

    def count(self) -> int:
        return self._collection.count()


# ════════════════════════════════════════════════════════════════════════
# ⑥ 数据集（STEP A：数据源身份 —— id / 来源 / 行数 / 时间范围 / 口径绑定）
# ════════════════════════════════════════════════════════════════════════
class JsonDatasetRepository(_JsonRepositoryBase, DatasetRepository):
    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.datasets_file, "datasets"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, dataset_id: str) -> dict | None:
        return self._collection.find(lambda record: record.get("dataset_id") == dataset_id)

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        records = self._collection.all()
        return records[offset:offset + limit], len(records)

    def count(self) -> int:
        return self._collection.count()


# ════════════════════════════════════════════════════════════════════════
# ⑦ 本地账号（注册/登录：账号名 + 密码的校验值）
# ════════════════════════════════════════════════════════════════════════
class JsonAccountRepository(_JsonRepositoryBase, AccountRepository):
    """账号表：**键是账号名**（不是 uuid），所以 `get` 按名字找，且大小写不敏感。

    这里只做"按名字找一条"这件事 —— 摘要与比对在 app/accounts.py，
    本文件从头到尾**没有出现"密码"这个词的一次运算**（也不该出现）。
    """

    def __init__(self, path_getter: Callable[[], Path] | None = None) -> None:
        super().__init__(JsonCollection(path_getter or json_store.accounts_file, "accounts"))

    def add(self, record: dict) -> dict:
        return self._collection.insert_front(record)

    def get(self, username: str) -> dict | None:
        # casefold() 而不是 lower()：中文/德语的 ß 这类字符也能正确折叠
        wanted = (username or "").strip().casefold()
        if not wanted:
            return None
        return self._collection.find(
            lambda record: str(record.get("username") or "").strip().casefold() == wanted
        )

    def all(self) -> list[dict]:
        return self._collection.all()

    def count(self) -> int:
        return self._collection.count()

    def set_status(self, username: str, status: str, reviewed_by: str,
                   reviewed_at: str) -> dict | None:
        """改状态 + 记审批痕迹。账号名匹配与 `get` 同一口径（大小写不敏感）。"""
        wanted = (username or "").strip().casefold()

        def mutate(record: dict) -> bool:
            if record.get("status") == status:
                return False                        # 没变就不写盘（幂等，也不刷 mtime）
            record["status"] = status
            record["reviewed_by"] = reviewed_by
            record["reviewed_at"] = reviewed_at
            return True

        return self._collection.update_first(
            lambda record: str(record.get("username") or "").strip().casefold() == wanted, mutate)

    def set_last_login(self, username: str, logged_in_at: str) -> dict | None:
        """刷新最后一次登录时间。

        账号名匹配与 `get` 同一口径（大小写不敏感），但**匹配到的那条记录**才改，
        改的是记录里原本的写法 —— 不把用户当初填的账号名改写掉。
        """
        wanted = (username or "").strip().casefold()

        def mutate(record: dict) -> bool:
            if record.get("last_login_at") == logged_in_at:
                return False                        # 没变就不写盘（幂等，也不刷 mtime）
            record["last_login_at"] = logged_in_at
            return True

        return self._collection.update_first(
            lambda record: str(record.get("username") or "").strip().casefold() == wanted, mutate
        )

    def remove(self, username: str) -> dict | None:
        """彻底删掉一个账号（记录从文件里消失），返回被删掉的那条。

        匹配口径与 `get` / `set_status` 完全一致（大小写不敏感、去掉首尾空白）——
        三个方法认的是同一个"这个账号名指的是哪条记录"，不能各认各的。
        "最后一个管理员不许删"那条保护**不在这里**（业务判断归 app/accounts.py）。
        """
        wanted = (username or "").strip().casefold()
        if not wanted:
            return None                                  # 空名字不匹配任何记录（也避免误删第一条）
        return self._collection.remove_first(
            lambda record: str(record.get("username") or "").strip().casefold() == wanted
        )

    # ── 密码恢复（FR-001A）：下面四个都靠 `update_first` 的进程内锁做到"原子" ────
    def _matches(self, username: str):
        """账号名匹配的口径**只写一次**（与 get / set_status / remove 同一套）。"""
        wanted = (username or "").strip().casefold()
        return lambda record: str(record.get("username") or "").strip().casefold() == wanted

    def update_fields(self, username: str, fields: dict) -> dict | None:
        """原子写几个字段（值为 None = 删掉这个键）。

        为什么"删键"而不是"写成 null"：账号记录的字段集是有人逐个数过的
        （`test_密码绝不明落盘` 那条测试就锁死了 12 个字段）——
        一次没用过的临时密码不该在记录里留下一串 null 字段当垃圾。
        """
        if not (username or "").strip():
            return None

        def mutate(record: dict) -> bool:
            changed = False
            for key, value in fields.items():
                if value is None:
                    if key in record:
                        record.pop(key, None)
                        changed = True
                elif record.get(key) != value:
                    record[key] = value
                    changed = True
            return changed                                # 一个都没变 → 不写盘（不刷 mtime）

        return self._collection.update_first(self._matches(username), mutate)

    def consume_recovery_code(self, username: str, code_hash: str, used_at: str) -> bool:
        """原子地"比对 + 消费"恢复码（评审 ④：检查与消费必须是同一个临界区）。

        比对用 `hmac.compare_digest`（定长）：不因为"第几个字符开始不一样"提前返回。
        对不上时**一个字节都不改** —— 输错恢复码绝不能把正确的那个毁掉（评审 Q4-4）。
        """
        if not (username or "").strip() or not code_hash:
            return False
        outcome = {"matched": False}

        def mutate(record: dict) -> bool:
            current = str(record.get("recovery_code_hash") or "")
            if not current or not hmac.compare_digest(current, code_hash):
                return False                              # 对不上：不写盘（正确的码原样留着）
            record.pop("recovery_code_hash", None)        # 用掉即作废：清掉，不是留个标记
            record["recovery_code_used_at"] = used_at
            outcome["matched"] = True
            return True

        self._collection.update_first(self._matches(username), mutate)
        return outcome["matched"]

    def consume_temp_password(self, username: str, used_at: str) -> bool:
        """原子地消费一次临时密码（评审 ⑥：并发多个登录里只允许一个成功）。

        过期的临时密码直接当"没有"处理（顺手清掉，不留过期货）。
        """
        if not (username or "").strip():
            return False
        outcome = {"matched": False}
        now = time.time()

        def mutate(record: dict) -> bool:
            if not record.get("temp_pwd_hash"):
                return False
            expires = float(record.get("temp_pwd_expires_at") or 0)
            if expires <= now:
                return False                              # 过期了：不消费（由上层当"没这个密码"）
            if record.get("temp_pwd_used_at"):
                return False                              # 已经被用掉：并发里的第二个必须失败
            record["temp_pwd_used_at"] = used_at
            outcome["matched"] = True
            return True

        self._collection.update_first(self._matches(username), mutate)
        return outcome["matched"]
