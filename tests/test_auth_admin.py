"""tests/test_auth_admin.py · 管理员审批 + 游客边界（用户 2026-09-25 那四条里的 ②③）

════════════════════════════════════════════════════════════════════════
【本文件盯的三件事】
════════════════════════════════════════════════════════════════════════
① **注册真入库、重启后还在**：账号落在 `state/accounts.json` 里。
   这里的"重启"是**真的新起一个进程**去读同一个目录（subprocess），不是"再 new 一个
   TestClient"——后者验不出"内存里的东西忘了写盘"这类问题。

② **管理员审批这条链**（谁批、怎么批、批完能不能登）：
   · 第一个注册的账号自动是管理员 + 可用 —— 否则没人能按"批准"，系统当场死锁；
   · 之后的账号是「等待批准」，登录时给的是**这一句**，不是"账号或密码不对"；
   · 管理员拒绝 → 「该账号未通过审批」；停用 → 「已被停用」；
     密码错 → 「账号或密码不对」—— **四种情况四句话**，谁都能逐个分辨；
   · 审批动作**只有管理员能做**：游客（没有会话）401、普通账号 403。

③ **游客边界（后端这一侧）**：游客没有会话编号，所以账号列表 / 审批 / 删除
   这几条管理端点**天然拒绝**它 —— 前端的拦截只是让用户早点看到人话，
   真正的闸门在这里（本文件要证明"游客不是只被弹窗挡住的"）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, captcha  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

PLAIN = "Sup3r-秘密-2026"
BOSS = "老板"
MEMBER = "小李"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """账号表落临时目录（绝不碰仓库里的真实 state/），并清两处进程内状态。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    captcha.reset()
    accounts.reset_failures()
    yield tmp_path / "state"
    captcha.reset()
    accounts.reset_failures()


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


def _sign_in(username: str) -> dict:
    """登录成功 → 返回那个账号（带会话编号）。登录不成功就直接让用例红。"""
    answer = _login(username)
    assert answer.status_code == 200, answer.text
    return answer.json()["account"]


def _error(answer) -> dict:
    return answer.json()["error"]


# ════════════════════════════════════════════════════════════════════════
# ② 注册真入库（硬证据：**新起一个进程**去读同一份账号文件）
# ════════════════════════════════════════════════════════════════════════
def test_账号真的落在磁盘上_换一个进程读得到(isolated_state):
    """注册 → 换一个新进程读同一份账号文件 → 账号还在。

    为什么要新进程：同一个进程里模块级缓存可能把"其实没写盘"盖住。
    另起一个 python 只做一件事 —— 从磁盘把账号读出来 —— 读到就是你真写了。
    """
    assert _register(BOSS).status_code == 200
    assert _register(MEMBER).status_code == 200
    path = isolated_state / "accounts.json"
    assert path.is_file(), "注册之后账号文件不存在（没落盘）"

    probe = (
        "import json, pathlib, os;"
        "p = pathlib.Path(os.environ['SRA_STATE_DIR']) / 'accounts.json';"
        "data = json.loads(p.read_text(encoding='utf-8'));"
        "print(json.dumps([a['username'] for a in data['accounts']], ensure_ascii=False))"
    )
    env = dict(os.environ)
    env["SRA_STATE_DIR"] = str(isolated_state)
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          cwd=str(PROJECT_ROOT), env=env, timeout=120)
    assert done.returncode == 0, done.stderr[-600:]
    names = json.loads(done.stdout.strip().splitlines()[-1])
    assert set(names) == {BOSS, MEMBER}, f"另一个进程读到的账号不对：{names}"


def test_重启后仍然能登录(isolated_state):
    """换个进程照样能登录 —— "账号还在"要能真的用，不只是"文件里有个名字"。"""
    assert _register(BOSS).status_code == 200
    code = (
        "import json, re, os, sys;"
        "sys.path.insert(0, '.');"
        "from app import accounts;"
        "rec = accounts.state.get_account(os.environ['PROBE_NAME']);"
        "print('FOUND' if rec else 'MISSING')"
    )
    env = dict(os.environ)
    env["SRA_STATE_DIR"] = str(isolated_state)
    env["PROBE_NAME"] = BOSS
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=str(PROJECT_ROOT), env=env, timeout=120)
    assert done.returncode == 0, done.stderr[-600:]
    assert "FOUND" in done.stdout, "新进程里找不到这个账号"


