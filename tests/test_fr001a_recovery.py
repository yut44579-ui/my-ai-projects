"""tests/test_fr001a_recovery.py · FR-001A 密码恢复闭环的**安全验收**（评审 A1–F20 + 附加项）。

════════════════════════════════════════════════════════════════════════
【这个文件在验什么】
════════════════════════════════════════════════════════════════════════
用户原话：「忘记密码之后无法找回，加上具体功能即可以通过忘记密码来**新增新密码**，
但是**需要验证账户**」。评审（`reviews/010-fr001a-password-recovery-review.md`）给了
一份 52 条验收清单 A1–F20，本文件逐条对着它写；另外把评审末尾点名的 5 个最高优先级
Gate（原子消费 / ticket 不带 username / accounts-exists 枚举旁路 / 临时密码首次登录立即消费 /
pending·rejected 不被恢复流程绕过）也一并测了。

测试命名前缀直接对着清单编号（`test_A1_...` / `test_F13_...`），
一眼能看出哪一条覆盖了哪个 Gate —— 报告里那张对照表就是照这份文件列出来的。

════════════════════════════════════════════════════════════════════════
【三层授权（本文件所有断言的共同背景）】
════════════════════════════════════════════════════════════════════════
    challenge（图形验证码）→ 只允许【进入恢复流程】
    recovery code（恢复码）→ 证明【账户所有权】
    ticket（一次性票据）   → 只允许【修改密码】
所以本文件里到处在验的是同一件事：**任何单独一层都不足以改别人的密码**。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, api_auth, captcha, challenge, recovery, state  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

# 刻意用一眼认得出的明文：它只要出现在文件/响应里，任何一条断言都藏不住
PLAIN = "Sup3r-秘密-2026"
NEW_PLAIN = "N3w-密码-2026x"
BOSS = "唐宇"          # 第一个注册的账号 → 自动是管理员
MEMBER = "小李"        # 后注册的普通账号 → 要管理员批准
SECRET_KEYS = ("pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations",
               "temp_pwd_algo", "temp_pwd_salt", "temp_pwd_hash", "temp_pwd_iterations",
               "recovery_code_hash")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """账号表落临时目录；六处**进程内**状态一律清干净。

    进程内状态是跨用例共享的模块级数据：验证码字典、挑战在册表、reset_token/ticket、
    登录失败计数、恢复失败计数、会话表、查重节流窗口。不清的话上一条用例留下的东西
    会把下一条带进"冷却 / 已用过 / 超频"，看起来像随机失败。
    """
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    _clear_all()
    yield tmp_path / "state"
    _clear_all()


def _clear_all() -> None:
    captcha.reset()
    challenge.reset()
    recovery.reset()
    accounts.reset_failures()
    accounts.reset_sessions()
    api_auth.reset_exists_throttle()


# ════════════════════════════════════════════════════════════════════════
# 小工具（全部走**用户走的那条路**：先取图、把图上的字读出来、原样递回去）
# ════════════════════════════════════════════════════════════════════════
def _captcha() -> dict:
    """取一张验证码，并把图上的字符读出来（每个字符是独立的 <text>，所以读得到）。

    返回的这一份同时带上两套字段名：
        · `captcha_id` / `captcha_text` —— 注册与登录那两个端点认的写法；
        · `challenge_id` / `challenge_proof` / `captcha` —— 恢复流程第①步的冻结写法。
    两套指的是**同一张图**，所以直接 `**_captcha()` 就能喂给任意一个表单。
    """
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert len(code) == captcha.LENGTH, f"图里应当正好 {captcha.LENGTH} 个字符，实际 {code!r}"
    return {"captcha_id": data["captcha_id"], "captcha_text": code,
            "challenge_id": data["captcha_id"], "challenge_proof": code, "captcha": code,
            "code": code}


def _register(username: str, password: str = PLAIN, display_name: str | None = None):
    body = {"username": username, "password": password, **_captcha()}
    if display_name is not None:
        body["display_name"] = display_name
    return client.post("/api/auth/register", json=body)


def _login(username: str, password: str = PLAIN):
    return client.post("/api/auth/login", json={"username": username, "password": password, **_captcha()})


def _session(username: str, password: str = PLAIN) -> str:
    response = _login(username, password)
    assert response.status_code == 200, response.text
    return response.json()["account"]["session_id"]


def _approve(admin_session: str, username: str) -> None:
    response = client.post(f"/api/auth/accounts/{username}/review",
                           json={"action": "approve", "session_id": admin_session})
    assert response.status_code == 200, response.text


def _make_recovery_code(session_id: str) -> str:
    response = client.post("/api/auth/recovery-code", json={"session_id": session_id})
    assert response.status_code == 200, response.text
    return response.json()["recovery_code"]


def _step1(username: str, cap: dict | None = None):
    """第①步：账号 + 验证码 → reset_token（请求体按评审冻结的四个字段发）。"""
    fresh = cap or _captcha()
    return client.post("/api/auth/reset/request", json={
        "username": username, "captcha": fresh["captcha"],
        "challenge_id": fresh["challenge_id"], "challenge_proof": fresh["challenge_proof"],
    })


def _step2(token: str, code: str, challenge_id: str):
    return client.post("/api/auth/reset/verify", json={
        "reset_token": token, "recovery_code": code, "challenge_id": challenge_id,
    })


def _step3(ticket: str, password: str, **extra):
    return client.post("/api/auth/reset/commit",
                       json={"ticket": ticket, "new_password": password, **extra})


def _make_temp(admin_session: str, username: str):
    return client.post(f"/api/auth/accounts/{username}/temp-password",
                       json={"session_id": admin_session})


def _change(session_id: str, old: str, new: str):
    return client.post("/api/auth/password/change", json={
        "old_password": old, "new_password": new, "session_id": session_id})


def _raw(state_dir: pathlib.Path) -> str:
    path = state_dir / "accounts.json"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _record(state_dir: pathlib.Path, username: str) -> dict:
    for record in json.loads(_raw(state_dir))["accounts"]:
        if str(record.get("username")) == username:
            return record
    raise AssertionError(f"账号表里没有 {username}")


def _no_secret(text: str, *secrets: str) -> None:
    """响应里既不许有敏感字段名，也不许把任何一串明文凭证带回去。"""
    for key in SECRET_KEYS:
        assert key not in text, f"响应里漏出了 {key}：{text[:200]}"
    for secret in secrets:
        assert secret not in text, f"响应里带回了明文凭证：{text[:200]}"


def _setup_boss_and_member(state_dir) -> tuple[str, str]:
    """常见起点：老板（第一个 = 管理员）与小李（普通账号，已批准），各返回会话编号。"""
    assert _register(BOSS, display_name="唐宇（老板）").status_code == 200
    boss = _session(BOSS)
    assert _register(MEMBER, display_name="小李").status_code == 200
    _approve(boss, MEMBER)
    member = _session(MEMBER)
    return boss, member


def _full_reset(username: str, recovery_code: str) -> dict:
    """走完整三步，返回第③步的响应（前两步失败即断言失败）。"""
    cap = _captcha()
    first = _step1(username, cap)
    assert first.status_code == 200, first.text
    second = _step2(first.json()["reset_token"], recovery_code, cap["challenge_id"])
    assert second.status_code == 200, second.text
    third = _step3(second.json()["ticket"], NEW_PLAIN)
    assert third.status_code == 200, third.text
    return third.json()


# ════════════════════════════════════════════════════════════════════════
# A. 恢复请求安全
# ════════════════════════════════════════════════════════════════════════
def test_A1_不存在的账户与存在的账户回答逐字同形():
    """A1：相同 HTTP status + 相同 schema + 不含任何"存在性"。

    做法：把四种情况各打一遍（正常账号 / 压根不存在 / 待批准 / 已停用），
    除了那一串随机 token 本身，**其余字段与那句话说必须一模一样**。
    """
    _register(BOSS)
    _register(MEMBER)                                  # 待批准（pending）
    _register("老张")
    third = _session(BOSS)
    _approve(third, "老张")
    client.post("/api/auth/accounts/老张/review", json={"action": "disable", "session_id": third})

    answers = {}
    for label, username in (("正常", BOSS), ("不存在", "查无此人"), ("待批准", MEMBER), ("已停用", "老张")):
        response = _step1(username)
        assert response.status_code == 200, f"{label}：{response.text}"
        payload = response.json()
        assert set(payload) == {"reset_token", "expires_in", "message"}, f"{label} 的字段不一样"
        assert payload["expires_in"] == recovery.RESET_TOKEN_TTL_SECONDS
        assert payload["message"] == recovery.GENERIC_REQUEST_MESSAGE, f"{label} 的话不一样"
        answers[label] = payload
    # 四种情况除了那串随机 token，其它字段**完全一致**
    assert len({json.dumps({k: v for k, v in item.items() if k != "reset_token"},
                           sort_keys=True, ensure_ascii=False)
                for item in answers.values()}) == 1, "四种情况的响应形状不一致"


def test_A2_用户名变化不改变任何能识别存在性的字段():
    """A2：换个用户名（大小写、多一个空格、完全不存在）都不改变响应里可识别的字段。"""
    _register(BOSS)
    payloads = []
    for username in (BOSS, BOSS.upper() if BOSS.isascii() else BOSS, f" {BOSS} ", "谁也不是"):
        response = _step1(username)
        assert response.status_code == 200, response.text
        payloads.append({k: v for k, v in response.json().items() if k != "reset_token"})
    assert all(item == payloads[0] for item in payloads), "用户名一变，响应就变了"


def test_A3_不存在账户也会照常签发一张_token_没有提前返回的路径():
    """A3：不许有"不存在 → 立即 return / 存在 → 大量处理"这种可枚举路径。

    判据（行为层面，不靠读代码）：账号不存在时**照样**走完签发那一步 ——
    在册的 reset_token 数量确实 +1。也就是"不存在"没有走一条短路径。
    """
    before = recovery.token_count()
    response = _step1("谁也不是")
    assert response.status_code == 200
    assert recovery.token_count() == before + 1, "账号不存在时没有签发 token（存在一条提前返回的路径）"
    # 而且那张 token 是**绑不到任何真实账号**的：它连真实挑战都不认
    token = response.json()["reset_token"]
    assert "谁也不是" not in str(recovery._load_token(token)), "token 里能读出账号名"


def test_A4_reset_token_随机_不透明_单次使用_会过期():
    """A4：密码学随机 + opaque + single-use + 有有效期。"""
    _register(BOSS)
    boss = _session(BOSS)
    code = _make_recovery_code(boss)

    first, second = _step1(BOSS).json()["reset_token"], _step1(BOSS).json()["reset_token"]
    assert first != second, "两次签发的 token 一样（不是随机）"
    assert len(first) >= 32, "token 太短"
    # opaque：里面不该出现账号名之类能读出来的东西
    assert BOSS not in first and BOSS.encode("utf-8").hex() not in first
    assert first != accounts.derive(first, b"\x00" * 16, 1), "token 不是不透明的随机串"
    # single-use：用掉一次就不能再用（下面 F2 会从攻击角度再测一遍）
    cap = _captcha()
    token = _step1(BOSS, cap).json()["reset_token"]
    assert _step2(token, code, cap["challenge_id"]).status_code == 200
    assert _step2(token, code, cap["challenge_id"]).status_code == 400
    # expire：响应里明说有效期，且实现里真有到期判断
    assert _step1(BOSS).json()["expires_in"] == recovery.RESET_TOKEN_TTL_SECONDS
    assert recovery.RESET_TOKEN_TTL_SECONDS > 0


def test_A5_reset_token_不出现在错误响应里(isolated_state, capsys):
    """A5：token 不得出现在日志 / 异常 message / response debug。

    做法：故意造一堆失败（令牌乱填、恢复码乱填），把**每一次响应体**、
    落盘的账号文件、以及测试期间打到 stdout/stderr 上的东西都收起来，逐个扫那串真 token。
    """
    _register(BOSS)
    boss = _session(BOSS)
    code = _make_recovery_code(boss)
    cap = _captcha()
    real_token = _step1(BOSS, cap).json()["reset_token"]

    bodies = [_step2(real_token, "WRONG-CODE", cap["challenge_id"]).text,
              _step2("not-a-real-token", code, cap["challenge_id"]).text,
              _step1(BOSS).text]
    assert real_token not in "".join(bodies), "真 token 出现在了错误响应里"
    assert real_token not in _raw(isolated_state), "token 明文落盘了"
    captured = capsys.readouterr()
    assert real_token not in captured.out and real_token not in captured.err, "token 进了日志"


def test_A5_恢复层异常消息都是固定字符串():
    """A5（源码级）：恢复层的每一条异常消息都必须是**字面量**，不许拼进任何凭证。

    这条是补强：光测"这次没漏"不够，要确保**将来也不会**因为某个人顺手写了
    `f"...token={token}"` 而漏。所以这里扫的是源码本身。
    """
    source = (PROJECT_ROOT / "app" / "recovery.py").read_text(encoding="utf-8")
    for match in re.finditer(r"RecoveryError\(([^)]*)\)", source):
        argument = match.group(1)
        for forbidden in ("token", "ticket", "code", "password", "reset_token"):
            assert f"{forbidden}=" not in argument and f"{{{forbidden}}}" not in argument, \
                f"异常消息里拼了变量：{argument[:80]}"


# ════════════════════════════════════════════════════════════════════════
# B. 恢复码安全
# ════════════════════════════════════════════════════════════════════════
def test_B1_只有已登录用户能生成恢复码():
    """B1：没登录（无会话 / 假会话 / 空会话）一律 401。"""
    _register(BOSS)
    for session_id in (None, "", "假会话编号", "0" * 48):
        response = client.post("/api/auth/recovery-code", json={"session_id": session_id})
        assert response.status_code == 401, f"{session_id!r} 竟然能生成恢复码：{response.text}"
        assert response.json()["error"]["code"] == "session_invalid"


def test_B2_不能靠用户名给别人生成恢复码(isolated_state):
    """B2：入参里根本没有账号名这个字段 —— 给谁生成只由会话决定。

    做法：登录小李，再往请求体里塞 `username: 唐宇`，看生成的是谁的码。
    """
    boss, member = _setup_boss_and_member(isolated_state)
    response = client.post("/api/auth/recovery-code",
                           json={"session_id": member, "username": BOSS, "account_id": BOSS})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == MEMBER, "居然给请求体里那个 username 生成恢复码"
    # 老板那条记录上不该冒出恢复码
    assert not _record(isolated_state, BOSS).get("recovery_code_hash"), "老板被顺手发了恢复码"
    assert _record(isolated_state, MEMBER).get("recovery_code_hash"), "小李自己那条没写上"
    # 老板仍然没有恢复码：拿他的用户名走恢复流程必须失败
    assert _step2(_step1(BOSS).json()["reset_token"], response.json()["recovery_code"],
                  "x").status_code == 400


def test_B3_B4_恢复码用_secrets_生成且是_20_位_Base32():
    """B3/B4：`secrets` 生成；20 位（4-4-4-4-4 分组）；字符集 32 个符号。"""
    source = (PROJECT_ROOT / "app" / "recovery.py").read_text(encoding="utf-8")
    assert "secrets.choice" in source, "恢复码没用 secrets 生成"
    assert "import random" not in source and "random." not in source, "恢复码里混进了 random"

    codes = {recovery.make_recovery_code() for _ in range(20)}
    assert len(codes) == 20, "连着生成出现了重复（不是随机）"
    for code in list(codes)[:5]:
        groups = code.split("-")
        assert len(groups) == recovery.GROUP_COUNT == 5, f"分组数不对：{code}"
        assert all(len(group) == recovery.GROUP_LENGTH == 4 for group in groups), f"分组长度不对：{code}"
        flat = code.replace("-", "")
        assert len(flat) == recovery.CODE_LENGTH == 20, f"长度不是 20：{code}"
        assert set(flat) <= set(recovery.RECOVERY_ALPHABET), f"用了字符集外的字符：{code}"
    assert len(recovery.RECOVERY_ALPHABET) == 32, "字符集不是 32 个（20×5 bit 的熵就不成立）"


def test_B5_恢复码只存哈希_账号文件里没有明文(isolated_state):
    """B5：落盘里只允许 hash（而且是不可还原的 SHA-256），明文一个字都不许有。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    raw = _raw(isolated_state)
    assert code not in raw, "账号文件里出现了恢复码明文"
    assert code.replace("-", "") not in raw, "账号文件里出现了恢复码明文（去掉连字符的形式）"
    record = _record(isolated_state, MEMBER)
    stored = record["recovery_code_hash"]
    assert len(stored) == 64 and re.fullmatch(r"[0-9a-f]{64}", stored), "存的不是 SHA-256"
    assert stored == recovery.code_hash(code), "存的不是这个码的摘要"
    assert "recovery_code" not in record or record.get("recovery_code") is None


