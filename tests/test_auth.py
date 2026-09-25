"""tests/test_auth.py · 本地账号（注册 / 登录 / 查重）验收测试。

════════════════════════════════════════════════════════════════════════
【本文件测什么 —— 核心是"密码到底存了什么、登录到底比了什么"】
════════════════════════════════════════════════════════════════════════
真实 HTTP（TestClient 进程内）、真落盘（临时 state 目录）、真摘要（标准库 pbkdf2）。

  ① 注册：成功 / 重复（409，且**不建第二条记录**）/ 形态不合格（400 + 人话）；
  ② 登录：对 → 放行并刷新 last_login_at；错 → 401，且**账号不存在与密码错给同一句话**
     （不给出"这个账号在不在"的探测口）；
  ③ **密码绝不明文落盘**：拿注册时用的那串明文去 grep `state/accounts.json` → 0 命中；
     文件里存的是算法名 + 每账号独立的盐 + 校验值 + 迭代次数；
  ④ 响应里**绝不含**盐 / 校验值（白名单：只有账号名、显示名、两个时间）；
  ⑤ 每账号独立盐：同一个密码在两个账号下算出的校验值不同（彩虹表在这儿失效）；
  ⑥ 查重端点：`{exists, account_count}`；一个账号都没有时 account_count=0（前端据此给首次使用引导）；
  ⑦ 游客不进账号表：没有注册请求时，账号文件根本不会被创建；
  ⑧ 既有能力不回归：健康检查与问答能力清单照旧 200（新增路由没吃掉老路由）。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, captcha  # noqa: E402
from app.api import app  # noqa: E402
from app.repositories import base as repo_base  # noqa: E402

client = TestClient(app)

# 刻意用一串"一眼认得出来"的明文：它只要出现在文件里，任何一条断言都藏不住
PLAIN = "Sup3r-秘密-2026"
PLAIN_LATIN = "Sup3r"
ACCOUNT = "tang.yu-01"

# 响应里永远不许出现的字段（后端记录里有的敏感字段名）
SECRET_KEYS = ("pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations", "secret", "salt")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """账号表落临时目录：绝不碰仓库里的真实 state/。

    顺带清两处**进程内**状态（验证码字典、连续失败计数）——它们是跨用例共享的模块级数据，
    不清的话上一条用例留下的计数会把下一条带进"冷却"，看起来像随机失败。
    """
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    captcha.reset()
    accounts.reset_failures()
    yield tmp_path / "state"
    captcha.reset()
    accounts.reset_failures()


def _captcha() -> dict:
    """取一张验证码，并把图上的字符读出来。

    怎么读的：每个字符都是一个独立的 <text> 元素 —— 这条断言本身就在证明
    "字符确实画在图上、而且是分开的四个"，也顺带证明这图是能被认出来的（AC-9 的机器版）。
    """
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    assert len(code) == captcha.LENGTH, f"图里应当正好 {captcha.LENGTH} 个字符，实际读到 {code!r}"
    return {"captcha_id": data["captcha_id"], "captcha_text": code}


def _login(username: str = ACCOUNT, password: str = PLAIN, **extra) -> object:
    """带着验证码登录（验证码是**必填**的，测试也走用户那条路，不开后门）。"""
    return client.post("/api/auth/login",
                       json={"username": username, "password": password, **_captcha(), **extra})


def _accounts_file(state_dir: pathlib.Path) -> pathlib.Path:
    return state_dir / "accounts.json"


def _raw(state_dir: pathlib.Path) -> str:
    path = _accounts_file(state_dir)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _register(username: str = ACCOUNT, password: str = PLAIN, display_name: str | None = None):
    body = {"username": username, "password": password, **_captcha()}
    if display_name is not None:
        body["display_name"] = display_name
    return client.post("/api/auth/register", json=body)


def _no_secret(payload_text: str) -> None:
    """响应里既不许有敏感字段名，也不许有那串明文密码。"""
    for key in SECRET_KEYS:
        assert key not in payload_text, f"响应里漏出了 {key}：{payload_text[:200]}"
    assert PLAIN not in payload_text, f"响应里带回了明文密码：{payload_text[:200]}"


# ════════════════════════════════════════════════════════════════════════
# ① 注册
# ════════════════════════════════════════════════════════════════════════
def test_注册成功并返回账号信息且不含任何敏感字段():
    response = _register(display_name="唐宇（销售）")
    assert response.status_code == 200, response.text
    _no_secret(response.text)
    account = response.json()["account"]
    assert account["username"] == ACCOUNT
    assert account["display_name"] == "唐宇（销售）"
    assert account["last_login_at"] is None, "刚注册还没登录过"
    assert account["created_at"], "建号时间要记下来（审计）"
    # 白名单：多出来的字段一个都不许有
    assert set(account) == {"username", "display_name", "created_at", "last_login_at"}


def test_显示名留空就用账号名():
    response = _register(username="只填账号", password=PLAIN, display_name="")
    assert response.json()["account"]["display_name"] == "只填账号"


def test_中文账号与点中划线都能注册():
    for name in ("唐宇", "tang.yu_01-x"):
        response = _register(username=name, password=PLAIN)
        assert response.status_code == 200, f"{name} 应当可以注册：{response.text}"


def test_重复注册返回409且不创建第二条记录(isolated_state):
    assert _register().status_code == 200
    again = _register(display_name="另一个人")
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "username_taken"
    assert "已被注册" in again.json()["error"]["message"]
    _no_secret(again.text)
    # 硬证据：文件里只有一条记录，且没被第二次注册改写
    records = json.loads(_raw(isolated_state))["accounts"]
    assert len(records) == 1, "重复注册居然建了第二条记录"
    assert records[0]["display_name"] == ACCOUNT, "原有记录被第二次注册改写了"


def test_重复判断不区分大小写():
    assert _register(username="TangYu", password=PLAIN).status_code == 200
    for same in ("tangyu", "TANGYU", "TaNgYu"):
        response = _register(username=same, password=PLAIN)
        assert response.status_code == 409, f"{same} 应当被认作已注册：{response.text}"


@pytest.mark.parametrize("username, why", [
    ("唐", "账号太短"),
    ("a" * 41, "账号太长"),
    ("a b", "账号里有空格"),
    ("tang@yu", "账号里有不允许的符号"),
    ("", "账号为空"),
])
def test_账号形态不合格返回400加人话(username: str, why: str):
    response = _register(username=username, password=PLAIN)
    assert response.status_code == 400, f"{why}：{response.text}"
    message = response.json()["error"]["message"]
    assert message and ("账号" in message), f"{why} 的人话不对：{message}"
    assert "Traceback" not in message and "pydantic" not in message


@pytest.mark.parametrize("password, why", [("12345", "密码只有 5 位"), ("", "密码为空")])
def test_密码太短返回400加人话(password: str, why: str):
    response = _register(password=password)
    assert response.status_code == 400, f"{why}：{response.text}"
    assert "密码" in response.json()["error"]["message"]


# ════════════════════════════════════════════════════════════════════════
# ② 登录
# ════════════════════════════════════════════════════════════════════════
def test_登录成功返回账号信息并刷新上次登录时间(isolated_state):
    _register()
    before = json.loads(_raw(isolated_state))["accounts"][0]["last_login_at"]
    response = _login()
    assert response.status_code == 200, response.text
    _no_secret(response.text)
    assert response.json()["account"]["username"] == ACCOUNT
    after = json.loads(_raw(isolated_state))["accounts"][0]["last_login_at"]
    assert before is None and after, "登录成功要把上次登录时间写回去"


def test_登录大小写不敏感且不改写原来的账号写法(isolated_state):
    _register(username="TangYu")
    response = _login(username="tangyu")
    assert response.status_code == 200, response.text
    assert response.json()["account"]["username"] == "TangYu", "回给前端的应当是原来那个写法"
    assert json.loads(_raw(isolated_state))["accounts"][0]["username"] == "TangYu"


def test_密码错与账号不存在给同一句话同一个状态码():
    _register()
    wrong = _login(password="wrong-password")
    missing = _login(username="从没注册过")
    assert wrong.status_code == 401 and missing.status_code == 401
    assert wrong.json()["error"]["code"] == missing.json()["error"]["code"] == "bad_credentials"
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "账号或密码不对" in wrong.json()["error"]["message"]
    # 那句话里不许透出"这个账号存在 / 不存在"
    for word in ("不存在", "没有这个账号", "未注册"):
        assert word not in wrong.json()["error"]["message"]
    _no_secret(wrong.text)
    _no_secret(missing.text)


def test_登录失败不刷新上次登录时间(isolated_state):
    _register()
    _login(password="wrong-password")
    assert json.loads(_raw(isolated_state))["accounts"][0]["last_login_at"] is None, \
        "密码不对却把登录时间刷新了"


def test_密码首尾空格算进密码本身():
    """不做 trim：' abc12345' 与 'abc12345' 是两个不同的密码（对不上就是不对）。"""
    _register(password="abc12345")
    assert _login(password="abc12345 ").status_code == 401
    assert _login(password="abc12345").status_code == 200


# ════════════════════════════════════════════════════════════════════════
# ③④⑤ 密码怎么存的（这一段是本文件的重点）
# ════════════════════════════════════════════════════════════════════════
def test_密码绝不明文落盘(isolated_state):
    """AC 的硬证据：拿注册时那串明文去 grep 账号文件 → 0 命中。"""
    _register()
    raw = _raw(isolated_state)
    assert raw, "账号文件没落盘"
    assert PLAIN not in raw, "账号文件里出现了明文密码"
    assert PLAIN_LATIN not in raw, "账号文件里出现了明文密码的一部分"
    record = json.loads(raw)["accounts"][0]
    # 记录的字段**逐个锁死**（多一个字段就要有人来解释它是干什么的）
    assert set(record) == {"username", "display_name", "created_at", "last_login_at",
                           "pwd_algo", "pwd_salt", "pwd_hash", "pwd_iterations"}, \
        f"账号记录的字段变了：{sorted(record)}"
    assert record["pwd_algo"] == "pbkdf2_sha256"
    assert record["pwd_iterations"] == accounts.PWD_ITERATIONS
    assert len(record["pwd_salt"]) == accounts.PWD_SALT_BYTES * 2, "盐应当是 16 字节（32 位十六进制）"
    assert len(record["pwd_hash"]) == 64, "PBKDF2-SHA256 的校验值是 32 字节（64 位十六进制）"


def test_同一密码在两个账号下算出不同的校验值(isolated_state):
    assert _register(username="甲方", password=PLAIN).status_code == 200
    assert _register(username="乙方", password=PLAIN).status_code == 200
    records = json.loads(_raw(isolated_state))["accounts"]
    assert len(records) == 2
    assert records[0]["pwd_salt"] != records[1]["pwd_salt"], "两个账号用了同一个盐"
    assert records[0]["pwd_hash"] != records[1]["pwd_hash"], \
        "同一个密码在两个账号下算出了同一个校验值（盐没起作用）"


def test_校验值比对函数对与错都对():
    secret = accounts.make_secret(PLAIN)
    assert accounts.secret_matches(secret, PLAIN) is True
    assert accounts.secret_matches(secret, PLAIN + "x") is False
    assert accounts.secret_matches(secret, "") is False
    # 记录被手改坏 → 一律当"对不上"，不许放行、更不许崩
    for broken in ({}, {"pwd_algo": "md5", "pwd_salt": secret["pwd_salt"], "pwd_hash": secret["pwd_hash"]},
                   {"pwd_algo": "pbkdf2_sha256", "pwd_salt": "zz", "pwd_hash": "00", "pwd_iterations": 1},
                   {"pwd_algo": "pbkdf2_sha256", "pwd_salt": secret["pwd_salt"],
                    "pwd_hash": secret["pwd_hash"], "pwd_iterations": 0}):
        assert accounts.secret_matches(broken, PLAIN) is False


def test_公开视图只有白名单字段():
    record = {"username": "甲", "display_name": "甲", "created_at": "t", "last_login_at": None,
              "pwd_algo": "pbkdf2_sha256", "pwd_salt": "aa", "pwd_hash": "bb", "pwd_iterations": 1}
    assert accounts.public(record) == {
        "username": "甲", "display_name": "甲", "created_at": "t", "last_login_at": None,
    }


def test_账号文件之外的路径不会漏出敏感字段(isolated_state):
    """把所有账号端点都打一遍，逐个响应扫敏感字段 —— 不是只扫注册那一条。"""
    _register()
    responses = [
        _login(),
        _login(password="wrong"),
        _register(),
        client.get("/api/auth/accounts/exists", params={"username": ACCOUNT}),
        client.get("/api/auth/accounts/exists"),
    ]
    for response in responses:
        _no_secret(response.text)


# ════════════════════════════════════════════════════════════════════════
# ⑥ 查重端点（注册页失焦查重 / 首次使用引导）
# ════════════════════════════════════════════════════════════════════════
def test_查重端点按名字回答并带上账号总数():
    empty = client.get("/api/auth/accounts/exists")
    assert empty.status_code == 200
    assert empty.json() == {"exists": False, "account_count": 0}, "一个账号都没有时要说 0"

    _register()
    taken = client.get("/api/auth/accounts/exists", params={"username": ACCOUNT})
    assert taken.json() == {"exists": True, "account_count": 1}
    free = client.get("/api/auth/accounts/exists", params={"username": "没人用过"})
    assert free.json() == {"exists": False, "account_count": 1}
    # 大小写不敏感，与注册/登录同一口径
    assert client.get("/api/auth/accounts/exists",
                      params={"username": ACCOUNT.upper()}).json()["exists"] is True


def test_查重端点用GET且不改任何东西(isolated_state):
    _register()
    before = _raw(isolated_state)
    client.get("/api/auth/accounts/exists", params={"username": "随便什么"})
    assert _raw(isolated_state) == before, "查重把文件改了"


# ════════════════════════════════════════════════════════════════════════
# ⑦ 游客不进账号表
# ════════════════════════════════════════════════════════════════════════
def test_没有注册请求就不会有账号文件(isolated_state):
    """游客是**纯前端**的本机随机名：一次注册请求都不发，账号表就不该被创建。"""
    client.get("/api/health")
    client.get("/api/auth/accounts/exists")
    assert not _accounts_file(isolated_state).exists(), "没人注册却建了账号文件"


# ════════════════════════════════════════════════════════════════════════
# ⑧ 既有能力不回归 + 抽象接口完整
# ════════════════════════════════════════════════════════════════════════
def test_新增路由没影响既有端点():
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/chat/capabilities").status_code == 200
    assert client.get("/api/datasets").status_code == 200
    # 账号表是空的时候也不该影响任何老端点
    assert client.get("/api/tasks").status_code == 200


def test_账号仓库实现了抽象接口的全部方法():
    """漏实现一个抽象方法 = 运行到那一步才炸；这里在导入期就把它钉死。"""
    from app.repositories import json_repo

    assert repo_base.AccountRepository in repo_base.REPOSITORY_INTERFACES
    unsatisfied = getattr(json_repo.JsonAccountRepository, "__abstractmethods__", frozenset())
    assert not unsatisfied, f"JSON 账号仓库还有没实现的方法：{sorted(unsatisfied)}"


def test_账号仓库按名字取且大小写不敏感(isolated_state):
    from app.repositories import json_repo

    repo = json_repo.JsonAccountRepository(lambda: _accounts_file(isolated_state))
    _register(username="TangYu")
    assert repo.count() == 1
    assert repo.get("tangyu")["username"] == "TangYu"
    assert repo.get("TANGYU")["username"] == "TangYu"
    assert repo.get("") is None and repo.get("  ") is None
    assert repo.get("从没注册过") is None
    # 刷新登录时间：值真的变了才写盘（幂等，与任务状态同一约定）
    at = "2026-09-25T10:00:00+08:00"
    assert repo.set_last_login("tangyu", at)["last_login_at"] == at
    assert repo.set_last_login("tangyu", at)["last_login_at"] == at
    assert repo.set_last_login("从没注册过", at) is None


# ════════════════════════════════════════════════════════════════════════
# ⑨ 图形验证码（注册与登录**都**要，没有旁路）
# ════════════════════════════════════════════════════════════════════════
def test_注册与登录都必须带验证码():
    """不带验证码 → 400 + 人话（不是 422 那种用户看不懂的报错）。"""
    for path, body in (
        ("/api/auth/register", {"username": ACCOUNT, "password": PLAIN}),
        ("/api/auth/login", {"username": ACCOUNT, "password": PLAIN}),
    ):
        response = client.post(path, json=body)
        assert response.status_code == 400, f"{path}：{response.text}"
        error = response.json()["error"]
        assert error["code"] == "captcha_missing"
        assert "验证码" in error["message"], error["message"]


def test_验证码填错就是填错_给的是人话():
    _register()
    data = client.get("/api/auth/captcha").json()
    response = client.post("/api/auth/login", json={
        "username": ACCOUNT, "password": PLAIN,
        "captcha_id": data["captcha_id"], "captcha_text": "ZZZZ",
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "captcha_wrong"
    assert response.json()["error"]["message"] == "验证码不对，请重新输入。"


def test_验证码用一次即作废_重放会被拒():
    """同一张图连着用两次：第二次必须失败（否则等于没做防重放）。"""
    ticket = _captcha()
    first = client.post("/api/auth/register",
                        json={"username": "第一次", "password": PLAIN, **ticket})
    assert first.status_code == 200, first.text
    second = client.post("/api/auth/register",
                         json={"username": "第二次", "password": PLAIN, **ticket})
    assert second.status_code == 400, second.text
    assert second.json()["error"]["code"] == "captcha_expired"
    assert "过期" in second.json()["error"]["message"]


def test_验证码过期之后用不了(monkeypatch):
    monkeypatch.setattr(captcha, "TTL_SECONDS", 0)      # 一发出就算过期
    ticket = _captcha()
    response = client.post("/api/auth/register",
                           json={"username": "过期测试", "password": PLAIN, **ticket})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "captcha_expired"


def test_验证码大小写不敏感():
    """图上是 ZXCV 就写 ZXCV；用户敲 zxcv 也该认（看得清就能对，不该因为大小写卡人）。"""
    data = client.get("/api/auth/captcha").json()
    code = "".join(re.findall(r">([0-9A-Z])</text>", data["image_svg"]))
    response = client.post("/api/auth/register", json={
        "username": "大小写测试", "password": PLAIN,
        "captcha_id": data["captcha_id"], "captcha_text": f"  {code.lower()}  ",
    })
    assert response.status_code == 200, response.text


def test_验证码图人眼可读_四个字符分开画且没有易混字符():
    """AC-9 的机器版：字符**分开画**（每个一个元素）、**正好四个**、不含容易看错的 0/O/1/I/L。"""
    for _ in range(20):                                 # 多取几张：随机的，一张不能代表全部
        data = client.get("/api/auth/captcha").json()
        codes = re.findall(r">([0-9A-Z])</text>", data["image_svg"])
        assert len(codes) == captcha.LENGTH
        for char in codes:
            assert char in captcha.ALPHABET
            assert char not in "01OIL", f"图里出现了容易看错的字符：{char}"
        # 大小与摆放：4 个字符横向分开（不叠在一起），高度装得下 26px 的字
        assert 'width="132"' in data["image_svg"] and 'height="44"' in data["image_svg"]
        assert data["expires_in"] == captcha.TTL_SECONDS == 120
        for index, char in enumerate(codes):
            assert f'x="{14 + index * captcha.CHAR_BOX}"' in data["image_svg"], "字符没有各占一格"


def test_验证码不会写进任何文件(isolated_state):
    client.get("/api/auth/captcha")
    client.get("/api/auth/captcha")
    assert not (isolated_state / "accounts.json").exists(), "只是要了张验证码，不该建账号文件"


# ════════════════════════════════════════════════════════════════════════
# ⑩ 连续尝试失败 → 冷却
# ════════════════════════════════════════════════════════════════════════
def test_连续错五次进入冷却并告诉还要等多久():
    _register()
    for index in range(1, accounts.MAX_ATTEMPTS + 1):
        response = _login(password="wrong-password")
        assert response.status_code == 401, f"第 {index} 次应当还是 401：{response.text}"
    # 第 5 次之后就上锁了
    assert accounts.lock_remaining(ACCOUNT) == accounts.COOLDOWN_SECONDS
    # 第 6 次：**连验证码都不再校验**，直接告诉你要等
    locked = _login(password=PLAIN)                     # 密码这次是对的，照样不放行
    assert locked.status_code == 429, locked.text
    error = locked.json()["error"]
    assert error["code"] == "too_many_attempts"
    assert "尝试次数过多" in error["message"]
    wait = int(re.search(r"请 (\d+) 秒", error["message"]).group(1))
    assert 1 <= wait <= accounts.COOLDOWN_SECONDS


def test_填错验证码也算一次失败():
    """"填错验证码"也是有人在猜 —— 连着填错一样会进冷却。"""
    _register()
    for _ in range(accounts.MAX_ATTEMPTS):
        data = client.get("/api/auth/captcha").json()
        client.post("/api/auth/login", json={
            "username": ACCOUNT, "password": PLAIN,
            "captcha_id": data["captcha_id"], "captcha_text": "ZZZZ",
        })
    assert accounts.lock_remaining(ACCOUNT) > 0, "连着填错验证码没有进冷却"
    assert _login().status_code == 429


def test_验证码过期不算失败_不会因为页面放久了就被锁():
    _register()
    for _ in range(accounts.MAX_ATTEMPTS):
        ticket = _captcha()
        client.post("/api/auth/login", json={
            "username": ACCOUNT, "password": PLAIN,
            "captcha_id": "早就没了的编号", "captcha_text": ticket["captcha_text"],
        })
    assert accounts.lock_remaining(ACCOUNT) == 0, "验证码过期不该被记成一次尝试"
    assert _login().status_code == 200


def test_登录成功把连续失败清零():
    _register()
    for _ in range(accounts.MAX_ATTEMPTS - 1):
        assert _login(password="wrong-password").status_code == 401
    assert accounts.lock_remaining(ACCOUNT) == 0
    ticket = _captcha()
    good = client.post("/api/auth/login", json={
        "username": ACCOUNT, "password": PLAIN, **ticket,
    })
    assert good.status_code == 200, good.text
    # 清零之后又可以有 4 次机会（不用等 60 秒）
    for _ in range(accounts.MAX_ATTEMPTS - 1):
        assert _login(password="wrong-password").status_code == 401
    assert accounts.lock_remaining(ACCOUNT) == 0


def test_冷却按账号记_不影响别的账号():
    _register()
    _register(username="另一个账号")
    for _ in range(accounts.MAX_ATTEMPTS):
        _login(password="wrong-password")
    assert accounts.lock_remaining(ACCOUNT) > 0
    assert accounts.lock_remaining("另一个账号") == 0
    assert _login(username="另一个账号").status_code == 200
