"""导入批次模型（D11 第 6 表，TASK-001 落地）。

一次导入 = 一行 import_batches。V1 不做撤销导入，"追溯"就靠这张表 + 详情接口。
列与取值全部来自 TASK-001 冻结规格，不多加列（例如不加 imported_by：V1 没有登录体系）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, CHAR, JSON, DateTime, Enum as SAEnum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportBatchStatus(str, Enum):
    """批次状态。SUCCESS=全部行都成功；PARTIAL=好行入库、坏行跳过；FAILED=整文件失败。"""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class SkipReason(str, Enum):
    """行级 / 列级跳过原因。★冻结枚举，禁止自由文本。"""

    MISSING_NAME = "missing_name"
    INVALID_PHONE = "invalid_phone"
    INVALID_EMAIL = "invalid_email"
    EMPTY_ROW = "empty_row"
    UNMAPPED_COLUMN = "unmapped_column"


class BatchSourceType(str, Enum):
    """批次声明的数据来源。

    本 TASK 只会用到 TEST（测试样本文件）；REAL 留给将来真实客户数据接入。
    """

    REAL = "REAL"
    TEST = "TEST"
    MANUAL = "MANUAL"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """让 MySQL 里存的是 'SUCCESS' 这样的值，而不是 'ImportBatchStatus.SUCCESS'。"""
    return [member.value for member in enum_cls]


class ImportBatch(Base):
    """导入批次：一次 preview→commit 的落库记录。"""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    file_sha256: Mapped[str] = mapped_column(
        CHAR(64), nullable=False, comment="文件内容 sha256，用于 preview/commit 一致性与幂等"
    )
    status: Mapped[ImportBatchStatus] = mapped_column(
        SAEnum(
            ImportBatchStatus,
            name="import_batch_status",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    source_type: Mapped[BatchSourceType] = mapped_column(
        SAEnum(BatchSourceType, name="batch_source_type", values_callable=_enum_values),
        nullable=False,
    )

    rows_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_created: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_deduplicated: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # 结构定死：[{"row": 37, "reason": "invalid_phone", "raw": "abc-def"}, ...]
    # raw 只作报错上下文保留，绝不写进业务字段。
    skipped_reasons_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 结构：[{"column": "备注信息XX", "reason": "unmapped_column"}, ...]
    skipped_columns_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    imported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="证据锚点，由代码生成为 import_batch:{id}"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<ImportBatch id={self.id} status={self.status} file={self.filename!r}>"