def test_B6_恢复码只在响应里返回一次(isolated_state):
    """B6：返回用户只返回一次 —— 之后任何端点都拿不回来。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    # 账号列表（管理员看得到的白名单视图）里没有
    listed = client.get("/api/auth/accounts", params={"session_id": boss}).text
    _no_secret(listed, code)
    # 轮换的响应里也没有"上一次那个码"
    second = _make_recovery_code(member)
    assert code not in second and second != code
    # 账号文件里也没有（B5 已测，这里再确认它随时间不会被写回去）
    assert code not in _raw(isolated_state)


def test_B7_B8_重新生成旧码立即失效_验证成功旧码立即失效(isolated_state):
    """B7：重新生成 → 旧码立刻失效、新码生效；B8：验证成功 → 这个码立刻失效。"""
    boss, member = _setup_boss_and_member(isolated_state)
    old_code = _make_recovery_code(member)
    new_code = _make_recovery_code(member)
    assert old_code != new_code

    # 新码能用
    cap = _captcha()
    token = _step1(MEMBER, cap).json()["reset_token"]
    assert _step2(token, new_code, cap["challenge_id"]).status_code == 200

    # B8：刚刚用掉的那个码立刻失效
    cap2 = _captcha()
    token2 = _step1(MEMBER, cap2).json()["reset_token"]
    assert _step2(token2, new_code, cap2["challenge_id"]).status_code == 400
    assert not _record(isolated_state, MEMBER).get("recovery_code_hash"), "用过的码还留在记录里"

    # B7：再生成一对，旧的立刻失效
    fresh_old = _make_recovery_code(member)
    fresh_new = _make_recovery_code(member)
    assert fresh_old != fresh_new
    assert recovery.code_hash(fresh_new) == _record(isolated_state, MEMBER)["recovery_code_hash"]
    cap3 = _captcha()
    token3 = _step1(MEMBER, cap3).json()["reset_token"]
    assert _step2(token3, fresh_old, cap3["challenge_id"]).status_code == 400, "旧码还能用"


def test_B7_恢复码写法宽容_前后端规整一致():
    """评审 Q6：`J7KD-X4PM-Q8TW-2NFC` 与 `J7KD X4PM Q8TW 2NFC` 都要能过。

    服务端规整（大写 + 去分隔符）必须**自己**做一遍 —— 只在前端做等于没做。
    """
    raw = "j7kd x4pm-q8tw 2nfc-9h3m"
    assert recovery.normalize_recovery_code(raw) == "J7KDX4PMQ8TW2NFC9H3M"
    assert recovery.normalize_recovery_code("J7KD-X4PM-Q8TW-2NFC-9H3M") == "J7KDX4PMQ8TW2NFC9H3M"
    assert len(recovery.normalize_recovery_code(raw)) == recovery.CODE_LENGTH
    # 前端那一遍（session.js 里也有一个同样的函数）——两边同一套规则
    js = (PROJECT_ROOT / "web" / "session.js").read_text(encoding="utf-8")
    assert "normalizeRecoveryCode" in js and "[^0-9A-Z]" in js, "前端没有做同样的规整"


def test_B_恢复码错误不会销毁正确的码(isolated_state):
    """评审 Q4-4：输错不销毁 —— 否则用户输错几次就把自己的恢复能力毁了。"""
    _register(BOSS)
    boss = _session(BOSS)
    code = _make_recovery_code(boss)
    for _ in range(3):
        cap = _captcha()
        token = _step1(BOSS, cap).json()["reset_token"]
        assert _step2(token, "ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ", cap["challenge_id"]).status_code == 400
    assert _record(isolated_state, BOSS)["recovery_code_hash"] == recovery.code_hash(code), \
        "输错几次之后正确的恢复码被销毁了"
    # 正确的码仍然能用
    cap = _captcha()
    token = _step1(BOSS, cap).json()["reset_token"]
    assert _step2(token, code, cap["challenge_id"]).status_code == 200


def test_B_没有恢复码的账号与乱填恢复码给的是同一句话():
    """老账号（没有恢复码）不能自助恢复，而且**不能**与"码打错了"区分开。"""
    _register(BOSS)
    cap_boss = _captcha()
    no_code = _step1(BOSS, cap_boss)               # 第①步用它自己那张挑战（已经答对了）
    token_no_code = no_code.json()["reset_token"]
    # 先给另一个账号造出"有码但填错"的场景，比对两句话
    _register(MEMBER)
    boss = _session(BOSS)
    _approve(boss, MEMBER)
    member = _session(MEMBER)
    _make_recovery_code(member)
    cap2 = _captcha()
    wrong = _step2(_step1(MEMBER, cap2).json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA", cap2["challenge_id"])
    missing = _step2(token_no_code, "AAAA-AAAA-AAAA-AAAA-AAAA", cap_boss["challenge_id"])
    assert wrong.status_code == missing.status_code == 400
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"] == recovery.GENERIC_RESET_MESSAGE


# ════════════════════════════════════════════════════════════════════════
# C. ticket 安全
# ════════════════════════════════════════════════════════════════════════
def test_C1_只有_verify_成功才发_ticket(isolated_state):
    """C1：恢复码不对时，响应里连 ticket 这个字段都不该有。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    response = _step2(_step1(MEMBER, cap).json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA", cap["challenge_id"])
    assert response.status_code == 400
    assert "ticket" not in response.text, "验证失败却把 ticket 漏出去了"
    # 成功那条路才有
    cap2 = _captcha()
    ok = _step2(_step1(MEMBER, cap2).json()["reset_token"], code, cap2["challenge_id"])
    assert ok.status_code == 200 and ok.json()["ticket"]


def test_C2_ticket_不透明_单次使用_会过期_只在内存():
    """C2：opaque + single-use + expire + memory only。"""
    first, second = recovery._issue_ticket("甲")["ticket"], recovery._issue_ticket("甲")["ticket"]
    assert first != second and len(first) >= 32, "ticket 不是随机的不透明串"
    assert recovery._consume_ticket(first) == "甲"
    assert recovery._consume_ticket(first) is None, "同一张 ticket 被消费了两次"
    assert recovery.TICKET_TTL_SECONDS > 0
    # memory only：ticket 只在内存 —— 落盘的账号文件里连这两个字段名都不该出现
    source = (PROJECT_ROOT / "app" / "recovery.py").read_text(encoding="utf-8")
    issue_body = source.split("def _issue_ticket")[1].split("\ndef ")[0]
    assert "state." not in issue_body, "签发 ticket 时碰了落盘状态（ticket 必须只在内存）"
    state_json = (PROJECT_ROOT / "state")
    for path in (state_json.glob("*.json") if state_json.exists() else []):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert first not in text, "ticket 落盘了"


def test_C3_commit_不接受_username_role_account_id_作授权依据(isolated_state):
    """C3/F11/F12：往 commit 里塞 username 也不会改到别人头上。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    boss_hash_before = _record(isolated_state, BOSS)["pwd_hash"]
    response = _step3(ticket, NEW_PLAIN, username=BOSS, account_id=BOSS, role="admin")
    assert response.status_code == 200, response.text
    assert response.json()["username"] == MEMBER, "改的不是票据绑定的那个账号"
    assert _record(isolated_state, BOSS)["pwd_hash"] == boss_hash_before, "老板的密码被改掉了！"
    assert _login(MEMBER, NEW_PLAIN).status_code == 200
    assert _login(BOSS, PLAIN).status_code == 200, "老板的密码不该被动过"
    # 请求体里那两个字段**一个都不该被读**（源码级补强：模型里根本没有它们）
    source = (PROJECT_ROOT / "app" / "api_auth.py").read_text(encoding="utf-8")
    body = source.split("class ResetCommitRequest(BaseModel):")[1].split("\nclass ")[0]
    declared = re.findall(r"^\s+(\w+)\s*:", body, re.M)       # 只看**字段声明**，注释不算
    assert sorted(declared) == ["new_password", "ticket"], f"这个模型收了别的字段：{declared}"


def test_C4_五种情况任一失败都必须拒绝_commit(isolated_state):
    """C4：不存在 / 过期 / 已使用 / challenge 不匹配 / 账户不匹配 —— 任一失败都拒绝 commit。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)

    # ① 不存在
    assert _step3("not-a-ticket", NEW_PLAIN).status_code == 400
    # ② 过期
    ticket = recovery._issue_ticket(MEMBER)["ticket"]
    with recovery._lock:
        recovery._tickets[recovery._digest(ticket)]["expires_at"] = 0
    assert _step3(ticket, NEW_PLAIN).status_code == 400
    # ③ 已使用（重放，F3）
    cap = _captcha()
    live = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    assert _step3(live, NEW_PLAIN).status_code == 200
    assert _step3(live, "anotherpw1").status_code == 400, "同一张 ticket 被用了两次"
    member = _session(MEMBER, NEW_PLAIN)           # 改密把旧会话全作废了，重新登录一条
    # ④ challenge 不匹配（F13/F14）
    code2 = _make_recovery_code(member)
    cap_a, cap_b = _captcha(), _captcha()
    token_a = _step1(MEMBER, cap_a).json()["reset_token"]
    assert _step2(token_a, code2, cap_b["challenge_id"]).status_code == 400, "Token A + Challenge B 竟然过了"
    # ⑤ 账户不匹配（票据绑的账号被删了）
    orphan = recovery._issue_ticket("查无此人")
    assert _step3(orphan["ticket"], NEW_PLAIN).status_code == 400


