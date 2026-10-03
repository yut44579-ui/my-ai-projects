"""pytest 公共夹具。"""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

# 让 tests/ 能 import backend/app（pytest.ini 里也配了 pythonpath，这里双保险）
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.import_batch import ImportBatch  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

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


@pytest.fixture
def clean_imports(db: Session) -> Generator[None, None, None]:
    """导入类测试的隔离：测试前后都清掉测试产生的批次与客户。

    这样测试可以**反复运行**：同一个 sha256 的夹具文件不会因为上一轮跑过而被判"已导入过"。
    只删本测试新增的行（按 id 基线 + 夹具 sha 双保险），不碰库里别的数据。
    """
    baseline_customer = db.execute(select(func.coalesce(func.max(Customer.id), 0))).scalar_one()
    baseline_batch = db.execute(select(func.coalesce(func.max(ImportBatch.id), 0))).scalar_one()
    fixture_shas = _fixture_sha256()

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

    purge()
    yield
    purge()


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
