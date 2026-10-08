"""tests/test_fr002a_local_mode.py · FR-002A 本机单用户模式（注册即生效）+ 启动迁移。

════════════════════════════════════════════════════════════════════════
【这个文件要证明的一件事】
════════════════════════════════════════════════════════════════════════
用户的原话：
    「我这边是管理员，**你不要把我的账号以及密码，在正确的情况下或者注册的情况后，
      让我还要去通过审批**。我找谁审批？**我自己弄的，我还要去找别人审批啊**」

真实死锁（用户实测复现）：库里已有一个 active 管理员（早期测试账号，密码用户不知道），
用户在实例上注册的账号于是变成 `pending` —— **没有任何人能批准它**，用户被自己锁在门外。

所以本文件盯三件事：

  ① **默认（不设任何环境变量）= 本机单用户模式**：注册即生效 ——
     第一个账号 active+admin，后续账号 active+user，**谁都不用批**，而且当场能登录；
  ② **真实审批流模式还在**（`SRA_REQUIRE_APPROVAL=1`）：注册仍落 pending，
     审批流（管理员批准 → 能登录）一行没坏；
  ③ **启动迁移**把卡住的库解开：有 pending、没人能批 → 服务起来之后用户能登录、
     **不删库、不改密码、不产生第二个 admin**，而且**幂等**（跑两次结果一致）。

测试都走**真 HTTP**（TestClient 进程内）与**真落盘**（临时 state 目录），
③ 里那条死锁回归还走**真的启动路径**（TestClient 的 lifespan，而不是直接调函数）——
否则"启动时迁移"这件事根本没被验过。预热数据集与本 TASK 无关，测试里把它换成空实现
（`prewarm.lifespan`），只留迁移那一件事。
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, api, captcha, state  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

PLAIN = "Sup3r-秘密-2026"
BOSS = "老板"
MEMBER = "小李"

# 死锁现场的两个账号：都是 pending，谁都不许登录 —— 而这个库里没有任何可用管理员
DEADLOCK_ADMIN = "admin"
DEADLOCK_MEMBER = "同事"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """账号表落临时目录，并**清掉模式开关**（默认模式就是本 TASK 要验的东西之一）。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv(accounts.ENV_REQUIRE_APPROVAL, raising=False)
    captcha.reset()
    accounts.reset_failures()
    yield tmp_path / "state"
    captcha.reset()
    accounts.reset_failures()


# ── 打真 HTTP 的两个小工具（验证码照用户那条路走，不开后门）────────────────
def _captcha() -> dict:
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert len(code) == captcha.LENGTH
    return {"captcha_id": data["captcha_id"], "captcha_text": code}


def _register(username: str, password: str = PLAIN) -> object:
    return client.post("/api/auth/register",
                       json={"username": username, "password": password, **_captcha()})


def _login(username: str, password: str = PLAIN) -> object:
    return client.post("/api/auth/login",
                       json={"username": username, "password": password, **_captcha()})


def _account_of(username: str, path: pathlib.Path) -> dict:
    """从**落盘的账号文件**里读出这条记录（不看内存、不看响应）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["accounts"]:
        if record["username"] == username:
            return record
    raise AssertionError(f"账号文件里没有 {username}：{[r['username'] for r in data['accounts']]}")


def _seed_accounts(path: pathlib.Path, rows: list[tuple[str, str, str, str]]) -> None:
    """直接造一份账号文件（模拟"历史上就卡在那儿"的库）。

    rows 的每一项是 `(账号名, status, role, created_at)`；密码统一是 PLAIN，
    用真的 `accounts.make_secret` 算 —— 这样"迁移前后原密码仍能登录"才是真验过的。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for username, status, role, created_at in rows:
        records.append({
            "username": username, "display_name": username, "status": status, "role": role,
            "created_at": created_at, "last_login_at": None,
            "reviewed_at": None, "reviewed_by": None,
            **accounts.make_secret(PLAIN),
        })
    # 账号文件"新的在前" —— 与仓库行为一致（传进来的顺序当作新的在前）
    path.write_text(json.dumps({"schema_version": 1, "accounts": records}, ensure_ascii=False),
                    encoding="utf-8")


@contextlib.asynccontextmanager
async def _no_prewarm(_app):
    """替身：只把 prewarm 那段空掉（预热是另一个 TASK 的事，与本文件无关）。"""
    yield