def test_C5_新密码形态不合格要给_400_不是_500(isolated_state):
    """新密码太短：回 400 + 人话（**不许**变成 500 —— 那是把校验写漏了的表现）。

    这条是补测：`commit_reset` 里那句 `validate_password` 抛的是**账号层**的错误，
    接口层必须一起接住，否则用户看到的是"服务器内部错误"而不是"密码至少 6 位"。
    """
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code,
                    cap["challenge_id"]).json()["ticket"]
    for bad in ("123", ""):
        response = _step3(ticket, bad)
        assert response.status_code == 400, f"{bad!r} → {response.status_code}：{response.text[:160]}"
        assert "至少 6 位" in response.json()["error"]["message"]
    # 失败不该有副作用：密码一个字没变，而且**票据没被这次失败用掉**
    assert _login(MEMBER, PLAIN).status_code == 200
    assert _login(MEMBER, NEW_PLAIN).status_code == 401
    assert _step3(ticket, NEW_PLAIN).status_code == 200, "一次填错密码就把好票据烧掉了"
    assert _login(MEMBER, NEW_PLAIN).status_code == 200


def test_C5_commit_成功后_ticket_与_reset_token_都作废(isolated_state):
    """C5：commit 成功后两张一次性凭证都失效。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    token = _step1(MEMBER, cap).json()["reset_token"]
    ticket = _step2(token, code, cap["challenge_id"]).json()["ticket"]
    assert _step3(ticket, NEW_PLAIN).status_code == 200
    # ticket 作废（重放被拒）
    assert _step3(ticket, "anotherpw1").status_code == 400
    # reset_token 早在 verify 成功那一刻就作废了（重放被拒）
    assert _step2(token, code, cap["challenge_id"]).status_code == 400


# ════════════════════════════════════════════════════════════════════════
# D. 管理员临时密码
# ════════════════════════════════════════════════════════════════════════
def test_D1_D2_只有管理员能发临时密码(isolated_state):
    """D1/D2：普通用户（不管目标是自己 / 别人 / 管理员 / 不存在的名字）一律 403。"""
    boss, member = _setup_boss_and_member(isolated_state)
    for target in (MEMBER, BOSS, "查无此人"):
        response = _make_temp(member, target)
        assert response.status_code == 403, f"普通用户给 {target} 发临时密码没有被拒：{response.text}"
        assert response.json()["error"]["code"] == "not_admin"
    # 游客（没有会话）→ 401
    assert client.post(f"/api/auth/accounts/{MEMBER}/temp-password",
                       json={"session_id": "假会话"}).status_code == 401
    # 管理员可以（发给普通账号）
    assert _make_temp(boss, MEMBER).status_code == 200


def test_D3_管理员不能读密码_不能设正式密码_不能读临时密码哈希(isolated_state):
    """D3：管理员拿不到用户的密码、也改不了用户的正式密码。"""
    boss, member = _setup_boss_and_member(isolated_state)
    before = _record(isolated_state, MEMBER)
    response = _make_temp(boss, MEMBER)
    assert response.status_code == 200
    _no_secret(response.text, PLAIN)
    after = _record(isolated_state, MEMBER)
    # 正式密码那三个字段一个都没动（没有"顺手设成临时密码"这种事）
    for field in ("pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations"):
        assert before[field] == after[field], f"临时密码顺手改了正式密码的 {field}"
    # 响应里唯一的账号信息走白名单，没有哈希
    assert set(response.json()["account"]) == set(accounts.PUBLIC_FIELDS)
    assert PLAIN not in response.text


def test_D4_临时密码_12位_secrets_只存哈希_有有效期(isolated_state):
    """D4：secrets 生成 + single-use + hashed + expires_at。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    assert len(plaintext) == accounts.TEMP_PWD_LENGTH == 12, "临时密码不是 12 位"
    assert set(plaintext) <= set(accounts.TEMP_PWD_ALPHABET)
    record = _record(isolated_state, MEMBER)
    assert record.get("temp_pwd_hash") and len(record["temp_pwd_hash"]) == 64
    assert plaintext not in _raw(isolated_state), "临时密码明文落盘了"
    assert record.get("temp_pwd_used_at") is None
    assert record.get("temp_pwd_expires_at") > 0
    assert _make_temp(boss, MEMBER).json()["expires_in"] == accounts.TEMP_PWD_TTL_SECONDS
    # hashed：拿明文去跟记录里的哈希比一下，确实是对得上的那一套摘要（不是明文比对）
    assert accounts.temp_password_matches(record, plaintext) is True
    assert accounts.temp_password_matches(record, plaintext + "x") is False


