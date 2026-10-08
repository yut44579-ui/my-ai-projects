"""accounts.py · 本地账号（注册 / 登录）—— 单机自用级别。

════════════════════════════════════════════════════════════════════════
【这是什么、明确不是什么】
════════════════════════════════════════════════════════════════════════
是：这台机器上的账号系统。注册一个账号、密码存成**不可还原的校验值**、登录时用它比对，
    比对通过才放行；账号信息与"上次登录时间"落 `state/accounts.json`。

不是（本轮明确不做，留在后续）：
    · 角色权限（谁能看什么、能改什么）
    · 多用户数据隔离（任务/数据源不分属某个账号 —— 本机自用，谁的都一样）
    · 令牌 / 刷新令牌 / 服务端会话 / SSO / 扫码登录
    所以**登录成功后前端拿到的只是一条"这次是谁"的记录**，不是一张通行证；
    后端接口也不按账号鉴权。这一点在页面上如实写着，不吹成"已安全登录"。

════════════════════════════════════════════════════════════════════════
【密码怎么存（这段是这个文件的重点）】
════════════════════════════════════════════════════════════════════════
用 Python **标准库** `hashlib.pbkdf2_hmac`，不引任何新依赖：

    校验值 = PBKDF2-HMAC-SHA256(明文密码, 每个账号独立的随机盐, 迭代次数)

    · 盐：`secrets.token_bytes(16)` —— 每个账号**各自一个**随机盐。
      为什么必须每账号独立：同一串密码在两个账号下会算出**不同**的校验值，
      拿着一张"常见密码 → 校验值"的表来反查（彩虹表）就失效了。
    · 迭代次数：写进记录的 `pwd_iterations`。将来提高迭代次数时，
      老账号按它自己记录里的次数照旧能登录，不用全体重设密码。
    · 比对：`hmac.compare_digest`（**定长比较**，不因为"第几个字符开始不一样"而提前返回）。
    · 明文密码在这个文件里只活在一次函数调用内，**不落盘、不进日志、不出现在响应里**。

⚠️ 老实说清这套东西的强度边界（不吹）：
    它挡的是"**有人翻到 accounts.json，想从文件里直接读出密码**"。
    它挡不住"**能在这台机器上跑程序的人**"—— 本地单机模式本来就没有对抗本机管理员的能力，
    真正的多用户隔离与权限校验是后端 + 数据库那一层的活（后续 TASK）。
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time

from app import state

# ── 摘要参数（改迭代次数时只改这里；**已经存过的账号按自己的记录走**）──────────
PWD_ALGO = "pbkdf2_sha256"
PWD_ITERATIONS = 200_000          # 2026 年的常规档位；单次比对约 0.1 秒，登录感知不到
PWD_SALT_BYTES = 16               # 128 位随机盐

# ── 账号状态机（**注册之后能不能直接登录，看"模式"，不看账号名**）────────────
#   pending   刚注册，等管理员批准           → 登录时明确说"正在等待批准"
#   active    可登录
#   rejected  管理员拒绝                     → 登录时明确说"未通过审批"
#   disabled  批准过，后来被停用             → 登录时明确说"已被停用"
#
# ⚠️ `pending` 只出现在**真实审批流模式**（`SRA_REQUIRE_APPROVAL=1`）下；默认的
#    本机单用户模式注册即 `active`（见下面 MODE 那一节）。`pending` 这个状态**保留不动** ——
#    审批流的每一行代码、每一句话都还在，只是默认不走那条路。
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REJECTED = "rejected"
STATUS_DISABLED = "disabled"
STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_REJECTED, STATUS_DISABLED)

# ── 角色（只分两种：管理员能批准账号，普通账号不能）──────────────────────────
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

# 评审动作
REVIEW_APPROVE = "approve"
REVIEW_REJECT = "reject"
REVIEW_DISABLE = "disable"
REVIEW_ENABLE = "enable"
REVIEW_ACTIONS = (REVIEW_APPROVE, REVIEW_REJECT, REVIEW_DISABLE, REVIEW_ENABLE)

# 登录失败后的那句人话（三种情况各不相同，用户要求能逐个分辨）
STATUS_MESSAGE = {
    STATUS_PENDING: "账号正在等待管理员批准，批准之后就能登录。",
    STATUS_REJECTED: "该账号未通过审批，无法登录。",
    STATUS_DISABLED: "该账号已被停用，请联系管理员。",
}
# 状态的中文说法（给前端提示用；界面上不出现英文状态码）
STATUS_TEXT = {
    STATUS_PENDING: "等待批准",
    STATUS_ACTIVE: "正常",
    STATUS_REJECTED: "未通过审批",
    STATUS_DISABLED: "已停用",
}
STATUS_ERROR_CODE = {
    STATUS_PENDING: "account_pending",
    STATUS_REJECTED: "account_rejected",
    STATUS_DISABLED: "account_disabled",
}

# ════════════════════════════════════════════════════════════════════════
# 模式：本机单用户（默认）vs 真实审批流（FR-002A）
# ════════════════════════════════════════════════════════════════════════
# 要解决的问题（用户原话）：
#     「我这边是管理员，你不要把我的账号以及密码，在正确的情况下或者注册的情况后，
#      让我还要去通过审批。我找谁审批？**我自己弄的，我还要去找别人审批啊**」
#
# 做法：**要不要审批由模式决定**。不是再造一个"能绕过审批的管理员"，
#      也不用 admin 这个名字 / IP / localhost / 浏览器 Cookie 去证明"你是本机所有者"
#      —— 那些都不构成所有权证明，而且那样做等于又造了一套登录系统。
#
#     SRA_REQUIRE_APPROVAL 未设 / 空 / 0（**默认 = 本机单用户**）
#           注册即生效：第一个账号 active+admin，其余 active+user —— 谁都不用批。
#     SRA_REQUIRE_APPROVAL = 1（真实审批流）
#           第一个账号仍是 active+admin（否则没人能按下"批准"），其余 pending，
#           等一个已生效的管理员批准。
#
# 读法：**调用时现读**（不缓存），所以测试里 `monkeypatch.setenv` 就能切换模式 ——
#      与项目里其它开关（`SRA_STATE_DIR` 等）同一套做法（见 tests/ 的隔离 fixture）。
ENV_REQUIRE_APPROVAL = "SRA_REQUIRE_APPROVAL"
_TRUTHY = ("1", "true", "yes", "on")


def require_approval() -> bool:
    """当前是不是**真实审批流**模式（未设 / 空 / 0 → False = 本机单用户）。

    只认写得明白的值。没写、或者写了不认识的东西（例如手滑的 `ture`）一律按
    **默认的本机单用户模式**走 —— 默认值就是"注册即生效"，
    配置写错时用户照样进得去，而不是被一句看不懂的开关锁在门外。
    """
    return (os.environ.get(ENV_REQUIRE_APPROVAL) or "").strip().casefold() in _TRUTHY

# ── 本机会话（登录后发一个编号，用来回答"这次管理动作是谁按的"）──────────────
# ⚠️ 边界写清楚（不许被当成企业级令牌）：它在**内存**里、重启服务即全部失效、
# 没有签名也没有权限范围 —— 它只回答"这台机器上这次登录的是谁"。
# 前端把它和"这次是谁"一起记在本机（退出登录时一并清掉）。
SESSION_BYTES = 24
# session_id -> (账号名, 建立时间戳, 是不是"临时密码登录、必须先改密")
# 第三个元素是 FR-001A 加的：临时密码登录建立的会话**只准改密码与退出登录**，
# 别的接口一律 403（评审 D7/D8）。它跟着会话走、不落盘 —— 服务重启会话全没，
# 那条"必须先改密"的状态也就一起没了，fail-closed。
_sessions: dict[str, tuple[str, float, bool]] = {}
_session_lock = threading.Lock()

# ── 账号 / 密码的长度与字符口径（与前端表单上的提示语同一套数字）─────────────
USERNAME_MIN = 2
USERNAME_MAX = 40
PASSWORD_MIN = 6
PASSWORD_MAX = 128                # 上限是**防止**有人拿超长密码来拖垮比对（不是嫌长）
DISPLAY_MAX = 40

# 账号允许：中文 / 字母 / 数字 / 下划线 / 点 / 中划线。
# 为什么**不**允许空格与其它符号：账号是要被人念出来、抄下来的东西，
# "空格、引号、全角标点"这类字符只会让"我登不上"变成查半天的问题。
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-一-鿿]+$")   # 一-鿿 = 常用汉字


class AccountError(RuntimeError):
    """账号层的可预期错误（HTTP 状态码由 api_auth.py 映射，消息本来就是人话）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 连续尝试失败 → 冷却（**按账号**记，进程内）
