"""scripts/fr002a_local_mode_e2e.py · FR-002A 本机单用户模式 + 启动迁移 **真服务**端到端。

     .venv/Scripts/python.exe scripts/fr002a_local_mode_e2e.py

与 tests/test_fr002a_local_mode.py 的区别：这里起**真 uvicorn 子进程**、用真 HTTP 打，
并且**先在磁盘上摆好那个死锁现场**（库里只有 pending 账号、没有任何可用管理员），
然后再启动服务 —— 验的就是"用户重新把服务打开，就进得去了"这一件事本身。

复用 `scripts/stepc_accounts_e2e.py` 的 serve / stop / captcha / login / check 那一套
（"起真服务、等就绪、发真 HTTP"在同一台机器上只写一次，不另造一份）。

落盘是**沙箱**（outputs/ 下的临时目录），不碰仓库真实的 state/。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile

SCRIPTS = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS.parent
for _path in (str(PROJECT_ROOT), str(SCRIPTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import stepc_accounts_e2e as sc  # noqa: E402 —— 复用那套真服务工具，不重复写一遍

# 死锁现场的两个账号：都是 pending，而库里**没有任何可用管理员**。
# created_at 的顺序有意义：更早注册的那个应当被提成管理员。
OLDER = "老板"        # 2026-09-01 注册（更早）
NEWER = "小李"        # 2026-09-20 注册


def seed_deadlock(accounts_file: pathlib.Path) -> None:
    """在磁盘上造出"用户那次真实死锁"的账号表。

    ⚠️ 账号记录的**形状**必须与 `app/accounts.py::register` 造出来的一致
    （包括 pwd_* 那四个摘要字段）—— 密码用真的 PBKDF2 现算，
    这样"迁移之后原密码照旧能登录"才是真验过的，而不是拿假记录糊过去。
    """
    from app import accounts  # noqa: PLC0415 —— 只为算一个真摘要

    records = []
    for username, created_at in ((NEWER, "2026-09-20T10:00:00+08:00"),
                                 (OLDER, "2026-09-01T10:00:00+08:00")):
        records.append({
            "username": username, "display_name": username,
            "status": accounts.STATUS_PENDING, "role": accounts.ROLE_USER,
            "created_at": created_at, "last_login_at": None,
            "reviewed_at": None, "reviewed_by": None,
            **accounts.make_secret(sc.PLAIN),
        })
    accounts_file.parent.mkdir(parents=True, exist_ok=True)
    accounts_file.write_text(json.dumps({"schema_version": 1, "accounts": records},
                                        ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="FR-002A 本机单用户模式真服务端到端")
    parser.add_argument("--port", type=int, default=8572)
    parser.add_argument("--keep", action="store_true", help="跑完不删沙箱（排查用）")
    args = parser.parse_args()

    sandbox = tempfile.mkdtemp(prefix="sra_fr002a_e2e_", dir=str(PROJECT_ROOT / "outputs"))
    env = dict(os.environ)
    env["SRA_STATE_DIR"] = os.path.join(sandbox, "state")
    env["SRA_UPLOAD_DIR"] = os.path.join(sandbox, "uploads")
    env["SRA_OUTPUT_DIR"] = os.path.join(sandbox, "outputs")
    # ★ 默认模式：**不设** SRA_REQUIRE_APPROVAL（要验的就是默认值本身）
    env.pop("SRA_REQUIRE_APPROVAL", None)
    for key in ("SRA_STATE_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR"):
        os.makedirs(env[key], exist_ok=True)
    print(f"落盘沙箱：{sandbox}")

    accounts_file = pathlib.Path(env["SRA_STATE_DIR"]) / "accounts.json"
    seed_deadlock(accounts_file)
    print(f"死锁现场已摆好：{OLDER} / {NEWER} 都是待批准，库里没有任何可用管理员")

    proc = sc.serve(args.port, env)
    try:
        import httpx  # noqa: PLC0415 —— 与 stepc 同一套客户端用法

        client = httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=600.0)

        print("\n【1】★ 死锁回归：服务起来之后，用户能登录（不删库、不改密码）")
        sc.check(sc.login(client, OLDER).status_code == 200,
                 "启动之后用原密码就能登进去（这一次是重启把死锁解开的）")
        sc.check(sc.login(client, NEWER).status_code == 200, "另一个待批准账号也一并收进来了")
        on_disk = json.loads(accounts_file.read_text(encoding="utf-8"))["accounts"]
        by_name = {record["username"]: record for record in on_disk}
        sc.check(len(on_disk) == 2, "账号一个没少（迁移不删账号）", f"{len(on_disk)} 个")
        sc.check(sc.PLAIN not in accounts_file.read_text(encoding="utf-8"),
                 "账号文件里仍然没有明文密码")

        print("\n【2】迁移的痕迹写在记录里（谁、什么时候、因为什么）")
        sc.check(by_name[OLDER]["role"] == "admin", "更早注册的那个成为管理员",
                 by_name[OLDER]["role"])
        sc.check(by_name[OLDER]["status"] == "active", "而且是可用状态")
        sc.check("启动迁移" in (by_name[OLDER]["reviewed_by"] or ""),
                 "留痕：写出处是启动迁移", by_name[OLDER]["reviewed_by"] or "")
        sc.check(by_name[NEWER]["role"] == "user", "另一个账号没有被提权（不产生第二个管理员）")
        sc.check("启动迁移" in (by_name[NEWER]["reviewed_by"] or ""),
                 "另一个账号的留痕也写了", by_name[NEWER]["reviewed_by"] or "")
        admins = [record["username"] for record in on_disk if record["role"] == "admin"]
        sc.check(admins == [OLDER], "全库恰好一个管理员", f"{admins}")

        print("\n【3】本机单用户模式：新注册的账号也直接能用")
        registered = client.post("/api/auth/register",
                                 json={"username": "新来的", "password": sc.PLAIN,
                                       **sc.captcha(client)})
        sc.check(registered.status_code == 200, "注册 → 200", registered.text[:160])
        sc.check(registered.json()["account"]["status"] == "active",
                 "注册即生效（不需要任何人批准）", registered.json()["account"]["status"])
        sc.check(sc.login(client, "新来的").status_code == 200, "刚注册的账号当场就能登录")

        print("\n【4】重启一次：迁移是幂等的，账号文件一字不变")
        raw_before = accounts_file.read_text(encoding="utf-8")
        sc.stop(proc)
        proc = sc.serve(args.port, env)
        client = httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=600.0)
        sc.check(accounts_file.read_text(encoding="utf-8") == raw_before,
                 "再启动一次：账号文件一字未变（幂等）")
        sc.check(sc.login(client, OLDER).status_code == 200, "重启后管理员照旧能登")
        sc.check(sc.login(client, NEWER).status_code == 200, "重启后普通账号照旧能登")

        print("\n【5】既有能力没被这次改动碰坏")
        sc.check(client.get("/api/health").status_code == 200, "健康检查照旧 200")
        sc.check(client.get("/").status_code == 200, "首页照旧 200")
    finally:
        sc.stop(proc)
        if args.keep:
            print(f"\n沙箱保留：{sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n共 {sc.TOTAL} 项检查：通过 {sc.TOTAL - len(sc.FAILED)}，未通过 {len(sc.FAILED)}")
    for item in sc.FAILED:
        print(f"  ✗ {item}")
    return 0 if not sc.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