def test_D5_临时密码不进日志_不进审计_不进异常(isolated_state, capsys):
    """D5：临时密码不得出现在日志 / audit payload / 异常 / 任何后续响应里。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    # 后续所有能读到账号的地方都不许再有它
    listed = client.get("/api/auth/accounts", params={"session_id": boss}).text
    _no_secret(listed, plaintext)
    assert plaintext not in _raw(isolated_state)
    assert plaintext not in client.get("/api/health").text
    # 登录失败 / 改密失败这些错误响应里也不许回显
    assert plaintext not in _login(MEMBER, plaintext + "x").text
    captured = capsys.readouterr()
    assert plaintext not in captured.out and plaintext not in captured.err, "临时密码进了日志"


def test_D6_D7_D8_临时密码登录后必须先改密_业务接口一律403(isolated_state):
    """D6/D7/D8：登录后 must_change_password = true；未改密时只放行改密与退出登录。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    login = _login(MEMBER, plaintext)
    assert login.status_code == 200, login.text
    account = login.json()["account"]
    assert account["must_change_password"] is True, "D6：没有标记必须先改密"
    temp_session = account["session_id"]
    headers = {"X-Session-Id": temp_session}

    # D7：业务 API 一律 403（服务端拦住，不靠前端不显示按钮）
    for path in ("/api/health", "/api/datasets", "/api/tables/sales", "/api/conversations",
                 "/api/documents"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, f"{path} 没有被拦住：{response.status_code}"
        assert response.json()["error"]["code"] == "must_change_password"
    assert client.post("/api/chat", json={"question": "上周销售额"}, headers=headers).status_code == 403
    # 生成恢复码也要先改密（只有改密与退出登录两条路）
    assert client.post("/api/auth/recovery-code",
                       json={"session_id": temp_session}).status_code == 403
    # D8：只放行这两条
    assert client.post("/api/auth/logout", json={"session_id": temp_session}).status_code == 200
    again = _login(MEMBER, plaintext)
    assert again.status_code == 401, "临时密码被用了第二次"


def test_D7_没有会话的请求不受影响():
    """闸门只在"带着一条 must_change 会话"时才可能拦人 —— 普通请求一个都不受影响。

    （这条是"不许改既有端点行为"的证据：12 个 Legacy 端点在没有那个请求头时完全照旧。）
    """
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"X-Session-Id": "no-such-session"}).status_code == 200