# ════════════════════════════════════════════════════════════════════════
# 为什么要有：验证码把"脚本批量试密码"挡在门外，但**手工**试密码还是能一直试。
#   连着错 N 次之后冷却一分钟 —— 这才是真正让"撞库"变得不划算的那道闸门。
# 记的是**账号名**（不是 IP）：本机单用户，没有 IP 可谈；按账号记也正好保护"某个账号被盯着试"。
# 什么算一次失败：验证码填错、账号或密码不对。**验证码过期不算**（那是"页面放太久了"，
#   不是"有人在猜"，前端会自动换一张让用户重输）。
# 成功一次即清零 —— 正常用户偶尔敲错一两次不会被记仇。
MAX_ATTEMPTS = 5
COOLDOWN_SECONDS = 60

_failures: dict[str, tuple[int, float]] = {}      # 账号名(casefold) -> (连续失败次数, 解锁时间戳)
_failure_lock = threading.Lock()


def _failure_key(username: str) -> str:
    return normalize_username(username).casefold()


def lock_remaining(username: str) -> int:
    """还要等多少秒才能再试（0 = 现在可以试）。向上取整到秒，给用户的话才好读。"""
    key = _failure_key(username)
    if not key:
        return 0
    now = time.time()
    with _failure_lock:
        entry = _failures.get(key)
        if entry is None:
            return 0
        count, locked_until = entry
        if locked_until <= now:
            if count >= MAX_ATTEMPTS:
                _failures.pop(key, None)          # 冷却结束：从头开始记
            return 0
        return int(math.ceil(locked_until - now))


def note_failure(username: str) -> int:
    """记一次失败；连着错满 MAX_ATTEMPTS 次就进冷却。返回当前连续失败次数。"""
    key = _failure_key(username)
    if not key:
        return 0
    now = time.time()
    with _failure_lock:
        count, locked_until = _failures.get(key, (0, 0.0))
        # 只有"进过冷却、而且冷却已经结束"才从头数。
        # （判据必须是 `locked_until` 非零 —— 平时的 0.0 意思是"没在冷却"，
        #   不是"冷却已经结束了"；写成 `locked_until <= now` 会把计数每次清零，永远数不到 5。）
        if locked_until and locked_until <= now:
            count = 0
        count += 1
        locked_until = (now + COOLDOWN_SECONDS) if count >= MAX_ATTEMPTS else 0.0
        _failures[key] = (count, locked_until)
    return count


