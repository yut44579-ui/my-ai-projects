"""一键清理测试导入数据（TASK-001 §六）—— 与 scripts/cleanup_test_data.sql 等价的管理命令。

用法（在项目根目录）：
    .venv/Scripts/python.exe scripts/cleanup_test_data.py            # 真删
    .venv/Scripts/python.exe scripts/cleanup_test_data.py --dry-run  # 只看会删多少

为什么需要它：用 tests/fixtures 里的样本文件跑通链路后，TEST 数据要能一键清掉，
不能留在库里混进汇报数字。删除顺序：先客户后批次（外键方向决定）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.customer import Customer, CustomerSourceType  # noqa: E402
from app.models.import_batch import BatchSourceType, ImportBatch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 source_type='TEST' 的导入数据")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args()

    with SessionLocal() as db:
        test_batches = list(
            db.execute(
                select(ImportBatch).where(ImportBatch.source_type == BatchSourceType.TEST)
            ).scalars()
        )
        batch_ids = [batch.id for batch in test_batches]

        customer_filter = Customer.source_type == CustomerSourceType.TEST
        if batch_ids:
            customer_filter = customer_filter | Customer.batch_id.in_(batch_ids)

        customer_count = db.execute(
            select(func.count()).select_from(Customer).where(customer_filter)
        ).scalar_one()

        print(f"将删除：{customer_count} 条 TEST 客户、{len(test_batches)} 个 TEST 批次")
        for batch in test_batches:
            print(f"  - batch_id={batch.id} {batch.filename} status={batch.status.value}")

        if args.dry_run:
            print("--dry-run：什么都没删")
            return 0

        # 先客户后批次（customers.batch_id 指向 import_batches）
        db.execute(Customer.__table__.delete().where(customer_filter))
        if batch_ids:
            db.execute(ImportBatch.__table__.delete().where(ImportBatch.id.in_(batch_ids)))
        db.commit()

        remaining = db.execute(
            select(func.count()).select_from(Customer).where(Customer.source_type == CustomerSourceType.TEST)
        ).scalar_one()
        print(f"完成。剩余 TEST 客户：{remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