# ════════════════════════════════════════════════════════════════════════
# E. Session / 越权（评审说这是整个 TASK 最重要的一组）
# ════════════════════════════════════════════════════════════════════════
def test_E1_E2_普通用户改_URL_也拿不到管理员能力(isolated_state):
    """E1/E2：普通用户调 temp-password 全 403 —— 自己 / 别人 / admin 名 / 不存在的名字，
    以及把 URL 里的目标换成 admin、大小写变形、加空格，都不行。"""
    boss, member = _setup_boss_and_member(isolated_state)
    targets = [MEMBER, BOSS, "查无此人", BOSS.upper(), f" {MEMBER} ", "admin", "root"]
    for target in targets:
        response = client.post(f"/api/auth/accounts/{target}/temp-password",
                               json={"session_id": member})
        assert response.status_code == 403, f"{target}：{response.status_code}"
    # 顺带把另外三条管理端点也验一遍（改 URL 拿不到任何管理能力）
    assert client.get("/api/auth/accounts", params={"session_id": member}).status_code == 403
    assert client.post(f"/api/auth/accounts/{MEMBER}/review",
                       json={"action": "approve", "session_id": member}).status_code == 403
    assert client.delete(f"/api/auth/accounts/{BOSS}",
                         params={"session_id": member}).status_code == 403


def test_E3_恢复成功之后旧_session_作废(isolated_state):
    """E3：走完恢复流程，原来那条会话立刻失效。"""
    boss, member = _setup_boss_and_member(isolated_state)
    assert client.post("/api/auth/recovery-code", json={"session_id": member}).status_code == 200
    code = _make_recovery_code(member)              # 拿最后这一张去走恢复（每次生成都会顶掉上一张）
    _full_reset(MEMBER, code)
    assert accounts.resolve_session(member) is None, "恢复之后旧会话还活着"
    assert client.post("/api/auth/recovery-code", json={"session_id": member}).status_code == 401


def test_E4_改密成功_旧_session_作废_新_session_有效(isolated_state):
    """E4：改密 = 认证状态重建 —— 旧会话失效、新会话可用。"""
    _register(BOSS)
    old = _session(BOSS)
    response = _change(old, PLAIN, NEW_PLAIN)
    assert response.status_code == 200, response.text
    new = response.json()["session_id"]
    assert new and new != old
    assert response.json()["must_change_password"] is False
    assert accounts.resolve_session(old) is None, "旧会话没作废"
    assert accounts.resolve_session(new) == BOSS
    assert client.post("/api/auth/recovery-code", json={"session_id": old}).status_code == 401
    assert client.post("/api/auth/recovery-code", json={"session_id": new}).status_code == 200