def clear_failures(username: str) -> None:
    """登录成功：把连续失败清掉。"""
    key = _failure_key(username)
    if key:
        with _failure_lock:
            _failures.pop(key, None)


def reset_failures() -> None:
    """全清（测试隔离用；产品代码不需要调它）。"""
    with _failure_lock:
        _failures.clear()


# ════════════════════════════════════════════════════════════════════════
# 校验（只做"填错了"的判断，不做业务判断）
# ════════════════════════════════════════════════════════════════════════
def normalize_username(raw: str) -> str:
    """去掉首尾空白 —— 复制粘贴带进来的空格不该算进账号名。"""
    return (raw or "").strip()


def validate_username(username: str) -> str:
    """账号形态校验；返回规整过的账号名，不合格抛 AccountError。"""
    name = normalize_username(username)
    if len(name) < USERNAME_MIN:
        raise AccountError("username_too_short", f"账号至少 {USERNAME_MIN} 个字符。")
    if len(name) > USERNAME_MAX:
        raise AccountError("username_too_long", f"账号最多 {USERNAME_MAX} 个字符，短一点更好记。")
    if not USERNAME_PATTERN.match(name):
        raise AccountError(
            "username_invalid",
            "账号只能用中文、字母、数字和下划线、点、中划线（不能有空格）。",
        )
    return name


def validate_password(password: str) -> str:
    """密码长度校验（强度提示在前端，这里只守底线）。"""
    value = password or ""
    if len(value) < PASSWORD_MIN:
        raise AccountError("password_too_short", f"密码至少 {PASSWORD_MIN} 位。")
    if len(value) > PASSWORD_MAX:
        raise AccountError("password_too_long", f"密码最长 {PASSWORD_MAX} 位，太长了反而不方便。")
    return value


def normalize_display_name(raw: str | None, fallback: str) -> str:
    """显示名（顶栏那个名字）：留空就用账号名本身。"""
    name = (raw or "").strip()
    if not name:
        return fallback
    if len(name) > DISPLAY_MAX:
        raise AccountError("display_name_too_long", f"显示名最多 {DISPLAY_MAX} 个字符。")
    return name