def _boot_app(monkeypatch) -> None:
    """走**真的启动路径**（FastAPI lifespan）跑一次，然后立刻关掉。

    为什么不是"直接调 accounts.migrate_accounts()"：那样只能证明那个函数自己没问题，
    证明不了"服务启动时会调它"。这里进一次 TestClient 的上下文就是走了一遍启动。
    """
    monkeypatch.setattr(api.prewarm, "lifespan", _no_prewarm)
    with TestClient(app):
        pass


# ════════════════════════════════════════════════════════════════════════
# ① 默认模式 = 本机单用户：注册即生效（不需要任何人批准）
# ════════════════════════════════════════════════════════════════════════
def test_A1_不设环境变量就是本机单用户模式():
    """A1：默认（一个变量都不设）= 本机单用户 —— 注册不需要审批。"""
    assert accounts.require_approval() is False, "默认必须是本机单用户模式"


def test_A2_第一个注册的账号是管理员且当场能登录():
    """A2：第一个账号 active + admin（否则没人能批账号，系统死锁）。"""
    account = _register(BOSS).json()["account"]
    assert (account["status"], account["role"]) == (accounts.STATUS_ACTIVE, accounts.ROLE_ADMIN)
    assert account["reviewed_by"], "自动成为管理员这件事要留出处（审计）"
    assert _login(BOSS).status_code == 200


def test_A3_后续账号注册即生效_不需要任何人批准():
    """A3：第二个账号 active + user —— **没有任何人按过"批准"**。"""
    _register(BOSS)
    account = _register(MEMBER).json()["account"]
    assert (account["status"], account["role"]) == (accounts.STATUS_ACTIVE, accounts.ROLE_USER)
    answer = _login(MEMBER)
    assert answer.status_code == 200, f"注册即生效模式下必须当场能登录：{answer.text}"
    assert answer.json()["account"]["username"] == MEMBER


def test_注册即生效也留下出处(isolated_state):
    """免审批不等于"来路不明"：记录里写清楚它是靠本机单用户模式生效的。"""
    _register(BOSS)
    _register(MEMBER)
    record = _account_of(MEMBER, isolated_state / "accounts.json")
    assert record["reviewed_by"] == accounts.REVIEWED_BY_LOCAL_MODE
    assert record["reviewed_at"], "什么时候生效的也要记下来"


def test_模式开关写成不认识的值也按默认走(monkeypatch):
    """手滑写错（`ture` / `YES!`）不该把用户锁在门外 —— 按默认的本机单用户模式走。"""
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "ture")
    assert accounts.require_approval() is False
    account = _register(BOSS).json()["account"]
    assert account["status"] == accounts.STATUS_ACTIVE


# ════════════════════════════════════════════════════════════════════════
# ② 真实审批流模式（SRA_REQUIRE_APPROVAL=1）：一行没坏
# ════════════════════════════════════════════════════════════════════════
def test_A5_env_为1时注册仍然是待批准(monkeypatch):
    """A5：显式要求审批时，注册照旧落 pending，登录时说的是"等待批准"那一句。"""
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "1")
    assert accounts.require_approval() is True
    _register(BOSS)
    account = _register(MEMBER).json()["account"]
    assert (account["status"], account["role"]) == (accounts.STATUS_PENDING, accounts.ROLE_USER)
    assert (account["reviewed_by"], account["reviewed_at"]) == (None, None), "还没人批过"
    answer = _login(MEMBER)
    assert answer.status_code == 403
    assert answer.json()["error"]["code"] == "account_pending"