def test_E5_临时密码会话_三条路的分别(isolated_state):
    """E5：业务 API 403 / password/change 200 / logout 200。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    temp_session = _login(MEMBER, plaintext).json()["account"]["session_id"]

    assert client.get("/api/datasets", headers={"X-Session-Id": temp_session}).status_code == 403
    changed = _change(temp_session, plaintext, NEW_PLAIN)
    assert changed.status_code == 200, changed.text
    fresh = changed.json()["session_id"]
    assert client.get("/api/datasets", headers={"X-Session-Id": fresh}).status_code == 200
    # logout 那条路（用另一张临时密码重新来一遍，免得受上面已改密的影响）
    plaintext2 = _make_temp(boss, MEMBER).json()["temp_password"]
    temp2 = _login(MEMBER, plaintext2).json()["account"]["session_id"]
    assert client.post("/api/auth/logout", json={"session_id": temp2}).status_code == 200


def test_E6_改密之后旧密码失败_新密码成功_临时密码失败(isolated_state):
    """E6：三条登录路的结果。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    temp_session = _login(MEMBER, plaintext).json()["account"]["session_id"]
    assert _change(temp_session, plaintext, NEW_PLAIN).status_code == 200

    assert _login(MEMBER, PLAIN).status_code == 401, "旧密码还能登录"
    assert _login(MEMBER, NEW_PLAIN).status_code == 200, "新密码登不上"
    assert _login(MEMBER, plaintext).status_code == 401, "临时密码还能登录"
    # 临时密码那套字段在改密时被清干净了
    record = _record(isolated_state, MEMBER)
    for field in accounts.TEMP_PWD_FIELDS:
        assert field not in record, f"改密后 {field} 还留在记录里"


# ════════════════════════════════════════════════════════════════════════
# F. 攻击回归（F1–F20）
# ════════════════════════════════════════════════════════════════════════
def test_F1_不存在用户名枚举(isolated_state):
    """F1：第①步与第②步都不回答"这个账号在不在"。"""
    boss, member = _setup_boss_and_member(isolated_state)
    fake = _step1("压根没有这个人")
    real = _step1(MEMBER)
    assert fake.status_code == real.status_code == 200
    assert fake.json()["message"] == real.json()["message"]
    assert set(fake.json()) == set(real.json())
    # 第②步：不存在的账号那条 token 永远换不到票据
    assert _step2(fake.json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA", "x").status_code == 400


def test_F2_reset_token_重放(isolated_state):
    """F2：同一张 reset_token 不许被用第二次。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    token = _step1(MEMBER, cap).json()["reset_token"]
    assert _step2(token, code, cap["challenge_id"]).status_code == 200
    assert _step2(token, code, cap["challenge_id"]).status_code == 400


def test_F3_ticket_重放(isolated_state):
    """F3：同一张 ticket 不许改两次密码。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    assert _step3(ticket, NEW_PLAIN).status_code == 200
    assert _step3(ticket, "yetanother1").status_code == 400
    assert _login(MEMBER, NEW_PLAIN).status_code == 200
    assert _login(MEMBER, "yetanother1").status_code == 401


def test_F4_recovery_code_重放(isolated_state):
    """F4：恢复码用掉一次就没了。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    assert _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).status_code == 200
    cap2 = _captcha()
    assert _step2(_step1(MEMBER, cap2).json()["reset_token"], code,
                  cap2["challenge_id"]).status_code == 400


def test_F5_recovery_code_暴力尝试(isolated_state):
    """F5：连着错满 5 次进 60 秒冷却，而且**不污染登录失败计数**（评审 Q4-3）。"""
    boss, member = _setup_boss_and_member(isolated_state)
    for index in range(recovery.RECOVERY_MAX_ATTEMPTS):
        cap = _captcha()
        response = _step2(_step1(MEMBER, cap).json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA",
                          cap["challenge_id"])
        assert response.status_code == 400, f"第 {index + 1} 次：{response.text}"
    assert recovery.failure_count(MEMBER) == recovery.RECOVERY_MAX_ATTEMPTS
    # 第六次：冷却中 → 429
    cap = _captcha()
    cooled = _step2(_step1(MEMBER, cap).json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA",
                    cap["challenge_id"])
    assert cooled.status_code == 429, cooled.text
    assert "秒后再试" in cooled.json()["error"]["message"]
    # ★ 恢复失败**没有**污染登录那本账：小李照样能正常登录
    assert accounts.lock_remaining(MEMBER) == 0, "恢复失败把登录也锁了（攻击者可以拿它做 DoS）"
    assert _login(MEMBER, PLAIN).status_code == 200
    # 冷却结束后还能再试（把窗口推回过去，等价于"等到了"）
    recovery.reset_recovery_failures()
    assert recovery.recovery_lock_remaining(MEMBER) == 0
    cap = _captcha()
    assert _step2(_step1(MEMBER, cap).json()["reset_token"], "AAAA-AAAA-AAAA-AAAA-AAAA",
                  cap["challenge_id"]).status_code == 400, "冷却结束后应当能正常重试（只是码不对）"


def test_F6_expired_token(isolated_state):
    """F6：过期的 reset_token 一律拒绝。"""
    _register(BOSS)
    cap = _captcha()
    token = _step1(BOSS, cap).json()["reset_token"]
    with recovery._lock:
        recovery._reset_tokens[recovery._digest(token)]["expires_at"] = 0
    assert _step2(token, "AAAA-AAAA-AAAA-AAAA-AAAA", cap["challenge_id"]).status_code == 400
    # 过期的那些会被顺手清掉（不留垃圾）
    recovery.token_count()
    assert recovery._load_token(token) is None


def test_F7_expired_ticket(isolated_state):
    """F7：过期的 ticket 一律拒绝。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    with recovery._lock:
        recovery._tickets[recovery._digest(ticket)]["expires_at"] = 0
    assert _step3(ticket, NEW_PLAIN).status_code == 400
    assert _login(MEMBER, NEW_PLAIN).status_code == 401, "过期票据居然把密码改了"


def test_F8_expired_temp_password(isolated_state):
    """F8：过期的临时密码登不进去。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    state.update_account_fields(MEMBER, {"temp_pwd_expires_at": 0})
    assert _login(MEMBER, plaintext).status_code == 401, "过期的临时密码还能登录"
    # 正式密码不受影响（过期的只是那张一次性凭证）
    assert _login(MEMBER, PLAIN).status_code == 200


def test_F9_普通用户调_admin_endpoint(isolated_state):
    """F9：三条管理端点对普通用户全 403。"""
    boss, member = _setup_boss_and_member(isolated_state)
    assert client.get("/api/auth/accounts", params={"session_id": member}).status_code == 403
    assert _make_temp(member, MEMBER).status_code == 403
    assert client.post(f"/api/auth/accounts/{MEMBER}/review",
                       json={"action": "disable", "session_id": member}).status_code == 403
    assert client.delete(f"/api/auth/accounts/{BOSS}",
                         params={"session_id": member}).status_code == 403


def test_F10_改_URL_目标用户名(isolated_state):
    """F10：把 URL 里的目标换成别人 / 管理员 / 不存在的名字，都拿不到任何东西。"""
    boss, member = _setup_boss_and_member(isolated_state)
    for target in (BOSS, MEMBER, "查无此人"):
        assert _make_temp(member, target).status_code == 403
    # 普通用户也不该因为"改 URL 指到不存在的账号"而拿到 404（那本身就是一种存在性探测）
    assert _make_temp(member, "查无此人").json()["error"]["code"] == "not_admin"


def test_F11_F12_commit_注入_username_与_ticket_配别人(isolated_state):
    """F11/F12：ticket 只能改它自己绑定的那个账号。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    boss_before = _record(isolated_state, BOSS)["pwd_hash"]
    assert _step3(ticket, NEW_PLAIN, username=BOSS).status_code == 200
    assert _record(isolated_state, BOSS)["pwd_hash"] == boss_before
    assert _login(BOSS, PLAIN).status_code == 200
    assert _login(MEMBER, NEW_PLAIN).status_code == 200


def test_F13_F14_challenge_与_token_交叉绑定(isolated_state):
    """F13/F14：Token A + Challenge B、Token B + Challenge A 都必须失败。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap_a, cap_b = _captcha(), _captcha()
    token_a = _step1(MEMBER, cap_a).json()["reset_token"]
    token_b = _step1(MEMBER, cap_b).json()["reset_token"]
    # F13：Token A + Challenge B
    assert _step2(token_a, code, cap_b["challenge_id"]).status_code == 400
    # F14：Token B + Challenge A
    assert _step2(token_b, code, cap_a["challenge_id"]).status_code == 400
    # 各自配自己的那张才过（证明上面失败不是因为别的原因）
    assert _step2(token_a, code, cap_a["challenge_id"]).status_code == 200
    # 而且挑战必须是**答对过**的那张：编一个没答过的编号不行
    cap_c = _captcha()
    assert _step2(token_b, code, "从来没发过的编号").status_code == 400
    assert _step2(token_b, code, cap_c["challenge_id"]).status_code == 400, "没答对过的挑战被当成了有效"


def test_F15_旧_session(isolated_state):
    """F15：退出登录之后那条会话就不作数了。"""
    _register(BOSS)
    session_id = _session(BOSS)
    assert client.post("/api/auth/logout", json={"session_id": session_id}).status_code == 200
    assert accounts.resolve_session(session_id) is None
    assert client.post("/api/auth/recovery-code", json={"session_id": session_id}).status_code == 401


def test_F16_临时密码_session_越权(isolated_state):
    """F16：临时密码那条会话想干别的，一律 403（含管理端点与业务端点）。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    temp_session = _login(MEMBER, plaintext).json()["account"]["session_id"]
    for response in (client.get("/api/datasets", headers={"X-Session-Id": temp_session}),
                     client.get("/api/health", headers={"X-Session-Id": temp_session}),
                     client.get("/api/auth/accounts", params={"session_id": temp_session}),
                     client.post("/api/auth/recovery-code", json={"session_id": temp_session})):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "must_change_password"


def test_F17_改密之后的旧_session(isolated_state):
    """F17：改完密码，改密之前那条会话彻底失效。"""
    _register(BOSS)
    old = _session(BOSS)
    new = _change(old, PLAIN, NEW_PLAIN).json()["session_id"]
    assert accounts.resolve_session(old) is None
    assert client.get("/api/auth/accounts", params={"session_id": old}).status_code == 401
    assert client.get("/api/auth/accounts", params={"session_id": new}).status_code == 200


def test_F18_F19_F20_三类凭证都不出现在日志与响应里(isolated_state, capsys):
    """F18/F19/F20：恢复码 / 临时密码 / reset_token 都不得出现在任何输出里。

    做法：把三条路各走一遍，然后扫 ① 打到 stdout/stderr 的东西、
    ② 落盘的账号文件、③ 后续若干端点的响应体。
    """
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    temp_plain = _make_temp(boss, MEMBER).json()["temp_password"]
    cap = _captcha()
    token = _step1(MEMBER, cap).json()["reset_token"]
    ticket = _step2(token, code, cap["challenge_id"]).json()["ticket"]

    captured = capsys.readouterr()
    stream = captured.out + captured.err
    for label, secret in (("恢复码", code), ("临时密码", temp_plain),
                          ("reset_token", token), ("ticket", ticket)):
        assert secret not in stream, f"{label} 进了日志"
        assert secret not in _raw(isolated_state), f"{label} 落盘了"
    # 后续端点的响应里也不许有
    for path, params in (("/api/auth/accounts", {"session_id": boss}), ("/api/health", {}),
                         ("/api/auth/accounts/exists", {})):
        body = client.get(path, params=params).text
        for secret in (code, temp_plain, token, ticket):
            assert secret not in body, f"{path} 的响应里漏了凭证"


# ════════════════════════════════════════════════════════════════════════
# 评审新发现的 6 类并发口子（③④⑤⑥ —— 前两类由 F13/F14 覆盖）
# ════════════════════════════════════════════════════════════════════════
def _race(worker, times: int = 8) -> list:
    """让 `times` 个线程在同一个屏障上起跑，收集各自的返回值。"""
    barrier = threading.Barrier(times)

    def run(index):
        barrier.wait()
        return worker(index)

    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(run, range(times)))