# ════════════════════════════════════════════════════════════════════════
# ③ 第一个账号自动是管理员（否则没人能批账号，系统当场死锁）
# ════════════════════════════════════════════════════════════════════════
def test_第一个注册的账号自动成为管理员且直接可用():
    account = _register(BOSS).json()["account"]
    assert account["role"] == accounts.ROLE_ADMIN
    assert account["status"] == accounts.STATUS_ACTIVE
    assert account["reviewed_by"], "自动成为管理员这件事要留下出处（审计）"
    assert _login(BOSS).status_code == 200, "管理员注册完就该能登录"


def test_第二个注册的账号是待批准_不能直接登录():
    _register(BOSS)
    account = _register(MEMBER).json()["account"]
    assert account["role"] == accounts.ROLE_USER, "只有第一个账号是管理员"
    assert account["status"] == accounts.STATUS_PENDING
    answer = _login(MEMBER)
    assert answer.status_code == 403
    assert _error(answer)["code"] == "account_pending"
    assert "等待管理员批准" in _error(answer)["message"]


# ════════════════════════════════════════════════════════════════════════
# ③ 四种"登不上"要说四句不同的话（用户点名要能逐个分辨）
# ════════════════════════════════════════════════════════════════════════
def test_四种登不上各说各的话():
    _register(BOSS)
    _register(MEMBER)                       # → 待批准
    _register("被拒的")                     # → 待批准，稍后拒绝
    _register("被停的")                     # → 待批准，稍后停用
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    assert client.post("/api/auth/accounts/被拒的/review",
                       json={"action": "reject", "session_id": sid}).status_code == 200
    assert client.post("/api/auth/accounts/被停的/review",
                       json={"action": "approve", "session_id": sid}).status_code == 200
    assert client.post("/api/auth/accounts/被停的/review",
                       json={"action": "disable", "session_id": sid}).status_code == 200

    pending = _login(MEMBER)
    rejected = _login("被拒的")
    disabled = _login("被停的")
    wrong = _login(MEMBER, password="this-is-not-it")
    assert (pending.status_code, rejected.status_code, disabled.status_code) == (403, 403, 403)
    assert wrong.status_code == 401, "密码错是 401（没通过校验），不是 403（没被允许）"
    messages = {
        _error(pending)["message"],
        _error(rejected)["message"],
        _error(disabled)["message"],
        _error(wrong)["message"],
    }
    assert len(messages) == 4, f"四种情况的话必须各不相同：{sorted(messages)}"
    assert "账号或密码不对" in _error(wrong)["message"], "密码错就是这句，不许被状态那几句顶掉"


def test_账号不存在与密码错给同一句话():
    """不给"这个账号存在吗"的探测口：两条路同一句话、同一个状态码。"""
    _register(BOSS)
    missing = _login("查无此人")
    wrong = _login(BOSS, password="wrong-password")
    assert missing.status_code == wrong.status_code == 401
    assert _error(missing)["message"] == _error(wrong)["message"] == "账号或密码不对。"


# ════════════════════════════════════════════════════════════════════════
# ③ 审批：谁能批、批完什么效果
# ════════════════════════════════════════════════════════════════════════
def test_管理员批准之后就能登录():
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    answer = client.post(f"/api/auth/accounts/{MEMBER}/review",
                         json={"action": "approve", "session_id": boss["session_id"]})
    assert answer.status_code == 200, answer.text
    reviewed = answer.json()["account"]
    assert reviewed["status"] == accounts.STATUS_ACTIVE
    assert reviewed["reviewed_by"] == BOSS, "要记下是谁批的（审计）"
    assert reviewed["reviewed_at"], "要记下什么时候批的（审计）"
    assert _login(MEMBER).status_code == 200, "批完就该能登录"


