"""pytest 公共夹具。"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

# 让 tests/ 能 import backend/app（pytest.ini 里也配了 pythonpath，这里双保险）
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.customer import Customer, CustomerSourceType, LifecycleStatus  # noqa: E402
from app.models.customer_event import CustomerEvent  # noqa: E402
from app.models.import_batch import ImportBatch  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
# 被临时摘除的既有客户的兜底快照（测试结束会删；进程被硬杀时可据此手动恢复）
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tmp" / "pytest-detached-customers.json"

CLEAN_FIXTURE = FIXTURE_DIR / "customers_sample_TEST_clean.csv"
MESSY_FIXTURE = FIXTURE_DIR / "customers_sample_TEST_messy.csv"


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """直连本机 MySQL 的会话（真实库，不 mock）。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _fixture_sha256() -> set[str]:
    from app.services.tabular import sha256_of

    return {sha256_of(p.read_bytes()) for p in FIXTURE_DIR.iterdir() if p.is_file()}


def _fixture_dedupe_keys() -> tuple[set[str], set[str]]:
    """夹具文件里出现过的（规范化后的）手机号 / 邮箱 —— 也就是该文件的去重键。

    复用项目自己的解析与映射（parse_table + auto_map_columns + normalize_*），
    不另写一套 CSV 解析。
    """
    from app.services.dedupe import normalize_email, normalize_phone
    from app.services.mapping import auto_map_columns
    from app.services.tabular import parse_table

    phones: set[str] = set()
    emails: set[str] = set()
    for path in sorted(FIXTURE_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".csv":
            continue
        table = parse_table(path.read_bytes(), path.name)
        targets = [col.target for col in auto_map_columns(table.headers)]
        for row in table.rows:
            values = dict(zip(targets, row))
            phone = normalize_phone(values.get("phone"))
            email = normalize_email(values.get("email"))
            if phone:
                phones.add(phone)
            if email:
                emails.add(email)
    return phones, emails


def _with_deadlock_retry(db: Session, run, attempts: int = 3) -> None:
    """执行一段写操作并在 MySQL 死锁（1213）时重试。

    本夹具会 DELETE/INSERT 既有客户行，与另一个 Session 的写入偶发锁冲突是正常现象
    （InnoDB 会把其中一方判为死锁受害者）。这里只给重试，不吞别的错误。
    """
    for attempt in range(1, attempts + 1):
        try:
            run()
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            is_deadlock = bool(exc.orig and exc.orig.args and exc.orig.args[0] == 1213)
            if not is_deadlock or attempt == attempts:
                raise
            time.sleep(0.2 * attempt)


@pytest.fixture
def clean_imports(db: Session) -> Generator[None, None, None]:
    """导入类测试的隔离：测试前后都清掉测试产生的批次与客户。

    这样测试可以**反复运行**：同一个 sha256 的夹具文件不会因为上一轮跑过而被判"已导入过"。
    只删本测试新增的行（按 id 基线 + 夹具 sha 双保险），不碰库里别的数据。

    ★ TASK-002 追加的一段隔离：本机库里可能已经存在**与夹具同手机号/邮箱**的既有客户
      （例如用同样号码造的演示数据）。这类行会让夹具文件的导入被判成"去重命中"，
      使「导入 8 行 → 新建 8 行」不再成立。处理办法：测试开始前把这些行**临时摘除**
      （先整行快照，包括 id），测试结束、清完本轮新数据后**原样写回**，一行都不删。
      快照同时落一份到 tmp/，万一进程被硬杀也能手动恢复。
    """
    baseline_customer = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()
    baseline_batch = db.execute(select(func.coalesce(func.max(ImportBatch.id), 0))).scalar_one()
    fixture_shas = _fixture_sha256()
    fixture_phones, fixture_emails = _fixture_dedupe_keys()
    columns = [c.name for c in Customer.__table__.columns]
    detached: list[dict] = []

    def purge() -> None:
        db.rollback()
        stale_batch_ids = list(
            db.execute(
                select(ImportBatch.id).where(ImportBatch.file_sha256.in_(fixture_shas))
            ).scalars()
        )
        db.execute(
            delete(Customer).where(
                or_(
                    Customer.id > baseline_customer,
                    Customer.batch_id.in_(stale_batch_ids),
                )
            )
        )
        db.execute(
            delete(ImportBatch).where(
                or_(
                    ImportBatch.id > baseline_batch,
                    ImportBatch.id.in_(stale_batch_ids),
                )
            )
        )
        db.commit()

    def detach_collisions() -> None:
        """把与夹具同键的既有客户整行快照后临时摘除（只影响本次测试的时长）。"""
        conditions = []
        if fixture_phones:
            conditions.append(Customer.phone.in_(fixture_phones))
        if fixture_emails:
            conditions.append(Customer.email.in_(fixture_emails))
        if not conditions:
            return
        rows = db.execute(select(Customer).where(or_(*conditions))).scalars().all()
        if not rows:
            return
        for row in rows:
            detached.append({name: getattr(row, name) for name in columns})
        SNAPSHOT_PATH.write_text(
            json.dumps(detached, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        _with_deadlock_retry(
            db,
            lambda: db.execute(
                delete(Customer).where(Customer.id.in_([r["id"] for r in detached]))
            ),
        )

    def restore_collisions() -> None:
        """原样写回（显式 id），保证库里数据与测试前完全一致。"""
        if not detached:
            return
        _with_deadlock_retry(db, lambda: db.execute(insert(Customer), detached))
        detached.clear()
        SNAPSHOT_PATH.unlink(missing_ok=True)

    detach_collisions()
    purge()
    yield
    purge()
    restore_collisions()


@pytest.fixture
def test_customer(db: Session) -> Generator[Customer, None, None]:
    """TASK-006 状态机测试用的临时客户（source_type=TEST，遵守测试数据隔离）。

    直接经 ORM 建行（不走导入接口），测完连同它的事件一起删掉，不污染库。
    """
    customer = Customer(
        name="__TASK006_TEST_CUSTOMER__",
        source_type=CustomerSourceType.TEST,
        lifecycle_status=LifecycleStatus.NEW,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    try:
        yield customer
    finally:
        db.rollback()
        db.execute(delete(CustomerEvent).where(CustomerEvent.customer_id == customer.id))
        db.execute(delete(Customer).where(Customer.id == customer.id))
        db.commit()


def fresh(db: Session) -> None:
    """结束当前事务，丢弃陈旧快照。

    ★ 必须 rollback 而不是 expire_all：MySQL 默认 REPEATABLE READ，
      测试会话若一直挂着同一个读事务，就永远看不到另一个连接（API 请求）刚提交的数据。
    """
    db.rollback()


def refresh_events(db: Session, customer_id: int) -> list[CustomerEvent]:
    """读该客户的事件（按 id 升序 = 发生顺序），读前先结束旧事务。"""
    fresh(db)
    return list(
        db.execute(
            select(CustomerEvent)
            .where(CustomerEvent.customer_id == customer_id)
            .order_by(CustomerEvent.id)
        )
        .scalars()
        .all()
    )


@pytest.fixture
def clean_csv() -> tuple[str, bytes]:
    """(文件名, 字节) —— 8 行正常数据、UTF-8、表头用中文别名。"""
    return CLEAN_FIXTURE.name, CLEAN_FIXTURE.read_bytes()


@pytest.fixture
def messy_csv() -> tuple[str, bytes]:
    """(文件名, 字节) —— GB18030 编码的脏数据文件，覆盖各类坏行与多余列。"""
    return MESSY_FIXTURE.name, MESSY_FIXTURE.read_bytes()


def upload(client: TestClient, filename: str, raw: bytes, **form) -> "object":
    """POST /api/imports/preview 的小助手。"""
    return client.post(
        "/api/imports/preview",
        files={"file": (filename, raw, "text/csv")},
        data={k: v for k, v in form.items() if v is not None},
    )


def commit(client: TestClient, filename: str, raw: bytes, sha256: str, **form) -> "object":
    """POST /api/imports/commit 的小助手。"""
    data = {"sha256": sha256, "source_type": "TEST", **{k: v for k, v in form.items() if v is not None}}
    return client.post(
        "/api/imports/commit",
        files={"file": (filename, raw, "text/csv")},
        data=data,
    )
