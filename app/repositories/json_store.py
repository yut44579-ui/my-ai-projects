"""json_store.py · JSON 文件存储底座（TASK-002 的 Repository 抽象的**最底层**）。

本文件只干一件事：**把"一个 JSON 文件里的一串记录"安全地读出来 / 写回去**。
它不认识"任务""执行""上传"这些业务概念（那是 json_repo.py 的事），也不做业务校验。

从 `app/state.py` 搬过来的（TASK-002 · Repository 抽象，**行为/文件格式/路径一字不变**）：
    · 三个目录的路径解析（每次调用都读环境变量，测试才能靠改环境隔离）
    · 原子写（临时文件 + os.replace）+ 进程内 RLock
    · 顶层 schema_version 的读写
为什么搬：Repository 实现层（json_repo.py）要用这些，若它们留在 state.py，
    state.py 反过来又要 import repositories → 循环导入。搬到最底层，两边都只依赖它。

⚠️ 并发边界（与搬家前完全一致，没有被这次重构放松）：
    单进程安全（RLock 串行化"读-改-写"）；**多进程会写坏文件** —— 启动必须 `--workers 1`
    （见 scripts/serve.py）。多进程安全等 TASK-009 落 SQLite 时解决，这里不做假装安全的分布式锁。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

# app/repositories/json_store.py → 向上 3 层 = 项目根（app/state.py 是 parents[1]，别抄错）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_STATE_DIR = PROJECT_ROOT / "state"
_DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
_DEFAULT_DOC_DIR = PROJECT_ROOT / "data" / "documents"
_DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"

# 落盘文件名（固定名，便于人肉查看）
UPLOADS_FILE = "uploads.json"
EXECUTIONS_FILE = "executions.json"
TASKS_FILE = "tasks.json"
DOCUMENTS_FILE = "documents.json"
CONVERSATIONS_FILE = "conversations.json"
# 数据集登记表（STEP A）：一个"数据源"就是一个 Dataset —— 带 id、来源文件、行数/时间范围、
# 口径绑定与导入时间。文件哈希只在这一层与后端里流转，前端只显示业务字段。
DATASETS_FILE = "datasets.json"
# 本地账号（注册/登录 · 单机自用级别）：**只存校验值，不存明文密码**。
# 条目的形状由 app/accounts.py 定义（pwd_algo / pwd_salt / pwd_hash / pwd_iterations），
# 这一层只负责把它安全地读出来写回去。
ACCOUNTS_FILE = "accounts.json"

# 落盘格式版本（R005 required_change #11）：三个 JSON 都写顶层 schema_version
SCHEMA_VERSION = 1

# 进程内串行化：保护"读-改-写"三个动作不被别的线程插进来
LOCK = threading.RLock()


class StateError(RuntimeError):
    """状态文件损坏/不可读写。

    刻意**不静默降级**（不返回空列表假装没记录）：执行记录是审计凭据，
    悄悄丢掉历史等于审计链断了。宁可报错让人来修。
    """


# ════════════════════════════════════════════════════════════════════════
# 路径（都在**调用时**读环境变量 —— 测试才能靠改环境做隔离）
# ════════════════════════════════════════════════════════════════════════
def state_dir() -> Path:
    """执行记录/上传记录/任务 JSON 所在目录。"""
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


def doc_dir() -> Path:
    """Word/PDF 原文的落盘目录（TASK-003；不存在则创建）。

    与 upload_dir() 分开：表格上传（data/uploads）与文档上传（data/documents）是两条独立的
    输入管道，混在一个目录里将来做清理/配额时会分不清谁是谁。
    """
    path = Path(os.environ.get("SRA_DOC_DIR") or _DEFAULT_DOC_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_file() -> Path:
    return state_dir() / UPLOADS_FILE


def executions_file() -> Path:
    return state_dir() / EXECUTIONS_FILE


def tasks_file() -> Path:
    return state_dir() / TASKS_FILE


def documents_file() -> Path:
    return state_dir() / DOCUMENTS_FILE


def conversations_file() -> Path:
    """自然语言对话记录（TASK-004）。落 state/ 与其它记录同级：
    对话里带着"当时算出来的数字"，和 tasks/executions 一样是**审计凭据**，不该另起一处。"""
    return state_dir() / CONVERSATIONS_FILE


def datasets_file() -> Path:
    """数据集登记表（STEP A）。与其它记录同级：它也是**审计凭据**
    （"这个数字基于哪个数据源、哪套口径"要靠它回答）。"""
    return state_dir() / DATASETS_FILE


def accounts_file() -> Path:
    """本地账号表（注册/登录）。与其它记录同级落 state/：
    它也是**审计凭据**（"这个账号是谁、什么时候建的、上次什么时候登录的"要靠它回答）。"""
    return state_dir() / ACCOUNTS_FILE


# ════════════════════════════════════════════════════════════════════════
# 读写原语（原子写 + 版本号）
# ════════════════════════════════════════════════════════════════════════
def read_json(path: Path, default: Any) -> Any:
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


def write_json(path: Path, payload: Any) -> None:
    """原子写：先写临时文件再 os.replace，避免半截 JSON 覆盖掉好文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_records(path: Path, key: str) -> list[dict]:
    """取记录列表（兼容 {'<key>': [...]} 与裸列表两种形态，防止手改坏了）。"""
    payload = read_json(path, {key: []})
    if isinstance(payload, list):                 # 裸列表（人肉编辑过）
        return payload
    records = payload.get(key)
    if not isinstance(records, list):
        raise StateError(f"状态文件结构不对：{path} 里 {key!r} 不是列表")
    return records