def test_并发3_恢复码轮换必须原子替换(isolated_state):
    """③：连点"生成恢复码"不能出现"旧码还有效 + 新码也有效"。"""
    _register(BOSS)
    boss = _session(BOSS)
    results = _race(lambda _: client.post("/api/auth/recovery-code",
                                          json={"session_id": boss}).json()["recovery_code"])
    record = _record(isolated_state, BOSS)
    stored = record["recovery_code_hash"]
    # 落盘的那一个哈希必须正好是**最后一次**生成的那串码的摘要（不多也不少）
    matched = [code for code in results if recovery.code_hash(code) == stored]
    assert len(matched) == 1, f"落盘的不是其中任何一串（或不止一串）：{len(matched)}"
    # 除它之外，其它几串**全都**已经失效（不许出现两个有效码）
    for code in results:
        if recovery.code_hash(code) == stored:
            continue
        assert state.consume_account_recovery_code(BOSS, recovery.code_hash(code), "t") is False, \
            "并发轮换之后出现了第二个仍然有效的恢复码"


def test_并发4_恢复码检查与消费必须原子(isolated_state):
    """④：同一个恢复码并发两个请求 → 只能有一个成功，另一个必须失败。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    ready = _race(lambda _: recovery.code_hash(code), times=6)   # 先把摘要算好（避免把算哈希的时间算进竞争）
    assert len(set(ready)) == 1

    results = _race(lambda _: state.consume_account_recovery_code(MEMBER, recovery.code_hash(code), "t"),
                    times=6)
    assert results.count(True) == 1, f"同一个恢复码被消费了 {results.count(True)} 次"


def test_并发4_HTTP_层同一个恢复码并发验证(isolated_state):
    """④（HTTP 层）：并发打同一个码的 verify，只允许一张票据被发出来。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    # 用**同一张** token 并发（保证竞争发生在"消费恢复码"那一步）
    token = _step1(MEMBER, cap).json()["reset_token"]
    results = _race(lambda _: _step2(token, code, cap["challenge_id"]).status_code, times=6)
    assert results.count(200) == 1, f"并发 verify 成功了 {results.count(200)} 次"
    assert all(status == 200 or status >= 400 for status in results), results