def test_审批只有管理员能做_游客与普通账号都被挡住():
    """★ 游客边界在后端这一侧的证据：**没有会话** → 401；不是管理员 → 403。"""
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    client.post(f"/api/auth/accounts/{MEMBER}/review",
                json={"action": "approve", "session_id": boss["session_id"]})
    member = _sign_in(MEMBER)

    # 游客：没有会话编号（前端也不会给）—— 两条路都拿不到账号列表
    guest_list = client.get("/api/auth/accounts")
    assert guest_list.status_code == 401
    assert _error(guest_list)["code"] == "session_invalid"
    guest_review = client.post(f"/api/auth/accounts/{BOSS}/review", json={"action": "reject"})
    assert guest_review.status_code == 401, "游客连审批动作都碰不到"

    # 普通账号：登录了，但角色不是管理员
    member_list = client.get("/api/auth/accounts", params={"session_id": member["session_id"]})
    assert member_list.status_code == 403
    assert _error(member_list)["code"] == "not_admin"
    member_review = client.post(f"/api/auth/accounts/{BOSS}/review",
                                json={"action": "reject", "session_id": member["session_id"]})
    assert member_review.status_code == 403, "普通账号不能审批别人"


def test_账号列表_待批准的排在最前且带出数量():
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    data = client.get("/api/auth/accounts", params={"session_id": boss["session_id"]}).json()
    assert data["pending"] == 1
    assert data["accounts"][0]["username"] == MEMBER, "待批准的排在最前（要先看到等着批的）"
    assert data["status_text"][accounts.STATUS_PENDING] == "等待批准"
    # 列表里**绝不含**盐与校验值（走的是同一个白名单）
    for item in data["accounts"]:
        assert set(item) == set(accounts.PUBLIC_FIELDS)


def test_管理员不能被拒绝或停用():
    """别把能批账号的人弄没了 —— 系统会退化成"谁都不能批账号"。"""
    _register(BOSS)
    boss = _sign_in(BOSS)
    for action in ("reject", "disable"):
        answer = client.post(f"/api/auth/accounts/{BOSS}/review",
                             json={"action": action, "session_id": boss["session_id"]})
        assert answer.status_code == 400
        assert _error(answer)["code"] == "last_admin"
    assert _login(BOSS).status_code == 200, "管理员照旧能登录"


def test_停用之后说被停用_恢复之后又能登():
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "approve", "session_id": sid})
    client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "disable", "session_id": sid})
    disabled = _login(MEMBER)
    assert disabled.status_code == 403
    assert "已被停用" in _error(disabled)["message"]
    client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "enable", "session_id": sid})
    assert _login(MEMBER).status_code == 200, "恢复之后要能登回来"


# ════════════════════════════════════════════════════════════════════════
# ③ 删除（已批准列表里的「删除」）：不可逆，规矩要多几条
# ════════════════════════════════════════════════════════════════════════
def test_删除账号_记录消失且账号名可以被重新注册(isolated_state):
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "approve", "session_id": sid})

    answer = client.request("DELETE", f"/api/auth/accounts/{MEMBER}", params={"session_id": sid})
    assert answer.status_code == 200, answer.text
    assert answer.json()["deleted"] is True
    raw = (isolated_state / "accounts.json").read_text(encoding="utf-8")
    assert MEMBER not in raw, "记录该真的从账号文件里消失（不是标个停用）"
    names = [item["username"] for item in
             client.get("/api/auth/accounts", params={"session_id": sid}).json()["accounts"]]
    assert names == [BOSS]
    # 账号名随即可以被重新注册（这是"删除"与"停用"的区别）
    again = _register(MEMBER)
    assert again.status_code == 200, again.text
    assert again.json()["account"]["status"] == accounts.STATUS_PENDING


def test_删除也要管理员_游客与普通账号都不行():
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    client.post(f"/api/auth/accounts/{MEMBER}/review", json={"action": "approve", "session_id": sid})
    member = _sign_in(MEMBER)

    guest = client.request("DELETE", f"/api/auth/accounts/{MEMBER}")
    assert guest.status_code == 401, "游客删不动任何账号"
    other = client.request("DELETE", f"/api/auth/accounts/{BOSS}",
                           params={"session_id": member["session_id"]})
    assert other.status_code == 403, "普通账号也删不动"
    assert _login(MEMBER).status_code == 200, "两次都被挡住，账号原样还在"