def write_records(path: Path, key: str, records: list[dict]) -> None:
    """写记录列表（统一带上顶层 schema_version）。

    所有写盘都走这一个函数 —— 免得将来加了新文件却漏了版本号（漏了就没法识别旧格式）。
    """
    write_json(path, {"schema_version": SCHEMA_VERSION, key: records})


# ════════════════════════════════════════════════════════════════════════
# JsonCollection：一个 JSON 文件 = 一个记录集合（Repository 实现用它拼装）
# ════════════════════════════════════════════════════════════════════════
class JsonCollection:
    """`<state_dir>/<file>` 里 key 对应的记录列表（新的在前）。

    `path_getter` 是个**函数**（不是 Path）：每次操作时才解析路径 ——
    这样测试改 `SRA_STATE_DIR` 后立刻生效（等价于 state.py 搬家前的行为）。
    """

    def __init__(self, path_getter: Callable[[], Path], key: str) -> None:
        self._path_getter = path_getter
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    def path(self) -> Path:
        return self._path_getter()

    # ── 读 ──────────────────────────────────────────────────────────────
    def all(self) -> list[dict]:
        with LOCK:
            return read_records(self.path(), self._key)

    def count(self) -> int:
        with LOCK:
            return len(read_records(self.path(), self._key))

    def find(self, predicate: Callable[[dict], bool]) -> dict | None:
        with LOCK:
            for record in read_records(self.path(), self._key):
                if predicate(record):
                    return record
        return None

    def filter(self, predicate: Callable[[dict], bool]) -> list[dict]:
        with LOCK:
            return [record for record in read_records(self.path(), self._key) if predicate(record)]

    # ── 写 ──────────────────────────────────────────────────────────────
    def insert_front(self, record: dict) -> dict:
        """插到最前面（记录新的在前 —— 三个文件的约定）。"""
        with LOCK:
            records = read_records(self.path(), self._key)
            records.insert(0, record)
            write_records(self.path(), self._key, records)
        return record

    def update_first(self, predicate: Callable[[dict], bool], mutate: Callable[[dict], bool]) -> dict | None:
        """找到第一条匹配的记录，`mutate` 返回 True（真的改了东西）才写盘。

        "没改就不写"是刻意的：避免每次调用都刷新文件 mtime（`mark_task_has_run` 幂等）。
        """
        with LOCK:
            records = read_records(self.path(), self._key)
            for record in records:
                if not predicate(record):
                    continue
                if mutate(record):
                    write_records(self.path(), self._key, records)
                return record
        return None

    def extend(self, records: Iterable[dict]) -> None:
        """批量插到最前面（保持传入顺序）。"""
        with LOCK:
            existing = read_records(self.path(), self._key)
            write_records(self.path(), self._key, list(records) + existing)
