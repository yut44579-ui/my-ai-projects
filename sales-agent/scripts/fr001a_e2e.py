"""scripts/fr001a_e2e.py · FR-001A 密码恢复闭环的**真服务**端到端。

    .venv/Scripts/python.exe scripts/fr001a_e2e.py

与 `tests/test_fr001a_recovery.py` 的分工：
    · 测试文件用 `TestClient`（进程内）把 52 条验收逐条钉死，跑得快、覆盖全；
    · **本脚本起真 uvicorn 子进程、用真 HTTP 打**，走的是用户真正走的那条路 ——
      尤其是"重启服务之后一次性凭证全部失效"这一条，进程内的 TestClient 证不了
      （它压根没有"重启"这回事）。

它验的是四件测试文件证不了 / 证起来别扭的事：
    ① **重启即失效**：reset_token / ticket / session 全在内存，重启之后一律不认 ——
       这是评审 Q2 点名要的 fail-closed 行为，而"重启"必须真的杀进程再拉起来才算数；
    ② **恢复码落在账号文件里的只有摘要**：真去读那份 JSON，逐字节找明文；
    ③ **临时密码只进一次**：真 HTTP 连打两次登录，第二次必须失败；
    ④ **must_change 会话的业务 API 真的 403**（带 X-Session-Id 头打真服务）。

落盘目录是**沙箱**（outputs/ 下的临时目录），不碰仓库真实的 state/。
"""

from __future__ import annotations

import argparse
import json
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
NEW_PLAIN = "N3w-密码-2026x"
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
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)


def captcha(client: httpx.Client) -> dict:
    """取一张验证码并把图上的字符读出来（走用户走的那条路，不开后门）。"""
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert len(code) == 4, f"图里应当正好 4 个字符，实际 {code!r}"
    return {"captcha_id": data["captcha_id"], "captcha_text": code}


def challenge_fields(client: httpx.Client) -> dict:
    """同一张图，按恢复流程第①步的**冻结字段名**再写一份。"""
    fresh = captcha(client)
    return {**fresh, "challenge_id": fresh["captcha_id"],
            "challenge_proof": fresh["captcha_text"], "captcha": fresh["captcha_text"]}


def register(client: httpx.Client, name: str) -> httpx.Response:
    return client.post("/api/auth/register",
                       json={"username": name, "password": PLAIN, **captcha(client)})


def login(client: httpx.Client, name: str, password: str = PLAIN) -> httpx.Response:
    return client.post("/api/auth/login",
                       json={"username": name, "password": password, **captcha(client)})


def accounts_resolve_stale(client: httpx.Client, stale_session: str) -> bool:
    """重启前那条会话现在还作数吗？（作数返回 False —— 我们要的是"不作数"）

    判据：拿它去调一条**需要会话**的端点。业务端点回 200 说明不了什么
    （它们本来就不按账号鉴权），必须用一条真会话端点来问。
    """
    response = client.post("/api/auth/recovery-code", json={"session_id": stale_session})
    return response.status_code == 401


