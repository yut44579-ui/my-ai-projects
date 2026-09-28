"""db.py · FR-003B：SQLite 物化的**连接、表结构与事务**（标准库 sqlite3，无服务端）。

════════════════════════════════════════════════════════════════════════
【★ 定位：SQLite 只是持久化层，不是第二套计算引擎】
════════════════════════════════════════════════════════════════════════
本文件里**没有一处聚合**：没有 SUM / GROUP BY / AVG，也不许有。
它只干两件事：

    ① 把解析出来的行**原样**写进去（一行一条，值原样存）
    ② 按条件把行**原样**取出来（WHERE 只有数据集/表名，排序只按写入顺序）

所有业务数字（销售额、地区分布…）一律在**拿到 DataFrame 之后**由现有确定性计算层算
（`app/engine/metrics.py` 的 D16 掩码 + `line_amount`，见 regions.py）。评审原话：
「不要把 SQLite 设计成第二套计算引擎…否则最后形成三套数字来源」。这条是硬约束。

════════════════════════════════════════════════════════════════════════
【位置与隔离】
════════════════════════════════════════════════════════════════════════
    data/app.db                       默认库（单机单文件，无服务端）
    data/original/<file_id>_<名字>     原文件的**保留副本**（评审 #6：原文件 = 证据）
    环境变量 SRA_DB_PATH / SRA_ORIGINAL_DIR 覆盖上面两个（**测试隔离用**）

★ 路径一律在**调用时**读环境变量（与 app/repositories/json_store.py 同一套做法）——
  测试改了环境变量立刻生效，不需要重建任何模块级常量。
  评审把"测试写脏真实 state/ 与真实 db"列为已经犯过的错，这一层是那件事的防线之一。

════════════════════════════════════════════════════════════════════════
【事务：要么全成，要么什么都不留】
════════════════════════════════════════════════════════════════════════
一次导入的全过程 = parse → validate → materialize → commit metadata → success。
`transaction()` 把 materialize 与 commit metadata 包在**同一个** SQLite 事务里：
任何一步抛错 → 整条 import 记录、整份 dataset/document 一起回滚，**绝不留下半成品**，
也绝不出现"库里没东西但接口说成功"（ASSERT 14/18 钉的就是这个）。

原文件副本不在事务里（它落在文件系统上）——所以顺序是：**先搬原文件，后写库**；
库里回滚了，调用方负责把那份副本删掉（见 pipeline.py 的 except 分支）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.importer.models import ImporterError, MAX_DB_BYTES

# app/importer/db.py → 向上 2 层 = 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app.db"
_DEFAULT_ORIGINAL_DIR = PROJECT_ROOT / "data" / "original"

# 进程内一把锁：sqlite3 的连接对象不保证跨线程安全，而 FastAPI 的同步端点在**线程池**里跑。
# 单用户单进程下这把锁足够（与 json_store 的 RLock 同一量级、同一理由，不做假装安全的分布式锁）。
_LOCK = threading.RLock()

# ════════════════════════════════════════════════════════════════════════
# 表结构（★ 施工指令 §三 FR-003A/B 点名的字段，一个不少）
# ════════════════════════════════════════════════════════════════════════
# 全部用 `IF NOT EXISTS`：启动时幂等建表，不需要迁移框架（本 TASK 不引入 Alembic 之类）。
SCHEMA_SQL = """
-- ① 一份**文件**：一个 sha256 就是一份文件，与"被解析成了什么"无关
CREATE TABLE IF NOT EXISTS source_files (
    source_file_id  TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    original_path   TEXT,                       -- 保留副本的落盘路径（证据）
    original_sha256 TEXT,                       -- 与 sha256 同源，单独留一列便于审计核对
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_files_sha ON source_files(sha256);

-- ② 一次**导入动作**（评审点名的字段全在这里；幂等靠 sha256 + parser_version）
CREATE TABLE IF NOT EXISTS imports (
    import_id       TEXT PRIMARY KEY,
    source_file_id  TEXT NOT NULL REFERENCES source_files(source_file_id),
    source_filename TEXT NOT NULL,
    source_sha256   TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    imported_at     TEXT NOT NULL,
    dataset_id      TEXT,                       -- 可为空（这份格式不产数据集 / 没通过资格判定）
    document_id     TEXT,                       -- 可为空（同上）
    status          TEXT NOT NULL,              -- success / failed / skipped
    error_code      TEXT,
    parser_version  TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1, -- 同文件 + 新 parser_version → 新 revision
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_imports_sha ON imports(source_sha256, parser_version);
CREATE INDEX IF NOT EXISTS idx_imports_source ON imports(source_file_id);

-- ③ 数据集（一个文件 = 一个 dataset container；多 Sheet 是它下面的多个 table，不拆成多个数据源）
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id          TEXT PRIMARY KEY,
    source_file_id      TEXT REFERENCES source_files(source_file_id),
    import_id           TEXT,
    name                TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    table_count         INTEGER NOT NULL DEFAULT 0,
    row_count           INTEGER NOT NULL DEFAULT 0,
    column_count        INTEGER NOT NULL DEFAULT 0,
    date_start          TEXT,
    date_end            TEXT,
    region_dimensions   TEXT NOT NULL DEFAULT '[]',  -- JSON 数组：[{"key":"province","field":"省份"},…]
    field_map           TEXT NOT NULL DEFAULT '{}',  -- JSON：语义字段 → 实际列名
    materialized_at     TEXT NOT NULL,
    parser_version      TEXT NOT NULL,
    status              TEXT NOT NULL,
    analysis_enabled    INTEGER NOT NULL DEFAULT 0,
    meta                TEXT NOT NULL DEFAULT '{}'
);

-- ④ 数据集里的表（多 Sheet / 多张 Markdown 表 / PPT 多张表格都落这里）
CREATE TABLE IF NOT EXISTS dataset_tables (
    table_id     TEXT PRIMARY KEY,
    dataset_id   TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    table_name   TEXT NOT NULL,                 -- 稳定标识（Sheet 名 / md_table_1 / pptx_table_2）
    display_name TEXT NOT NULL,                  -- 给用户看的名字
    ordinal      INTEGER NOT NULL,               -- 原文件里的顺序
    row_count    INTEGER NOT NULL,
    column_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dataset_tables_ds ON dataset_tables(dataset_id);

-- ⑤ 字段（类型/非空率/唯一值个数 —— 地区维度识别与"数据字典"都读这里）
CREATE TABLE IF NOT EXISTS dataset_columns (
    column_id     TEXT PRIMARY KEY,
    dataset_id    TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    table_name    TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    name          TEXT NOT NULL,
    inferred_type TEXT NOT NULL,                -- text / integer / number / date / bool
    non_null      INTEGER NOT NULL,
    null_count    INTEGER NOT NULL,
    distinct_count INTEGER NOT NULL,
    samples       TEXT NOT NULL DEFAULT '[]',   -- JSON 数组（前几个不同值，给人看的）
    region_key    TEXT                          -- 命中地区维度时写 region/province/city…
);
CREATE INDEX IF NOT EXISTS idx_dataset_columns_ds ON dataset_columns(dataset_id, table_name);

-- ⑥ 物化的行（★ **原样存**，一行一条 JSON；不做任何计算、不留任何聚合结果）
CREATE TABLE IF NOT EXISTS dataset_rows (
    dataset_id  TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    table_name  TEXT NOT NULL,
    row_index   INTEGER NOT NULL,
    payload     TEXT NOT NULL,                  -- JSON 对象：{列名: 规范化后的值}
    PRIMARY KEY (dataset_id, table_name, row_index)
);

-- ⑦ 文档（正文资料；一份文件可以既有 document 又有 dataset，两者共享 source_file_id）
CREATE TABLE IF NOT EXISTS documents (
    document_id    TEXT PRIMARY KEY,
    source_file_id TEXT REFERENCES source_files(source_file_id),
    import_id      TEXT,
    filename       TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    text           TEXT NOT NULL,
    char_count     INTEGER NOT NULL,
    block_count    INTEGER NOT NULL DEFAULT 0,
    imported_at    TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    meta           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_file_id);

-- ⑧ 表结构版本（与 json_store 的 schema_version 同一个思路：能回答"这份库是哪版建的"）
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: 这份库的表结构版本。**表结构一改就要 +1**（并顺手写迁移）；
#: 与 PARSER_VERSION 不是一回事：那个管"同一份文件要不要重解析"，这个管"库长什么样"。
SCHEMA_VERSION = 1


# ════════════════════════════════════════════════════════════════════════
# 路径（都在**调用时**读环境变量 —— 测试才能靠改环境做隔离）
# ════════════════════════════════════════════════════════════════════════
def db_path() -> Path:
    """SQLite 库路径（`SRA_DB_PATH` 可覆盖；默认 `<项目根>/data/app.db`）。"""
    return Path(os.environ.get("SRA_DB_PATH") or _DEFAULT_DB_PATH)


def original_dir() -> Path:
    """原文件**保留副本**的目录（`SRA_ORIGINAL_DIR` 可覆盖；不存在则创建）。

    与 upload_dir()（临时落盘）分开：上传目录里的东西是"待处理的输入"，
    这里的是"已物化的证据"，语义不同、清理策略也不同（评审 #6：原文件保留）。
    """
    path = Path(os.environ.get("SRA_ORIGINAL_DIR") or _DEFAULT_ORIGINAL_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_size_bytes() -> int:
    """当前库文件大小（不存在算 0）。"""
    try:
        return db_path().stat().st_size
    except OSError:
        return 0


def ensure_capacity(incoming_bytes: int = 0) -> None:
    """写库前的容量闸门（评审 §8⑧：SQLite 总容量也要有上限，否则就是个 DoS 入口）。"""
    size = db_size_bytes() + max(0, int(incoming_bytes))
    if size > MAX_DB_BYTES:
        raise ImporterError(
            "db_capacity_exceeded",
            f"数据库已用到 {size / 1048576:.0f}MB，超过 {MAX_DB_BYTES // 1048576}MB 上限 —— "
            f"请先清理不再需要的数据源再导入。",
        )


# ════════════════════════════════════════════════════════════════════════
# 连接与事务
# ════════════════════════════════════════════════════════════════════════
def connect() -> sqlite3.Connection:
    """打开一个连接（`row_factory` 设为 sqlite3.Row，调用方按列名取值）。

    为什么不用全局长连接：FastAPI 的同步端点跑在线程池里，长连接跨线程用会出问题；
    每次开一次连接的代价在单用户量级下可以忽略（SQLite 打开一个本地文件是微秒级）。
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")     # 读写不互相阻塞（单进程也用得上）
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_schema() -> None:
    """幂等建表（启动时调一次；重复调无副作用）。"""
    with _LOCK, connect() as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """一个**真事务**：块内全成 → COMMIT；任何异常 → ROLLBACK 后把异常原样抛出。

    为什么显式 `BEGIN IMMEDIATE`：默认的延迟事务在"先读后写"时会等到写的那一刻才拿锁，
    两个并发导入可能各自读到"还没有这份文件"然后都去插 —— 幂等就破了。
    IMMEDIATE 一上来就拿写锁，把"查重 + 写入"整体串起来。
    """
    connection = connect()
    try:
        with _LOCK:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
    finally:
        connection.close()


@contextmanager
def readonly() -> Iterator[sqlite3.Connection]:
    """只读查询用的连接（不开事务、不加写锁）。"""
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def table_names() -> list[str]:
    """库里现有的表名（自检与测试用）。"""
    with readonly() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    return [str(row["name"]) for row in rows]


def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    """取一个标量（测试与自检用；**只允许在测试/诊断路径上调用聚合查询**）。

    ⚠️ 业务代码不许用它算业务指标 —— 那是"SQLite 当第二套计算引擎"。它的用途是
    `SELECT COUNT(*) FROM dataset_rows` 这类**运维计数**（表几行、库多大）。
    """
    with readonly() as connection:
        row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


__all__ = [
    "PROJECT_ROOT",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "connect",
    "db_path",
    "db_size_bytes",
    "ensure_capacity",
    "init_schema",
    "original_dir",
    "readonly",
    "scalar",
    "table_names",
    "transaction",
]
