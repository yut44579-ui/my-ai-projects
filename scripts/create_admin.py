"""创建首个管理员账号（TASK-019）。

═══════════════════════════════════════════════════════════════════════
【为什么是命令行脚本而不是"首次注册"接口】
═══════════════════════════════════════════════════════════════════════
    AUTH_SPEC §9：如果提供"首次注册"接口，部署后**任何人都能抢注管理员**。
    所以首个管理员只能由能登服务器的人执行脚本创建。

★ 种子账号**不写进迁移**：迁移文件在仓库里，塞默认口令是安全反模式。

用法（在项目根目录）：
    .venv/Scripts/python.exe -m scripts.create_admin --username admin --password '...' --name '张三'
或：
    .venv/Scripts/python.exe scripts/create_admin.py --username admin --password '...'

★ 口令也可以走环境变量 ADMIN_PASSWORD，避免出现在命令历史里。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 允许 `python scripts/create_admin.py` 直接跑：把 backend 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="创建管理员账号（幂等可选）")
    parser.add_argument("--username", required=True, help="登录名（会统一转小写）")
    parser.add_argument(
        "--password",
        default=None,
        help="登录口令（至少 8 位）。不传则读环境变量 ADMIN_PASSWORD",
    )
    parser.add_argument("--name", default=None, help="显示名，默认与登录名相同")
    parser.add_argument("--role", default="ADMIN", choices=["ADMIN", "MEMBER"])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="若账号已存在则重置其口令（同时使旧令牌失效）",
    )
    args = parser.parse_args()

    password = args.password or os.environ.get("ADMIN_PASSWORD")
    if not password:
        print("错误：必须提供口令（--password 或环境变量 ADMIN_PASSWORD）", file=sys.stderr)
        return 2
    if len(password) < 8:
        print("错误：口令至少 8 位", file=sys.stderr)
        return 2

    username = args.username.strip().lower()
    db = SessionLocal()
    try:
        from sqlalchemy import select

        existing = db.scalar(select(User).where(User.username == username))

        if existing is not None:
            if not args.reset:
                print(f"账号 {username} 已存在（id={existing.id}）。要重置口令请加 --reset")
                return 1
            existing.password_hash = hash_password(password)
            existing.token_version += 1  # 旧令牌立即失效
            if args.name:
                existing.display_name = args.name
            if args.role:
                existing.role = UserRole(args.role)
            db.commit()
            print(f"已重置账号 {username}（id={existing.id}）的口令；其旧令牌已失效")
            return 0

        user = User(
            username=username,
            display_name=(args.name or username),
            password_hash=hash_password(password),
            role=UserRole(args.role),
            is_active=True,
            token_version=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"已创建账号 {user.username}（id={user.id}，角色 {user.role.value}）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