def test_唯一的管理员不许删_自己也不许删(isolated_state):
    """两条保护合起来的效果：这台机器上永远至少留着一个能批账号的人。"""
    _register(BOSS)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    myself = client.request("DELETE", f"/api/auth/accounts/{BOSS}", params={"session_id": sid})
    assert myself.status_code == 400
    assert _error(myself)["code"] == "cannot_delete_self"

    # 再来一个管理员（先注册、再批准）—— 但 role 只在注册时定，批准不会改角色，
    # 所以系统里始终只有"第一个账号"这一个管理员：删它永远被拦。
    _register("二号")
    client.post("/api/auth/accounts/二号/review", json={"action": "approve", "session_id": sid})
    still = client.request("DELETE", f"/api/auth/accounts/{BOSS}", params={"session_id": sid})
    assert still.status_code == 400, "唯一的管理员删不得"
    raw = (isolated_state / "accounts.json").read_text(encoding="utf-8")
    assert BOSS in raw, "被拦下之后账号原样还在"


def test_账号层的删除守卫_最后一个管理员删不得():
    """接口层先拦了"删自己"，所以 `last_admin` 这条路要走**账号层**才验得到。

    为什么要单验它：这是"别把系统搞成没人能批账号"的最后一道闸门。
    接口层那道（不能删自己）只挡"删自己"，挡不住"别的管理员来删我"——
    将来角色能授予多个管理员时，这里就是真正起作用的那一道。
    """
    _register(BOSS)                       # 唯一的管理员
    _register(MEMBER)                     # 普通账号（待批准，删得掉）
    with pytest.raises(accounts.AccountError) as error:
        accounts.delete_account(BOSS)
    assert error.value.code == "last_admin"
    assert accounts.state.get_account(BOSS) is not None, "被拦下之后账号原样还在"
    removed = accounts.delete_account(MEMBER)
    assert removed["username"] == MEMBER
    assert accounts.state.get_account(MEMBER) is None


def test_删除不存在的账号是404():
    _register(BOSS)
    boss = _sign_in(BOSS)
    answer = client.request("DELETE", "/api/auth/accounts/查无此人",
                            params={"session_id": boss["session_id"]})
    assert answer.status_code == 404
    assert _error(answer)["code"] == "account_not_found"


# ════════════════════════════════════════════════════════════════════════
# ④ 会话的边界（写清楚"会话不是什么"）
# ════════════════════════════════════════════════════════════════════════
def test_退出登录之后那条会话立刻失效():
    _register(BOSS)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    assert client.get("/api/auth/accounts", params={"session_id": sid}).status_code == 200
    assert client.post("/api/auth/logout", json={"session_id": sid}).status_code == 200
    after = client.get("/api/auth/accounts", params={"session_id": sid})
    assert after.status_code == 401, "退出之后这条会话不该还能管账号"


def test_响应里永远没有盐与校验值():
    """把账号相关的端点都打一遍，逐个响应扫敏感字段。"""
    _register(BOSS)
    _register(MEMBER)
    boss = _sign_in(BOSS)
    sid = boss["session_id"]
    bodies = [
        _register("再来一个").text,
        _login(BOSS).text,
        client.get("/api/auth/accounts", params={"session_id": sid}).text,
        client.post(f"/api/auth/accounts/{MEMBER}/review",
                    json={"action": "approve", "session_id": sid}).text,
        client.request("DELETE", f"/api/auth/accounts/{MEMBER}",
                       params={"session_id": sid}).text,
        client.get("/api/auth/accounts/exists", params={"username": BOSS}).text,
    ]
    for body in bodies:
        for key in ("pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations"):
            assert key not in body, f"响应里漏出了 {key}：{body[:200]}"
        assert PLAIN not in body, "响应里带回了明文密码"