def test_A5_审批流仍然可用_批准之后就能登录(monkeypatch):
    """A5：审批流程本身可用 —— 管理员批一个 pending 账号，它随即能登录。"""
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "1")
    _register(BOSS)
    _register(MEMBER)
    boss = _login(BOSS).json()["account"]
    approved = client.post(f"/api/auth/accounts/{MEMBER}/review",
                           json={"action": "approve", "session_id": boss["session_id"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["account"]["status"] == accounts.STATUS_ACTIVE
    assert approved.json()["account"]["reviewed_by"] == BOSS, "审批要记下是谁批的"
    assert _login(MEMBER).status_code == 200


def test_A9_pending_这个状态仍然保留着(monkeypatch):
    """A9：不是把 pending 删掉、改成别的意思 —— 它还在，审批流照旧产生它、识别它。"""
    assert accounts.STATUS_PENDING in accounts.STATUSES
    assert accounts.STATUS_ERROR_CODE[accounts.STATUS_PENDING] == "account_pending"
    assert "待批准" in accounts.STATUS_TEXT[accounts.STATUS_PENDING]
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "1")
    _register(BOSS)
    assert _register(MEMBER).json()["account"]["status"] == accounts.STATUS_PENDING


# ════════════════════════════════════════════════════════════════════════
# ③ 启动迁移：把"卡在 pending、又没人能批"的库解开
# ════════════════════════════════════════════════════════════════════════
def test_A4_死锁回归_库里有pending且没有可用管理员_启动之后能登录(isolated_state, monkeypatch):
    """★ 这条就是用户那次真实死锁的回归测试。

    复现原样：库里只有 pending 账号，**没有任何可用管理员**（有的是早期测试账号留下的、
    密码用户根本不知道的那种情况也一并覆盖 —— 这里直接让库里一条可用管理员都没有）。
    通过**真实启动路径**跑一次迁移，然后：

        · 不删库   —— 账号文件还在、账号一个没少；
        · 不死锁   —— 用户能用自己的密码登录（不是靠改名/换 IP/浏览器 Cookie）；
        · 有管理员 —— 恰好一个，否则"谁批账号"这个问题还是无解。
    """
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (DEADLOCK_MEMBER, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (DEADLOCK_ADMIN, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-01T10:00:00+08:00"),
    ])
    before = json.loads(accounts_file.read_text(encoding="utf-8"))["accounts"]

    _boot_app(monkeypatch)                       # ← 真实的启动路径

    after = json.loads(accounts_file.read_text(encoding="utf-8"))["accounts"]
    assert len(after) == len(before) == 2, "迁移不许删账号"
    assert _login(DEADLOCK_ADMIN).status_code == 200, "启动之后用户必须能登录（死锁没解开）"
    assert _login(DEADLOCK_MEMBER).status_code == 200, "另一个 pending 账号也该被收进本机单用户模式"
    # 最早的那个账号（2026-09-01）成为唯一的管理员
    assert _account_of(DEADLOCK_ADMIN, accounts_file)["role"] == accounts.ROLE_ADMIN
    assert _account_of(DEADLOCK_MEMBER, accounts_file)["role"] == accounts.ROLE_USER
    assert accounts.admin_count() == 1, "必须恰好有一个可用管理员，否则还是没人能批账号"


def test_A6_迁移幂等_跑两次结果一致且不会产生第二个管理员(isolated_state):
    """A6/A7：连跑两次 —— 第二次什么都不改（文件逐字节相同），管理员始终只有一个。"""
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (DEADLOCK_MEMBER, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (DEADLOCK_ADMIN, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-01T10:00:00+08:00"),
    ])
    first = accounts.migrate_accounts()
    assert first["promoted"] == DEADLOCK_ADMIN
    assert first["activated"] == [DEADLOCK_MEMBER]
    once = accounts_file.read_text(encoding="utf-8")

    second = accounts.migrate_accounts()
    assert second["changed"] is False, f"第二次不该再改任何东西：{second}"
    assert (second["activated"], second["promoted"]) == ([], None)
    assert accounts_file.read_text(encoding="utf-8") == once, "第二次迁移改了文件（不幂等）"
    admins = [r["username"] for r in json.loads(once)["accounts"] if r["role"] == accounts.ROLE_ADMIN]
    assert admins == [DEADLOCK_ADMIN], f"迁移产生了第二个管理员：{admins}"


def test_A8_迁移不改密码_迁移前后原密码都能登录(isolated_state):
    """A8：迁移**一个字节都不许碰** pwd_*，原密码前后照样能登录。"""
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (DEADLOCK_ADMIN, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-01T10:00:00+08:00"),
    ])
    secret_fields = ("pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations")
    before = {key: _account_of(DEADLOCK_ADMIN, accounts_file)[key] for key in secret_fields}

    assert accounts.migrate_accounts()["promoted"] == DEADLOCK_ADMIN

    after = {key: _account_of(DEADLOCK_ADMIN, accounts_file)[key] for key in secret_fields}
    assert after == before, "迁移动了密码字段"
    assert _login(DEADLOCK_ADMIN).status_code == 200, "迁移之后原密码应当照旧能登录"
    assert _login(DEADLOCK_ADMIN, password="别的密码").status_code == 401


def test_A7_已经有可用管理员时迁移不碰任何人的角色(isolated_state):
    """A7/规则①：库里已经有人能批账号 → pending 升 active，**角色一个都不改**。"""
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (MEMBER, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (BOSS, accounts.STATUS_ACTIVE, accounts.ROLE_ADMIN, "2026-09-01T10:00:00+08:00"),
    ])
    report = accounts.migrate_accounts()
    assert (report["activated"], report["promoted"]) == ([MEMBER], None)
    assert _account_of(MEMBER, accounts_file)["role"] == accounts.ROLE_USER, "迁移不该给人提权"
    assert _account_of(MEMBER, accounts_file)["reviewed_by"] == accounts.REVIEWED_BY_MIGRATED
    assert _login(MEMBER).status_code == 200
    assert accounts.admin_count() == 1


