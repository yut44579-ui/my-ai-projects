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
import re
import secrets
import threading
import time

from app import state

# ── 摘要参数（改迭代次数时只改这里；**已经存过的账号按自己的记录走**）──────────
PWD_ALGO = "pbkdf2_sha256"
PWD_ITERATIONS = 200_000          # 2026 年的常规档位；单次比对约 0.1 秒，登录感知不到
PWD_SALT_BYTES = 16               # 128 位随机盐

# ── 账号状态机（**注册之后不能直接登录，要管理员批**）────────────────────────
#   pending   刚注册，等管理员批准           → 登录时明确说"正在等待批准"
#   active    可登录
#   rejected  管理员拒绝                     → 登录时明确说"未通过审批"
#   disabled  批准过，后来被停用             → 登录时明确说"已被停用"
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

# ── 本机会话（登录后发一个编号，用来回答"这次管理动作是谁按的"）──────────────
# ⚠️ 边界写清楚（不许被当成企业级令牌）：它在**内存**里、重启服务即全部失效、
# 没有签名也没有权限范围 —— 它只回答"这台机器上这次登录的是谁"。
# 前端把它和"这次是谁"一起记在本机（退出登录时一并清掉）。
SESSION_BYTES = 24
_sessions: dict[str, tuple[str, float]] = {}      # session_id -> (账号名, 建立时间戳)
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
# 本机会话（登录时发号；退出/重启即失效）
# ════════════════════════════════════════════════════════════════════════
def open_session(username: str) -> str:
    """给这次登录发一个会话编号（新的登录不会踢掉旧的：同一台机器上多开几个页面很正常）。"""
    session_id = secrets.token_hex(SESSION_BYTES)
    with _session_lock:
        _sessions[session_id] = (username, time.time())
    return session_id


def resolve_session(session_id: str | None) -> str | None:
    """按会话编号取账号名；取不到（假的/服务重启过了）返回 None。"""
    if not session_id:
        return None
    with _session_lock:
        found = _sessions.get(str(session_id))
    return found[0] if found else None


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

    ★ **注册之后不能直接登录**：新账号状态是 `pending`，等管理员批准。
      唯一的例外是**第一个账号**（也是"系统里一个管理员都没有"的情况）：它直接是
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
    record = {
        "username": name,
        "display_name": normalize_display_name(display_name, name),
        "status": STATUS_ACTIVE if first_one else STATUS_PENDING,
        "role": ROLE_ADMIN if first_one else ROLE_USER,
        "created_at": state.now_iso(),
        "last_login_at": None,                           # 刚注册还没登录过
        "reviewed_at": None,                             # 第一个账号不需要谁批
        "reviewed_by": "（系统：第一个注册的账号自动成为管理员）" if first_one else None,
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


def require_admin(session_id: str | None) -> dict:
    """管理动作的守卫：会话 → 账号 → 必须是管理员。返回那个管理员账号。

    三种失败分开给话（前端要能分辨"你该重新登录"和"你没这个权限"）：
        session_invalid → 没登录 / 会话失效（服务重启过）
        not_admin       → 登录了，但不是管理员
    """
    username = resolve_session(session_id)
    if not username:
        raise AccountError("session_invalid", "管理操作需要重新登录。")
    record = state.get_account(username)
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
    if not secret_matches(record, password or ""):
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
    clear_failures(name)
    updated = state.touch_account_login(record.get("username") or name, state.now_iso())
    account = public(updated or record)
    account["session_id"] = open_session(record.get("username") or name)   # 本机会话（见文件开头边界）
    return account


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
    "admin_count",
    "close_session",
    "is_admin",
    "list_accounts",
    "open_session",
    "require_admin",
    "reset_sessions",
    "resolve_session",
    "review",
    "session_count",
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
    "validate_password",
    "validate_username",
]