def main() -> int:
    parser = argparse.ArgumentParser(description="FR-001A 密码恢复闭环真服务端到端")
    parser.add_argument("--port", type=int, default=8542)
    parser.add_argument("--keep", action="store_true", help="跑完不删沙箱（排查用）")
    args = parser.parse_args()

    sandbox = tempfile.mkdtemp(prefix="sra_fr001a_e2e_", dir=str(PROJECT_ROOT / "outputs"))
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
        client = httpx.Client(base_url=base, timeout=600.0)

        print("\n【1】先把两个账号摆好（老板 = 管理员，小李 = 普通账号）")
        check(register(client, BOSS).status_code == 200, "注册老板（第一个账号）")
        boss_session = login(client, BOSS).json()["account"]["session_id"]
        check(register(client, MEMBER).json()["account"]["status"] == "pending", "注册小李（等待批准）")
        check(client.post(f"/api/auth/accounts/{MEMBER}/review",
                          json={"action": "approve", "session_id": boss_session}).status_code == 200,
              "老板批准小李")
        member_session = login(client, MEMBER).json()["account"]["session_id"]

        print("\n【2】恢复码：登录后自己生成，只在响应里出现一次")
        made = client.post("/api/auth/recovery-code", json={"session_id": member_session})
        check(made.status_code == 200, "小李生成恢复码 → 200", made.text[:120])
        code = made.json()["recovery_code"]
        check(len(code.replace("-", "")) == 20 and code.count("-") == 4,
              "恢复码是 20 位、4-4-4-4-4 分组", code)
        raw = accounts_file.read_text(encoding="utf-8")
        check(code not in raw and code.replace("-", "") not in raw,
              "账号文件里没有恢复码明文（只存摘要）")
        record = [item for item in json.loads(raw)["accounts"] if item["username"] == MEMBER][0]
        check(len(record.get("recovery_code_hash") or "") == 64,
              "落盘的是 64 位十六进制摘要", record.get("recovery_code_hash", "")[:16] + "…")
        check(code not in client.get("/api/auth/accounts",
                                     params={"session_id": boss_session}).text,
              "管理员读账号列表也拿不到它（只返回一次）")
        check(client.post("/api/auth/recovery-code",
                          json={"session_id": "no-such-session"}).status_code == 401,
              "没登录不能生成恢复码 → 401")

        print("\n【3】忘记密码三步：账号+验证码 → 恢复码 → 新密码")
        cap_a = challenge_fields(client)
        first = client.post("/api/auth/reset/request", json={"username": MEMBER, **cap_a})
        check(first.status_code == 200, "第①步 → 200", first.text[:120])
        check(set(first.json()) == {"reset_token", "expires_in", "message"},
              "第①步只回这三样（不回答「账号在不在」）")
        ghost = client.post("/api/auth/reset/request",
                            json={"username": "查无此人", **challenge_fields(client)})
        check(ghost.status_code == first.status_code
              and set(ghost.json()) == set(first.json())
              and ghost.json()["message"] == first.json()["message"],
              "不存在的账号：状态码、字段、话逐字同形（防枚举）")

        # 第②步要带**第①步那张** challenge_id（交叉绑定）
        other = captcha(client)                       # 另一张挑战（从没答对过）
        crossed = client.post("/api/auth/reset/verify", json={
            "reset_token": first.json()["reset_token"], "recovery_code": code,
            "challenge_id": other["captcha_id"]})
        check(crossed.status_code == 400, "Token A + Challenge B → 400（交叉绑定生效）",
              f"status={crossed.status_code}")

        step2 = client.post("/api/auth/reset/verify", json={
            "reset_token": first.json()["reset_token"],
            "recovery_code": code.lower().replace("-", " "),    # 小写 + 空格写法
            "challenge_id": cap_a["challenge_id"]})
        check(step2.status_code == 200, "第②步（恢复码用空格小写写法）→ 200", step2.text[:160])
        ticket = step2.json()["ticket"]

        replay = client.post("/api/auth/reset/verify", json={
            "reset_token": first.json()["reset_token"], "recovery_code": code,
            "challenge_id": cap_a["challenge_id"]})
        check(replay.status_code == 400, "同一张 reset_token 重放 → 400")

        print("\n【4】★ 重启服务：一次性凭证全部失效（fail-closed）")
        stop(proc)
        print("  · 服务已停（进程真的被杀掉）")
        proc = serve(args.port, env)
        print("  · 服务已重新起（新进程）")
        client = httpx.Client(base_url=base, timeout=600.0)
        after_restart = client.post("/api/auth/reset/commit",
                                    json={"ticket": ticket, "new_password": NEW_PLAIN})
        check(after_restart.status_code == 400, "重启前的 ticket → 重启后不认（400）",
              f"status={after_restart.status_code}")
        check(login(client, MEMBER, PLAIN).status_code == 200, "重启后小李的原密码照样能登（没被改）")

        print("\n【5】重新走一遍三步，这次走到改密")
        # 刚才那张恢复码在第②步用掉了（一次性），而且重启把会话也清了 —— 重新登录再生成一张
        check(accounts_resolve_stale(client, member_session),
              "重启后重启前那条会话不认了（会话也在内存里）")
        member_session = login(client, MEMBER).json()["account"]["session_id"]
        code = client.post("/api/auth/recovery-code",
                           json={"session_id": member_session}).json()["recovery_code"]
        cap = challenge_fields(client)
        token = client.post("/api/auth/reset/request",
                            json={"username": MEMBER, **cap}).json()["reset_token"]
        ticket = client.post("/api/auth/reset/verify", json={
            "reset_token": token, "recovery_code": code,
            "challenge_id": cap["challenge_id"]}).json()["ticket"]
        committed = client.post("/api/auth/reset/commit",
                                json={"ticket": ticket, "new_password": NEW_PLAIN,
                                      "username": BOSS})       # ← 故意注入 username
        check(committed.status_code == 200 and committed.json()["username"] == MEMBER,
              "第③步改的是票据绑定的账号（注入 username 无效）", committed.text[:160])
        check(login(client, MEMBER, NEW_PLAIN).status_code == 200, "新密码能登")
        check(login(client, MEMBER, PLAIN).status_code == 401, "旧密码登不上")
        check(login(client, BOSS, PLAIN).status_code == 200, "老板没被牵连（注入的那个 username 无效）")
        raw = accounts_file.read_text(encoding="utf-8")
        check(code not in raw, "用过的恢复码没有留在文件里")

        print("\n【6】管理员临时密码：一次性 + 必须先改密")
        boss_session = login(client, BOSS).json()["account"]["session_id"]   # 重启后重登
        made = client.post(f"/api/auth/accounts/{MEMBER}/temp-password",
                           json={"session_id": boss_session})
        check(made.status_code == 200, "管理员发临时密码 → 200", made.text[:120])
        temp = made.json()["temp_password"]
        check(len(temp) == 12, "临时密码 12 位", temp)
        check(temp not in accounts_file.read_text(encoding="utf-8"), "账号文件里没有临时密码明文")
        member_session2 = login(client, MEMBER, NEW_PLAIN).json()["account"]["session_id"]
        check(client.post(f"/api/auth/accounts/{BOSS}/temp-password",
                          json={"session_id": member_session2}).status_code == 403,
              "普通账号给老板发临时密码 → 403")
        check(client.post(f"/api/auth/accounts/{BOSS}/temp-password",
                          json={"session_id": boss_session}).status_code == 403,
              "管理员给管理员发临时密码 → 403（不许管理员之间互相接管）")

        temp_login = login(client, MEMBER, temp)
        check(temp_login.status_code == 200, "用临时密码登录 → 200")
        temp_session = temp_login.json()["account"]["session_id"]
        check(temp_login.json()["account"]["must_change_password"] is True,
              "登录响应里标记了「必须先改密」")
        guarded = client.get("/api/datasets", headers={"X-Session-Id": temp_session})
        check(guarded.status_code == 403 and guarded.json()["error"]["code"] == "must_change_password",
              "★ 未改密时业务 API → 403（服务端拦的，不是页面不显示按钮）",
              f"status={guarded.status_code}")
        check(client.post("/api/auth/recovery-code",
                          json={"session_id": temp_session}).status_code == 403,
              "未改密时也不能生成恢复码")
        second_login = login(client, MEMBER, temp)
        check(second_login.status_code == 401, "临时密码只能登录一次（第二次 401）")

        changed = client.post("/api/auth/password/change", json={
            "old_password": temp, "new_password": NEW_PLAIN, "session_id": temp_session})
        check(changed.status_code == 200, "改密 → 200", changed.text[:120])
        fresh = changed.json()["session_id"]
        check(fresh and fresh != temp_session, "换了一条新会话（密码变了 = 认证状态重建）")
        check(client.post("/api/auth/recovery-code",
                          json={"session_id": temp_session}).status_code == 401,
              "改密之后那条旧会话作废 → 401")
        check(client.get("/api/datasets",
                         headers={"X-Session-Id": fresh}).status_code == 200,
              "新会话能正常用（业务 API 200）")
        check(login(client, MEMBER, NEW_PLAIN).status_code == 200, "改完用新密码登录 → 200")
        check(login(client, MEMBER, temp).status_code == 401, "临时密码用完即废 → 401")

        print("\n【7】没有会话的普通请求照旧（不许改既有端点行为）")
        check(client.get("/api/health").status_code == 200, "/api/health 不带会话头 → 200")
        check(client.get("/api/datasets").status_code == 200, "/api/datasets 不带会话头 → 200")

        print("\n【8】凭证一个都没进服务端日志")
        stop(proc)
        log = (proc.stdout.read() or b"").decode("utf-8", "replace")
        for label, secret in (("恢复码", code), ("临时密码", temp), ("reset_token", token),
                              ("ticket", ticket)):
            check(secret not in log, f"{label} 不在服务端日志里")
    finally:
        if proc.poll() is None:
            stop(proc)
        if not args.keep:
            import shutil
            shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n共 {TOTAL} 项检查：通过 {TOTAL - len(FAILED)}，未通过 {len(FAILED)}")
    for label in FAILED:
        print(f"  · 未通过：{label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