def test_真实审批流模式下不替管理员按批准(isolated_state, monkeypatch):
    """模式=1 时有可用管理员 → pending **原地等批准**（迁移不替管理员做主）。"""
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "1")
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (MEMBER, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (BOSS, accounts.STATUS_ACTIVE, accounts.ROLE_ADMIN, "2026-09-01T10:00:00+08:00"),
    ])
    report = accounts.migrate_accounts()
    assert report["changed"] is False
    assert _account_of(MEMBER, accounts_file)["status"] == accounts.STATUS_PENDING
    assert _login(MEMBER).status_code == 403, "审批流模式下 pending 就该登不进去"


def test_真实审批流模式下没人能批账号时仍然先捞出管理员(isolated_state, monkeypatch):
    """模式=1 且**一个可用管理员都没有** → 那是死锁，先把最早账号提成管理员（其余照旧等批）。"""
    monkeypatch.setenv(accounts.ENV_REQUIRE_APPROVAL, "1")
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (MEMBER, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (BOSS, accounts.STATUS_PENDING, accounts.ROLE_USER, "2026-09-01T10:00:00+08:00"),
    ])
    report = accounts.migrate_accounts()
    assert report["promoted"] == BOSS
    assert _account_of(BOSS, accounts_file)["role"] == accounts.ROLE_ADMIN
    assert _account_of(MEMBER, accounts_file)["status"] == accounts.STATUS_PENDING, \
        "审批流模式下其余账号仍等管理员批准"
    assert _login(BOSS).status_code == 200, "被捞出来的管理员必须能登录"
    assert accounts.admin_count() == 1


def test_迁移不复活被拒绝或被停用的账号(isolated_state):
    """全是拒绝/停用的账号时**什么都不做** —— 那是有人按下的决定，迁移不猜、不复活。"""
    accounts_file = isolated_state / "accounts.json"
    _seed_accounts(accounts_file, [
        (MEMBER, accounts.STATUS_DISABLED, accounts.ROLE_USER, "2026-09-20T10:00:00+08:00"),
        (BOSS, accounts.STATUS_REJECTED, accounts.ROLE_USER, "2026-09-01T10:00:00+08:00"),
    ])
    before = accounts_file.read_text(encoding="utf-8")
    report = accounts.migrate_accounts()
    assert report["changed"] is False and report["skipped"]
    assert accounts_file.read_text(encoding="utf-8") == before


def test_账号文件读不出来时不拦服务启动_也不改任何东西(isolated_state, monkeypatch):
    """账号文件坏了 → 迁移如实记下错误、**不拦服务**（迁移是帮忙的，不是启动前提）。"""
    accounts_file = isolated_state / "accounts.json"
    accounts_file.parent.mkdir(parents=True, exist_ok=True)
    accounts_file.write_text("{ 这不是 JSON", encoding="utf-8")

    report = accounts.migrate_accounts()
    assert report["error"], "读不出账号表要如实记下原因（不许装作没事）"
    assert report["changed"] is False
    assert accounts_file.read_text(encoding="utf-8") == "{ 这不是 JSON", "坏文件也不许被改"

    _boot_app(monkeypatch)             # 服务照常起得来
    assert client.get("/api/health").status_code == 200


def test_空库时迁移什么都不做(isolated_state):
    """一个账号都没有：不建账号、不写文件（首次使用的引导照旧）。"""
    assert accounts.migrate_accounts()["scanned"] == 0
    assert not (isolated_state / "accounts.json").exists()
    assert accounts.account_count() == 0


# ════════════════════════════════════════════════════════════════════════
# ④ 没被这次改动带坏的东西（密码 / 会话语义原位不动）
# ════════════════════════════════════════════════════════════════════════
def test_A10_迁移不碰会话与密码语义(isolated_state):
    """A10：迁移只改 status/role/审计三样 —— 密码校验、会话签发照旧。"""
    assert accounts.migrate_accounts()["scanned"] == 0
    _register(BOSS)
    boss = _login(BOSS).json()["account"]
    assert accounts.resolve_session(boss["session_id"]) == BOSS, "会话照旧按编号解析得到账号"
    assert accounts.close_session(boss["session_id"]) is True, "退出登录照旧清会话"
    assert accounts.resolve_session(boss["session_id"]) is None
    record = state.get_account(BOSS)
    assert accounts.secret_matches(record, PLAIN) and not accounts.secret_matches(record, "错的")