# ════════════════════════════════════════════════════════════════════════
# 摘要与比对
# ════════════════════════════════════════════════════════════════════════
def derive(password: str, salt: bytes, iterations: int = PWD_ITERATIONS) -> str:
    """算校验值（十六进制字符串）。**这个函数的返回值可以进文件，入参不行。**"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def make_secret(password: str) -> dict:
    """给一个明文密码配一套（算法 / 盐 / 校验值 / 迭代次数）—— 注册时用。

    返回值直接进账号记录。**里面没有明文密码**，只有算出来的校验值。
    """
    salt = secrets.token_bytes(PWD_SALT_BYTES)
    return {
        "pwd_algo": PWD_ALGO,
        "pwd_salt": salt.hex(),
        "pwd_hash": derive(password, salt, PWD_ITERATIONS),
        "pwd_iterations": PWD_ITERATIONS,
    }


def secret_matches(record: dict, password: str) -> bool:
    """拿明文密码跟记录里的校验值对一下（对不上返回 False，不抛错）。"""
    if (record.get("pwd_algo") or "") != PWD_ALGO:
        return False                                     # 不认识的算法：一律当对不上
    try:
        salt = bytes.fromhex(record.get("pwd_salt") or "")
        iterations = int(record.get("pwd_iterations") or 0)
    except (TypeError, ValueError):
        return False                                     # 记录被手改坏了：不放行，也不崩
    if not salt or iterations <= 0:
        return False
    expected = record.get("pwd_hash") or ""
    return hmac.compare_digest(expected, derive(password, salt, iterations))


# ════════════════════════════════════════════════════════════════════════
# 临时密码（FR-001A：管理员人工恢复时发的那张一次性凭证）
# ════════════════════════════════════════════════════════════════════════
# 冻结口径（评审 Q5 + 裁决第 12 条）：
#   · 12 位（不是 8 位 —— 它是管理员发的高权限恢复凭证，没有理由做得更短）
#   · 用 secrets 生成，绝不用 random
#   · 30 分钟过期
#   · **只存哈希**（与正式密码同一套 PBKDF2 + 随机盐，复用上面的 derive/make_secret 思路）
#   · **首次成功登录立即消费**（single-use，见 login 的③）
#   · 明文只在签发那一次返回给管理员，不进日志、不进记录、不进响应 debug
TEMP_PWD_LENGTH = 12
TEMP_PWD_TTL_SECONDS = 1800       # 30 分钟
# 易读字符集：去掉 0/O/o、1/l/I 这些抄下来会认错的 —— 这东西是给人**念**或者**抄**的
TEMP_PWD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
TEMP_PWD_FIELDS = ("temp_pwd_algo", "temp_pwd_salt", "temp_pwd_hash",
                   "temp_pwd_iterations", "temp_pwd_expires_at", "temp_pwd_used_at")


def make_temp_password() -> str:
    """生成一串 12 位临时密码（用 `secrets`，不是 `random`）。"""
    return "".join(secrets.choice(TEMP_PWD_ALPHABET) for _ in range(TEMP_PWD_LENGTH))


def temp_secret(password: str, *, now: float) -> dict:
    """给临时密码配一套（算法 / 盐 / 校验值 / 迭代次数 / 过期时刻）。

    返回的键直接并进账号记录 —— 里面**没有明文**。与 `make_secret` 同一套摘要，
    不另造一种"临时密码专用算法"（少一套算法就少一处能写错的地方）。
    """
    salt = secrets.token_bytes(PWD_SALT_BYTES)
    return {
        "temp_pwd_algo": PWD_ALGO,
        "temp_pwd_salt": salt.hex(),
        "temp_pwd_hash": derive(password, salt, PWD_ITERATIONS),
        "temp_pwd_iterations": PWD_ITERATIONS,
        "temp_pwd_expires_at": now + TEMP_PWD_TTL_SECONDS,
        "temp_pwd_used_at": None,           # 还没被用过
    }


def temp_password_matches(record: dict, password: str, *, allow_used: bool = False) -> bool:
    """拿明文跟记录里的**临时密码**校验值对一下（对不上/没有/过期/已用 → False）。

    `allow_used=True` 只有一个调用点：临时密码登录进来的那条会话走 `/password/change`
    时，"原密码"填的就是那串临时密码 —— 它已经被消费掉了（`temp_pwd_used_at` 有值），
    但核对它的哈希是这个流程唯一能自证的方式（登录已经证明了持有人拿得到它）。
    别的任何地方都不许打开这个开关。
    """
    if not record.get("temp_pwd_hash"):
        return False
    if record.get("temp_pwd_used_at") and not allow_used:
        return False
    try:
        expires = float(record.get("temp_pwd_expires_at") or 0)
    except (TypeError, ValueError):
        return False
    if expires <= time.time():
        return False                                   # 过期：当作没有这个密码
    probe = {
        "pwd_algo": record.get("temp_pwd_algo"),
        "pwd_salt": record.get("temp_pwd_salt"),
        "pwd_hash": record.get("temp_pwd_hash"),
        "pwd_iterations": record.get("temp_pwd_iterations"),
    }
    return secret_matches(probe, password)


# ════════════════════════════════════════════════════════════════════════
# 本机会话（登录时发号；退出/重启即失效）
# ════════════════════════════════════════════════════════════════════════
def open_session(username: str, *, must_change: bool = False) -> str:
    """给这次登录发一个会话编号（新的登录不会踢掉旧的：同一台机器上多开几个页面很正常）。

    `must_change=True` 只由**临时密码登录**那条路用（FR-001A）：见 `session_info`。
    """
    session_id = secrets.token_hex(SESSION_BYTES)
    with _session_lock:
        _sessions[session_id] = (username, time.time(), bool(must_change))
    return session_id


def resolve_session(session_id: str | None) -> str | None:
    """按会话编号取账号名；取不到（假的/服务重启过了）返回 None。"""
    if not session_id:
        return None
    with _session_lock:
        found = _sessions.get(str(session_id))
    return found[0] if found else None


def session_info(session_id: str | None) -> dict | None:
    """按会话编号取**这条会话的全部事实**（账号名 + 要不要先改密）。

    为什么单开一个函数而不是把 `resolve_session` 改成返回字典：那个函数的调用点
    到处都是（既要能少改就少改），而"这条会话有没有 must_change"只有守卫在乎。
    """
    if not session_id:
        return None
    with _session_lock:
        found = _sessions.get(str(session_id))
    if not found:
        return None
    return {"username": found[0], "created_at": found[1], "must_change": bool(found[2])}


def close_sessions_for(username: str) -> int:
    """把一个账号的**全部**会话作废，返回作废了几条。

    什么时候必须调（评审 15/16 两条冻结项）：
        · 密码改成功了 —— 密码变了 = 认证状态重建，旧会话一律不作数；
        · 走恢复码把密码重置了 —— 同理，而且要防"别人正拿着旧会话"。
    匹配口径与账号表一致（大小写不敏感、去首尾空白）。
    """
    wanted = normalize_username(username).casefold()
    if not wanted:
        return 0
    with _session_lock:
        doomed = [key for key, value in _sessions.items()
                  if str(value[0] or "").strip().casefold() == wanted]
        for key in doomed:
            _sessions.pop(key, None)
    return len(doomed)


def close_session(session_id: str | None) -> bool:
    """退出登录：把这条会话清掉（清不到也算成功 —— 本来就没有的更没什么可清）。"""
    if not session_id:
        return False
    with _session_lock:
        return _sessions.pop(str(session_id), None) is not None


def session_count() -> int:
    """当前活着的会话数（排查用，不进任何响应体）。"""
    with _session_lock:
        return len(_sessions)


def reset_sessions() -> None:
    """清空全部会话（测试隔离用；服务重启本来就会清）。"""
    with _session_lock:
        _sessions.clear()


# ════════════════════════════════════════════════════════════════════════
# 会话守卫（FR-001A：把"谁在调"与"他现在被允许调什么"收在一处）
# ════════════════════════════════════════════════════════════════════════
# 评审（D7/D8/E5）要的是一条**服务端**的统一闸门：
#
#     session 已认证
#          ↓
#     must_change_password?
#          ├─ 否 → 正常
#          └─ 是 → 只放行 /api/auth/password/change 与 /api/auth/logout，其余一律 403
#
# 为什么必须在这里、而不是各个端点自己判：判漏一处就是一条越权路径。
# 前端不许参与这个判断（评审 F：JS 不许判 must_change / admin / ticket）——
# 页面只负责"把状态显示出来 + 把凭证递过来"，能不能做由这里说了算。
def require_session(session_id: str | None) -> dict:
    """要求一条有效会话，返回 `{account, session}`（账号记录 + 这条会话的事实）。

    失败两种（前端要能分辨"重新登录"和"你得先改密码"）：
        session_invalid      没登录 / 会话失效（服务重启过）→ 401
        must_change_password 临时密码登录进来的，还没改密 → 403（见 require_session_any）
    """
    return require_session_any(session_id, allow_must_change=False)


def require_session_any(session_id: str | None, *, allow_must_change: bool) -> dict:
    """`require_session` 的实现。

    `allow_must_change=True` 只给两条路用：`/api/auth/password/change`（不改密就永远出不去）
    与 `/api/auth/logout`（连退都不让退就等于把用户锁死在半路上）。
    """
    info = session_info(session_id)
    if info is None:
        raise AccountError("session_invalid", "这个操作需要重新登录。")
    if info["must_change"] and not allow_must_change:
        raise AccountError(
            "must_change_password",
            "这个账号是用临时密码登录的，必须先在「修改密码」里设置新密码，之后才能使用其它功能。",
        )
    record = state.get_account(info["username"])
    if record is None:
        # 会话还在、账号没了（管理员把账号删了）→ 会话作废，不算"服务器坏了"
        close_session(session_id)
        raise AccountError("session_invalid", "这个操作需要重新登录。")
    return {"account": record, "session": info}


# ════════════════════════════════════════════════════════════════════════
# 对外视图（**绝不含 pwd_salt / pwd_hash**）
# ════════════════════════════════════════════════════════════════════════
PUBLIC_FIELDS = ("username", "display_name", "status", "role",
                 "created_at", "last_login_at", "reviewed_at", "reviewed_by")


def public(record: dict) -> dict:
    """把账号记录裁成**可以出给前端**的那几个字段。

    白名单而不是黑名单（不是"删掉 pwd_hash 再返回"）：将来记录里多一个敏感的字段，
    黑名单会漏出去，白名单不会。这是账号系统里唯一一条"多写一个字段就出事"的地方。
    """
    return {field: record.get(field) for field in PUBLIC_FIELDS}


# ════════════════════════════════════════════════════════════════════════
# 注册 / 登录 / 查重
# ════════════════════════════════════════════════════════════════════════
def register(username: str, password: str, display_name: str | None = None) -> dict:
    """注册一个账号，返回**可出给前端**的账号信息。

    重复账号 → AccountError("username_taken")（api_auth.py 映射成 409）。
    重复判断在落盘之前，且账号名大小写不敏感（"Tangyu" 与 "tangyu" 算同一个）。

    ★ **注册完之后能不能直接登录，由模式决定**（见上面 MODE 那一节）：
      · 本机单用户（默认）→ **注册即生效**：第一个账号 `admin`+`active`，
        其余 `active`+`user` —— 不需要任何人批准；
      · 真实审批流（`SRA_REQUIRE_APPROVAL=1`）→ 新账号 `pending`，等管理员批准。
        唯一的例外仍然是**第一个账号**（"系统里一个管理员都没有"）：它直接是
        `admin` + `active` —— 否则没人能按下"批准"，系统当场死锁。
    """
    name = validate_username(username)
    secret = password or ""
    if not secret:
        raise AccountError("password_too_short", f"密码至少 {PASSWORD_MIN} 位。")
    if state.get_account(name) is not None:
        raise AccountError("username_taken", "这个账号已被注册，换一个吧。")
    validated = validate_password(secret)                # 校验通过后才算校验值（别白算）
    first_one = not any(record.get("role") == ROLE_ADMIN for record in state.list_accounts())
    # 只有**真实审批流**模式下、且不是第一个账号，才落在 pending 上等批准
    waiting = require_approval() and not first_one
    if waiting:
        reviewed_by = reviewed_at = None                 # 还没人批过
    elif first_one:
        reviewed_by, reviewed_at = "（系统：第一个注册的账号自动成为管理员）", state.now_iso()
    else:
        reviewed_by, reviewed_at = REVIEWED_BY_LOCAL_MODE, state.now_iso()
    record = {
        "username": name,
        "display_name": normalize_display_name(display_name, name),
        "status": STATUS_PENDING if waiting else STATUS_ACTIVE,
        "role": ROLE_ADMIN if first_one else ROLE_USER,
        "created_at": state.now_iso(),
        "last_login_at": None,                           # 刚注册还没登录过
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewed_by,
        **make_secret(validated),
    }
    return public(state.record_account(record))


def is_admin(record: dict | None) -> bool:
    """是不是管理员（角色 + 账号可用，两个都要满足）。"""
    return bool(record) and record.get("role") == ROLE_ADMIN \
        and record.get("status") == STATUS_ACTIVE


def admin_count() -> int:
    """还有几个可用的管理员（用于"别把最后一个管理员停掉"这条保护）。"""
    return sum(1 for record in state.list_accounts() if is_admin(record))


# ════════════════════════════════════════════════════════════════════════
# 启动迁移（FR-002A）：把历史上"卡在 pending、又没人能批"的账号解开
# ════════════════════════════════════════════════════════════════════════
# 真实发生过的事（用户实测复现）：
#     库里已经有一个 active 的管理员（早期测试账号，密码用户不知道），
#     用户在实例上注册的账号于是变成 `pending` —— **没有任何人能批准它**，
#     用户被自己的系统锁在门外。
#
# 服务每次启动跑一次（幂等）。四条硬要求（评审冻结，一条都不许放宽）：
#     · 幂等、可重复执行       —— 已经是 active 的账号一律跳过：不改字段、不刷 mtime、
#                                不重复写审计（`set_account_status` 自己就"没变不写盘"）
#     · 留痕                   —— 写进 `reviewed_by` / `reviewed_at`（沿用审批的那两个字段，
#                                前端"账号管理"里直接看得见是迁移来的）
#     · 不改密码 / 不删账号 / 不重置权限 —— 本函数**从头到尾没有碰过 pwd_\* 一个字节**
#     · 不产生第二个 admin     —— 最多只提**一个**账号成 admin（规则②）；
#                                只要库里已经有可用管理员，谁的角色都不会被改
#
# 两条规则：
#     ① 库里已有可用 admin     → pending 升 active，role 保持 user
#     ② 库里没有任何可用 admin → 最早的那个账号升 admin+active（否则连"谁批账号"都没人），
#                                其余 pending 升 active
# ⚠️ **真实审批流模式（SRA_REQUIRE_APPROVAL=1）下不替管理员按"批准"**：
#     有可用管理员时迁移什么都不做（pending 本来就该等管理员批）；只有走到规则②
#     ——"系统里一个可用管理员都没有"——才动手，因为那时不是"该不该批某人"，
#     而是整个系统已经没人能批账号了（死锁）。
REVIEWED_BY_MIGRATED = "（本机单用户模式：启动迁移自动激活）"
REVIEWED_BY_BOOTSTRAP = "（启动迁移：系统里没有任何可用管理员，最早的账号自动成为管理员）"
REVIEWED_BY_LOCAL_MODE = "（本机单用户模式：注册即生效，无需审批）"


def _say(message: str) -> None:
    """往启动日志写一行（**失败也不许抛**：stdout 编码/关闭都会让 print 炸）。"""
    try:
        print(message, flush=True)
    except Exception:
        pass


def _oldest_first(records: list[dict]) -> list[dict]:
    """按"谁先注册的"排（早的在前）—— 规则②里"最早的账号"就是指这个顺序。

    `created_at` 是 ISO 字符串，直接比大小就是时间先后。万一手改的记录没有 created_at，
    它按 "" 参与排序（排在所有带时间的记录前面），并列时看**记录在文件里的位置**：
    账号文件是"新的在前"，所以位置越靠后 = 注册得越早。
    """
    indexed = sorted(enumerate(records),
                     key=lambda pair: (str(pair[1].get("created_at") or ""), -pair[0]))
    return [record for _, record in indexed]


def migrate_accounts() -> dict:
    """启动迁移（幂等）。返回一份**给人看**的报告 —— 只进启动日志，不进任何响应体。

    读不出账号文件时**不迁移**并如实记下原因：迁移是来帮忙的，不是服务能起来的前提
    （宁可维持现状，也不能让一次迁移把服务拦在门外）。
    """
    report: dict = {"scanned": 0, "activated": [], "promoted": None, "skipped": None,
                    "approval_mode": require_approval(), "error": None, "changed": False}
    try:
        records = list(state.list_accounts())
    except Exception as exc:                             # noqa: BLE001 —— 见 docstring
        report["error"] = f"{type(exc).__name__}: {exc}"
        _say(f"[账号迁移] 读账号表失败，本次不迁移（服务照常启动）：{report['error']}")
        return report
    report["scanned"] = len(records)
    if not records:
        return report

    def name_of(record: dict) -> str:
        return str(record.get("username") or "")

    def status_of(record: dict) -> str:
        # 老记录（这一版之前建的）没有 status → 当作可用 —— 与 login 同一口径
        return str(record.get("status") or STATUS_ACTIVE)

    def activate(record: dict) -> None:
        name = name_of(record)
        if name and state.set_account_status(name, STATUS_ACTIVE,
                                             REVIEWED_BY_MIGRATED, state.now_iso()):
            report["activated"].append(name)

    pending = _oldest_first([r for r in records if status_of(r) == STATUS_PENDING])
    promoted = None
    if any(is_admin(r) for r in records):
        # 规则①：有人能批账号 —— 只把 pending 收进本机单用户模式（真实审批流下不动）
        if report["approval_mode"]:
            report["skipped"] = "已有可用管理员：真实审批流模式下不自动激活（等管理员批）"
    else:
        # 规则②：一个可用管理员都没有 —— 必须捞出一个人，否则没人能批账号
        # 优先"本来就写着 admin 的"记录（手改/老数据），这样不会多出一个 admin
        existing_admin = _oldest_first([r for r in records if r.get("role") == ROLE_ADMIN])
        usable = _oldest_first([r for r in records if status_of(r) in (STATUS_PENDING, STATUS_ACTIVE)])
        target = (existing_admin or usable or [None])[0]
        if target is None:
            # 全是被拒绝 / 被停用的账号：那是有人按下的决定，迁移**不复活**它们（不猜）
            report["skipped"] = "没有任何可用账号可以提成管理员（剩下的都是拒绝/停用状态）"
        else:
            promoted = name_of(target)
            if target.get("role") == ROLE_ADMIN:
                state.set_account_status(promoted, STATUS_ACTIVE,
                                         REVIEWED_BY_BOOTSTRAP, state.now_iso())
            else:
                state.update_account_fields(promoted, {
                    "role": ROLE_ADMIN, "status": STATUS_ACTIVE,
                    "reviewed_by": REVIEWED_BY_BOOTSTRAP, "reviewed_at": state.now_iso()})
            report["promoted"] = promoted

    if not report["approval_mode"]:                      # 本机单用户：pending 一律收进来
        for record in pending:
            if name_of(record) != promoted:
                activate(record)
    report["changed"] = bool(report["activated"] or report["promoted"])
    if report["changed"] or report["skipped"]:
        _say(f"[账号迁移] 扫描 {report['scanned']} 个账号 → "
             f"激活 {len(report['activated'])} 个{report['activated'] or ''}"
             + (f"，提为管理员 {report['promoted']}" if report["promoted"] else "")
             + (f"；未动：{report['skipped']}" if report["skipped"] else ""))
    return report


def require_admin(session_id: str | None) -> dict:
    """管理动作的守卫：会话 → 账号 → 必须是管理员。返回那个管理员账号。

    三种失败分开给话（前端要能分辨"你该重新登录"和"你没这个权限"）：
        session_invalid      → 没登录 / 会话失效（服务重启过）
        must_change_password → 临时密码登录进来的、还没改密（FR-001A：先改密，别的都免谈）
        not_admin            → 登录了，但不是管理员
    """
    guarded = require_session(session_id)          # 会话 + must_change 两道闸门先过
    record = guarded["account"]
    if not is_admin(record):
        raise AccountError("not_admin", "只有管理员能管理账号。")
    return record


def list_accounts() -> list[dict]:
    """全部账号（按注册时间倒序，新的在前）——**只有管理动作会调它**，出参走白名单。"""
    records = list(state.list_accounts())
    records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
    return [public(record) for record in records]


def review(username: str, action: str, actor: str) -> dict:
    """管理员批准 / 拒绝 / 停用 / 恢复一个账号。返回更新后的账号信息。

    保护两条（都是"别把自己锁在门外"）：
        · 管理员账号不能被拒绝 / 停用 —— 至少要留一个能批账号的人
        · 已经被拒绝或停用的账号，只有 enable 能把它救回来（approve 也行，等同于恢复）
    """
    if action not in REVIEW_ACTIONS:
        raise AccountError("review_action_invalid", "不认识的审批动作。")
    name = normalize_username(username)
    record = state.get_account(name)
    if record is None:
        raise AccountError("account_not_found", "找不到这个账号。")
    if record.get("role") == ROLE_ADMIN and action in (REVIEW_REJECT, REVIEW_DISABLE):
        raise AccountError("last_admin", "管理员账号不能被拒绝或停用（否则没人能批账号了）。")
    target = {
        REVIEW_APPROVE: STATUS_ACTIVE,
        REVIEW_REJECT: STATUS_REJECTED,
        REVIEW_DISABLE: STATUS_DISABLED,
        REVIEW_ENABLE: STATUS_ACTIVE,
    }[action]
    if record.get("status") == target:
        raise AccountError("review_no_change",
                           f"这个账号已经是「{STATUS_TEXT.get(target, target)}」了。")
    updated = state.set_account_status(record.get("username") or name, target,
                                       reviewed_by=actor, reviewed_at=state.now_iso())
    return public(updated or record)


def delete_account(username: str) -> dict:
    """管理员**彻底删掉**一个账号，返回被删掉的那条（出参已过白名单）。

    与 `review(..., disable)` 的区别写在这里，别混：
        · 停用 → 记录还在，随时能恢复，登录时说"该账号已被停用"；
        · 删除 → 记录消失，**这个账号名随即可以被重新注册**（这是个不可逆动作）。

    保护规矩（和"不能拒绝/停用管理员"同一条思路：别把能批账号的人弄没了）：
        · 管理员账号只有在**还有别的可用管理员**时才让删
          —— 否则系统会退化成"谁都不能批账号"，而第一个账号又是自动管理员，
            那条"第一个注册的自动成为管理员"的救命规则只在**一个账号都没有**时才会触发，
            真删空了就得删账号文件才能恢复。宁可不许删，也不让人走到那一步。
        · 删自己也不行：删完这条会话对应的账号就没了（要退出登录重新注册一个）。
    """
    name = normalize_username(username)
    record = state.get_account(name)
    if record is None:
        raise AccountError("account_not_found", "找不到这个账号。")
    if record.get("role") == ROLE_ADMIN and admin_count() <= 1:
        raise AccountError("last_admin", "这是唯一的管理员账号，删掉就没人能批账号了。")
    removed = state.remove_account(record.get("username") or name)
    return public(removed or record)


def login(username: str, password: str) -> dict:
    """登录校验；成功了才刷新"上次登录时间"，返回**可出给前端**的账号信息。

    失败一律 AccountError("bad_credentials")：**账号不存在**与**密码不对**给的是同一句话、
    同一个状态码 —— 否则就成了"这个账号存在吗"的探针（撞库的人最喜欢这种提示）。
    账号不存在时也照样算一次校验值（见下面注释），让两条路的耗时看起来一样。

    连着错满 MAX_ATTEMPTS 次之后进冷却：这时抛的是另一条错误（"尝试次数过多"），
    这是**故意让人看出区别**的 —— 用户得知道"再等一会儿"，而不是继续对着正确的密码怀疑自己。

    ★ FR-001A 加的一条支路（评审允许的唯一一处 login 改动）：
      管理员发的**临时密码**也能登录，但它建立的是"必须先改密"的会话 ——
      返回里的 `must_change_password=true`，之后除 `/password/change` 与 `/logout`
      以外一律 403（守卫在 `require_session`，不在前端）。
      临时密码在**这次成功登录时当场消费**（评审 ⑥ 与裁决第 12 条）：
      并发来十个登录请求也只有一个能成功，其余一律当"密码不对"。
    """
    name = normalize_username(username)
    remaining = lock_remaining(name)
    if remaining > 0:
        raise AccountError("too_many_attempts", f"尝试次数过多，请 {remaining} 秒后再试。")
    record = state.get_account(name) if name else None
    if record is None:
        # 占位比对：不为放行，只为让"账号不存在"与"密码不对"的耗时接近，
        # 单看响应时间猜不出账号在不在（本地模式下这不值一提，但这是习惯问题）。
        derive(password or "", b"\x00" * PWD_SALT_BYTES, PWD_ITERATIONS)
        note_failure(name)
        raise AccountError("bad_credentials", "账号或密码不对。")
    # ① 先看**正式密码**（绝大多数登录走这条）
    real_ok = secret_matches(record, password or "")
    # ② 正式密码不对时才看临时密码 —— 这里只"看一眼"，**不消费**：
    #    万一这个账号状态不允许登录（停用/未审批），临时密码不该被白白用掉。
    temp_ok = (not real_ok) and temp_password_matches(record, password or "")
    if not (real_ok or temp_ok):
        note_failure(name)
        raise AccountError("bad_credentials", "账号或密码不对。")
    # 密码对上了，**再看这个账号能不能登录**。
    # 顺序很重要：先密码后状态 —— 否则"待审批/未通过"这两种话就成了"这个账号存在吗"的探针，
    # 谁都能拿一个账号名来试出状态；现在只有**密码正确的人**才看得到自己账号的状态。
    status = record.get("status") or STATUS_ACTIVE      # 老记录（这一版之前建的）没有 status → 当作可用
    if status != STATUS_ACTIVE:
        note_failure(name)
        raise AccountError(STATUS_ERROR_CODE.get(status, "account_disabled"),
                           STATUS_MESSAGE.get(status, "这个账号当前无法登录。"))
    if temp_ok:
        # ③ 原子消费：并发的第二个请求拿不到（评审 ⑥）
        stored_name = record.get("username") or name
        if not state.consume_account_temp_password(stored_name, state.now_iso()):
            note_failure(name)
            raise AccountError("bad_credentials", "账号或密码不对。")
    clear_failures(name)
    stored_name = record.get("username") or name
    updated = state.touch_account_login(stored_name, state.now_iso())
    account = public(updated or record)
    account["session_id"] = open_session(stored_name, must_change=temp_ok)  # 本机会话（见文件开头边界）
    # 这条**只对本次登录的调用方**有意义（"你这张会话现在只准改密码"），
    # 不是账号的属性，所以不进 PUBLIC_FIELDS、也不落盘。
    account["must_change_password"] = bool(temp_ok)
    return account


def change_password(username: str, old_password: str, new_password: str, *,
                    allow_temp_old: bool = False) -> str:
    """改密码：核对原密码 → 落新校验值 → 作废该账号**全部**旧会话 → 发一条**新**会话。

    冻结口径（评审 15：改密成功 = 认证状态重建）：
        · 旧会话一律作废（`close_sessions_for`）——包括正在用的这一条；
        · 换新盐、新校验值、重新按当前迭代次数算（`make_secret`）；
        · 顺带清掉临时密码那套字段：它已经完成使命，留着只是多一份可被攻击的哈希；
        · 返回**新会话编号**（前端要拿它接着用，否则用户改完密码就被自己踢下线了）。

    `allow_temp_old=True` 只由"临时密码登录 → 强制改密"那条路打开：那时用户手里的
    "原密码"就是那串临时密码（见 `temp_password_matches` 的说明）。
    """
    name = normalize_username(username)
    record = state.get_account(name) if name else None
    if record is None:
        raise AccountError("account_not_found", "找不到这个账号。")
    matched = secret_matches(record, old_password or "")
    if not matched and allow_temp_old:
        matched = temp_password_matches(record, old_password or "", allow_used=True)
    if not matched:
        raise AccountError("bad_credentials", "原密码不对。")
    validated = validate_password(new_password)
    stored_name = record.get("username") or name
    # 一次写盘：新校验值 + 把临时密码那一套字段删干净（值为 None = 删键）
    state.update_account_fields(stored_name, {
        **make_secret(validated),
        **{field: None for field in TEMP_PWD_FIELDS},
    })
    close_sessions_for(stored_name)                   # ★ 旧会话全部作废（含当前这条）
    return open_session(stored_name)                  # ★ 发一条新的正常会话


def exists(username: str) -> bool:
    """这个账号名是否已被注册（注册页失焦时查重、首次使用引导都用它）。"""
    name = normalize_username(username)
    return bool(name) and state.get_account(name) is not None


def account_count() -> int:
    """系统里一共有几个账号（0 个 = 首次使用，登录页要给"先注册一个"的引导）。"""
    return state.count_accounts()


__all__ = [
    "AccountError",
    "COOLDOWN_SECONDS",
    "ENV_REQUIRE_APPROVAL",
    "REVIEWED_BY_BOOTSTRAP",
    "REVIEWED_BY_LOCAL_MODE",
    "REVIEWED_BY_MIGRATED",
    "REVIEW_ACTIONS",
    "REVIEW_APPROVE",
    "REVIEW_DISABLE",
    "REVIEW_ENABLE",
    "REVIEW_REJECT",
    "ROLE_ADMIN",
    "ROLE_USER",
    "STATUSES",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_TEXT",
    "TEMP_PWD_FIELDS",
    "TEMP_PWD_LENGTH",
    "TEMP_PWD_TTL_SECONDS",
    "admin_count",
    "change_password",
    "close_session",
    "close_sessions_for",
    "is_admin",
    "list_accounts",
    "make_temp_password",
    "migrate_accounts",
    "open_session",
    "require_approval",
    "require_admin",
    "require_session",
    "require_session_any",
    "reset_sessions",
    "resolve_session",
    "review",
    "session_count",
    "session_info",
    "DISPLAY_MAX",
    "MAX_ATTEMPTS",
    "PASSWORD_MAX",
    "PASSWORD_MIN",
    "PUBLIC_FIELDS",
    "USERNAME_MAX",
    "USERNAME_MIN",
    "account_count",
    "clear_failures",
    "delete_account",
    "derive",
    "exists",
    "lock_remaining",
    "login",
    "make_secret",
    "normalize_display_name",
    "normalize_username",
    "note_failure",
    "public",
    "register",
    "reset_failures",
    "secret_matches",
    "temp_password_matches",
    "temp_secret",
    "validate_password",
    "validate_username",
]