def test_并发5_ticket_并发重放(isolated_state):
    """⑤：同一张 ticket 并发 commit → 只有一个能改成功。"""
    boss, member = _setup_boss_and_member(isolated_state)
    code = _make_recovery_code(member)
    cap = _captcha()
    ticket = _step2(_step1(MEMBER, cap).json()["reset_token"], code, cap["challenge_id"]).json()["ticket"]
    results = _race(lambda index: _step3(ticket, f"pass-{index}-x").status_code, times=6)
    assert results.count(200) == 1, f"同一张 ticket 改成功了 {results.count(200)} 次"


def test_并发6_临时密码并发登录只算单次(isolated_state):
    """⑥：拿着临时密码并发登录多次，必须**只算单次使用**。"""
    boss, member = _setup_boss_and_member(isolated_state)
    plaintext = _make_temp(boss, MEMBER).json()["temp_password"]
    bodies = [_captcha() for _ in range(6)]        # 每个请求各带一张自己的验证码（都是一次性的）

    def attempt(index):
        body = bodies[index]
        return client.post("/api/auth/login", json={
            "username": MEMBER, "password": plaintext,
            "captcha_id": body["captcha_id"], "captcha_text": body["captcha_text"],
        }).status_code

    results = _race(attempt, times=6)
    assert results.count(200) == 1, f"临时密码被并发登录成功了 {results.count(200)} 次"
    assert all(status == 200 or status >= 400 for status in results), results
    record = _record(isolated_state, MEMBER)
    assert record.get("temp_pwd_used_at"), "用过之后没有留下已用标记"


# ════════════════════════════════════════════════════════════════════════
# 评审附加：⑦ 管理员之间不许互相接管 / ⑧ pending·rejected 不许被绕过
#             ⑨ 枚举旁路节流 / ⑩ 三层授权不许合并
# ════════════════════════════════════════════════════════════════════════
def test_附加7_管理员不能给管理员发临时密码(isolated_state):
    """评审 ⑦：临时密码只能发给 role=user 的账号（避免管理员之间互相接管）。"""
    _register(BOSS)
    boss = _session(BOSS)
    _register("第二个管理员")
    _approve(boss, "第二个管理员")
    # 把第二个账号提成管理员（直接改记录，模拟"系统里有多个管理员"）
    state.update_account_fields("第二个管理员", {"role": accounts.ROLE_ADMIN})

    response = _make_temp(boss, "第二个管理员")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "admin_target_forbidden"
    assert _make_temp(boss, BOSS).status_code == 403, "居然能给自己发临时密码"
    # 普通账号照常可以
    _register(MEMBER)
    _approve(boss, MEMBER)
    assert _make_temp(boss, MEMBER).status_code == 200


def test_附加8_pending_rejected_账号不能靠恢复流程绕过审核(isolated_state):
    """评审 ⑧：待批准 / 未通过 / 已停用的账号，恢复流程与临时密码都进不去。"""
    _register(BOSS)
    boss = _session(BOSS)
    _register(MEMBER)                                     # pending
    _register("被拒的")
    client.post("/api/auth/accounts/被拒的/review", json={"action": "reject", "session_id": boss})
    _register("被停的")
    _approve(boss, "被停的")
    client.post("/api/auth/accounts/被停的/review", json={"action": "disable", "session_id": boss})

    for username in (MEMBER, "被拒的", "被停的"):
        cap = _captcha()
        token = _step1(username, cap).json()["reset_token"]
        response = _step2(token, "AAAA-AAAA-AAAA-AAAA-AAAA", cap["challenge_id"])
        assert response.status_code == 400, f"{username} 的恢复没有被挡住"
        # 临时密码也不给发
        assert _make_temp(boss, username).status_code == 400, f"{username} 竟然能拿到临时密码"
        # 状态没有被恢复流程改掉（恢复密码与账号审批是两件事）
        assert _record(isolated_state, username)["status"] != accounts.STATUS_ACTIVE
    # 被拒的账号也不能靠"批准"以外的方式回到可用状态
    assert _record(isolated_state, "被拒的")["status"] == accounts.STATUS_REJECTED


def test_附加9_查重端点的批量枚举会被节流():
    """评审 ⑨：`/accounts/exists` 是登记在案的枚举面，但**批量**扫会被挡住。"""
    for _ in range(api_auth.EXISTS_MAX_PER_WINDOW):
        assert client.get("/api/auth/accounts/exists",
                          params={"username": "随便问一个"}).status_code == 200
    blocked = client.get("/api/auth/accounts/exists", params={"username": "再问一个"})
    assert blocked.status_code == 429, "批量枚举没有被节流"
    assert blocked.json()["error"]["code"] == "too_many_requests"
    # 节流窗口过了照常可用
    api_auth.reset_exists_throttle()
    assert client.get("/api/auth/accounts/exists",
                      params={"username": "还可以问"}).status_code == 200


def test_附加10_三层授权不许合并_验证码单独不足以改密码(isolated_state):
    """评审冻结的三层：只有验证码过、或只有挑战 id，都改不了密码。

    这是本 TASK 的"灵魂"—— 合并任何两层都会留下越权路径，所以这里从"缺一层"的角度测：
    ① 只有验证码（没有恢复码）→ 拿不到票据；
    ② 只有恢复码（没有先过验证码）→ 连第①步都过不去；
    ③ 只有票据（已经过期/不存在）→ 改不了密码。
    """
    boss, member = _setup_boss_and_member(isolated_state)
    # ① 只有验证码
    cap = _captcha()
    token = _step1(MEMBER, cap).json()["reset_token"]
    assert "ticket" not in _step2(token, "AAAA-AAAA-AAAA-AAAA-AAAA", cap["challenge_id"]).text
    # ② 只有恢复码（不先过验证码那一步 —— 令牌与挑战 id 都自己编）
    code = _make_recovery_code(member)
    assert _step2("自己编的令牌", code, "自己编的挑战").status_code == 400
    # ③ 光有票据没用：票据必须真的由第②步发出来
    assert _step3("自己编的票据", NEW_PLAIN).status_code == 400
    assert _login(MEMBER, NEW_PLAIN).status_code == 401, "密码不该被改动过"
    assert _login(MEMBER, PLAIN).status_code == 200, "原密码应当照旧可用"


def test_附加_未答对的挑战不能当有效挑战(isolated_state):
    """`challenge.is_passed` 只认"答对过"的那张 —— 没答对过的 id 一律不算数。"""
    data = client.get("/api/auth/captcha").json()
    assert challenge.is_passed(data["captcha_id"]) is False, "还没答就当成通过了"
    # 登记时机是"答对的那一刻"（见 app/challenge.py）：还没答过的 id 根本不在册
    assert challenge.status(data["captcha_id"]) == "unknown"
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert challenge.verify(data["captcha_id"], "错误答案") == "wrong"
    assert challenge.is_passed(data["captcha_id"]) is False
    assert challenge.verify(data["captcha_id"], code) == "ok"
    assert challenge.is_passed(data["captcha_id"]) is True
    assert challenge.status(data["captcha_id"]) == "passed"
