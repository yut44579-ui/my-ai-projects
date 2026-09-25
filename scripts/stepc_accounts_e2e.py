"""scripts/stepc_accounts_e2e.py · 账号系统（管理员审批 + 游客）**真服务**端到端。

     .venv/Scripts/python.exe scripts/stepc_accounts_e2e.py

与 tests/ 里那些用例的区别：这里起**真 uvicorn 子进程**、用真 HTTP 打、并且**真的把服务
杀掉再拉起来**。用户点名的两条硬证据只有这条路能给出：

  ① 「重启服务后账号仍在」—— 不是"再 new 一个 TestClient"（那还在同一个进程里），
     而是 kill 掉进程、重新起一个，看注册过的账号还能不能登进来；
  ② 「游客真的被拦住不是只弹窗」—— 后端这一侧：游客没有会话编号，
     账号列表 / 审批 / 删除三条管理端点全部 401（不是靠前端不显示按钮）。

落盘目录是**沙箱**（outputs/ 下的临时目录），不碰仓库真实的 state/。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
PLAIN = "Sup3r-秘密-2026"
BOSS, MEMBER = "老板", "小李"

FAILED: list[str] = []
TOTAL = 0


def check(condition: bool, label: str, extra: str = "") -> None:
    global TOTAL
    TOTAL += 1
    if condition:
        print(f"  PASS  {label}" + (f"  {extra}" if extra else ""))
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}  {extra}")


def serve(port: int, env: dict) -> subprocess.Popen:
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60.0)
    for _ in range(180):                       # 首次 import pandas 有点慢，给足时间
        if proc.poll() is not None:
            raise RuntimeError(f"服务起来了又退出：{(proc.stdout.read() or b'')[-800:]!r}")
        try:
            if client.get("/api/health").status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("服务 90 秒内没起来（/api/health 一直读不到）")


def stop(proc: subprocess.Popen) -> None:
    """真的把服务杀掉（这就是"重启"的前半步）。"""
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)


def captcha(client: httpx.Client) -> dict:
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert len(code) == 4, f"图里应当正好 4 个字符，实际 {code!r}"
    return {"captcha_id": data["captcha_id"], "captcha_text": code}


def register(client: httpx.Client, name: str) -> httpx.Response:
    return client.post("/api/auth/register",
                       json={"username": name, "password": PLAIN, **captcha(client)})


def login(client: httpx.Client, name: str, password: str = PLAIN) -> httpx.Response:
    return client.post("/api/auth/login",
                       json={"username": name, "password": password, **captcha(client)})


def main() -> int:
    parser = argparse.ArgumentParser(description="账号系统真服务端到端")
    parser.add_argument("--port", type=int, default=8541)
    parser.add_argument("--keep", action="store_true", help="跑完不删沙箱（排查用）")
    args = parser.parse_args()

    sandbox = tempfile.mkdtemp(prefix="sra_accounts_e2e_", dir=str(PROJECT_ROOT / "outputs"))
    env = dict(os.environ)
    env["SRA_STATE_DIR"] = os.path.join(sandbox, "state")
    env["SRA_UPLOAD_DIR"] = os.path.join(sandbox, "uploads")
    env["SRA_OUTPUT_DIR"] = os.path.join(sandbox, "outputs")
    for key in ("SRA_STATE_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR"):
        os.makedirs(env[key], exist_ok=True)
    print(f"落盘沙箱：{sandbox}")

    accounts_file = pathlib.Path(env["SRA_STATE_DIR"]) / "accounts.json"
    base = f"http://127.0.0.1:{args.port}"
    proc = serve(args.port, env)
    try:
        client = httpx.Client(base_url=base, timeout=600.0)  # 能力清单要读一次数据（冷启动 100 多秒）

        print("\n【1】第一个账号：自动是管理员、直接可用")
        first = register(client, BOSS)
        check(first.status_code == 200, "注册第一个账号 → 200", first.text[:160])
        account = first.json()["account"]
        check(account["role"] == "admin", "第一个注册的账号自动成为管理员", account["role"])
        check(account["status"] == "active", "而且直接可用（否则没人能批账号）", account["status"])
        check("pwd_hash" not in first.text and "pwd_salt" not in first.text,
              "响应里没有校验值与盐")
        check(login(client, BOSS).status_code == 200, "管理员注册完就能登录")

        print("\n【2】第二个账号：等待批准（不是「账号或密码不对」）")
        second = register(client, MEMBER)
        check(second.json()["account"]["status"] == "pending", "第二个账号状态 = 等待批准")
        pending = login(client, MEMBER)
        check(pending.status_code == 403, "带着正确密码登录 → 403（不是放行、也不是 401）")
        check("等待管理员批准" in pending.json()["error"]["message"],
              "说的是「账号正在等待管理员批准」", pending.json()["error"]["message"])

        print("\n【3】★ 游客在后端这一侧真的被拦住（不是只被弹窗挡住）")
        guest_list = client.get("/api/auth/accounts")
        check(guest_list.status_code == 401, "游客读账号列表 → 401", f"status={guest_list.status_code}")
        check(guest_list.json()["error"]["code"] == "session_invalid", "错误码是会话无效")
        guest_review = client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "approve"})
        check(guest_review.status_code == 401, "游客批准账号 → 401（审批动作碰不到）")
        guest_delete = client.delete(f"/api/auth/accounts/{MEMBER}")
        check(guest_delete.status_code == 401, "游客删除账号 → 401")
        pending_still = login(client, MEMBER)
        check(pending_still.status_code == 403, "上面三次都没生效：账号仍在等待批准")

        print("\n【4】管理员审批：批准之后就能登")
        admin = login(client, BOSS).json()["account"]
        sid = admin["session_id"]
        listing = client.get("/api/auth/accounts", params={"session_id": sid})
        check(listing.status_code == 200 and listing.json()["pending"] == 1,
              "管理员读列表：1 个等待批准", f"pending={listing.json().get('pending')}")
        approved = client.post(f"/api/auth/accounts/{MEMBER}/review",
                               json={"action": "approve", "session_id": sid})
        check(approved.status_code == 200, "批准 → 200", approved.text[:160])
        check(approved.json()["account"]["reviewed_by"] == BOSS, "记下了是谁批的（审计）")
        check(login(client, MEMBER).status_code == 200, "批完就能登录了")
        member = login(client, MEMBER).json()["account"]
        check(client.get("/api/auth/accounts", params={"session_id": member["session_id"]})
              .status_code == 403, "普通账号读列表 → 403（不是管理员）")

        print("\n【5】★★ 重启服务：账号还在，还能登（用户点名的硬证据）")
        raw_before = accounts_file.read_text(encoding="utf-8")
        check(BOSS in raw_before and MEMBER in raw_before, "重启前：两个账号都落在账号文件里")
        check(PLAIN not in raw_before, "重启前：文件里没有明文密码")
        stop(proc)
        print("  · 服务已停（进程真的被杀掉）")
        proc = serve(args.port, env)
        print("  · 服务已重新起（新进程）")
        client = httpx.Client(base_url=base, timeout=600.0)  # 能力清单要读一次数据（冷启动 100 多秒）
        check(accounts_file.read_text(encoding="utf-8") == raw_before, "重启后账号文件一字未变")
        after = login(client, BOSS)
        check(after.status_code == 200, "重启后管理员照旧能登录", after.text[:160])
        check(login(client, MEMBER).status_code == 200, "重启后普通账号照旧能登录")
        # 会话在内存里：重启即全部失效（这一条要如实，不能吹成"令牌"）
        old_sid = sid
        check(client.get("/api/auth/accounts", params={"session_id": old_sid}).status_code == 401,
              "重启前那条本机会话失效了（会话在内存里，不是令牌）")
        admin_again = login(client, BOSS).json()["account"]
        sid = admin_again["session_id"]
        check(client.get("/api/auth/accounts", params={"session_id": sid}).status_code == 200,
              "重新登录拿到新会话 → 又能管账号")

        print("\n【6】停用 / 恢复 / 删除")
        client.post(f"/api/auth/accounts/{MEMBER}/review",
                    json={"action": "disable", "session_id": sid})
        disabled = login(client, MEMBER)
        check(disabled.status_code == 403 and "已被停用" in disabled.json()["error"]["message"],
              "停用后登录说的是「该账号已被停用」")
        client.post(f"/api/auth/accounts/{MEMBER}/review",
                    json={"action": "enable", "session_id": sid})
        check(login(client, MEMBER).status_code == 200, "恢复之后能登回来")
        removed = client.request("DELETE", f"/api/auth/accounts/{MEMBER}",
                                 params={"session_id": sid})
        check(removed.status_code == 200, "删除普通账号 → 200", removed.text[:160])
        check(MEMBER not in accounts_file.read_text(encoding="utf-8"), "记录真的从账号文件里消失了")
        check(login(client, MEMBER, PLAIN).status_code == 401,
              "删掉之后用原密码登录 → 401（账号不存在与密码错同一句话）")
        last = client.request("DELETE", f"/api/auth/accounts/{BOSS}", params={"session_id": sid})
        check(last.status_code == 400, "唯一的管理员删不得 → 400", last.text[:160])

        print("\n【7】既有能力没被这次改动碰坏")
        check(client.get("/api/health").status_code == 200, "健康检查照旧 200")
        check(client.get("/api/chat/capabilities").status_code == 200, "问答能力清单照旧 200")
        check(client.get("/").status_code == 200, "首页照旧 200")
    finally:
        stop(proc)
        if args.keep:
            print(f"\n沙箱保留：{sandbox}")
        else:
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n共 {TOTAL} 项检查：通过 {TOTAL - len(FAILED)}，未通过 {len(FAILED)}")
    for item in FAILED:
        print(f"  ✗ {item}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
