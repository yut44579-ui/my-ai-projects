"""recovery.py · 密码恢复闭环（FR-001A）—— 恢复码 / 一次性票据 / 管理员临时密码。

════════════════════════════════════════════════════════════════════════
【这个文件解决的真实问题】
════════════════════════════════════════════════════════════════════════
用户原话：「忘记密码之后无法找回，加上具体功能即可以通过忘记密码来**新增新密码**，
但是**需要验证账户**」。

上一版的「忘记密码？」只弹一句"本机没法找回" —— 那不是能力，那是一句免责声明。
本文件把"真能重置"这条链补齐，并且**只补这一条链**（不许扩成邮箱/短信/密保问题）。

════════════════════════════════════════════════════════════════════════
【★ 三层授权必须严格分离（这是本 TASK 的灵魂，写死在代码里）】
════════════════════════════════════════════════════════════════════════
    challenge（图形验证码 / 将来的滑块）→ 只允许【进入恢复流程】      app/challenge.py
    recovery code（20 位恢复码）        → 证明【账户所有权】         本文件的 verify_reset
    ticket（一次性票据）                → 只允许【修改密码】         本文件的 commit_reset

绝对禁止的两件事（评审点名，都在别的实现里出现过）：
    ❌ `username + captcha = ownership`（把用户名与验证码当成所有权证明 = 直接账户接管）
    ❌ `reset/request 发现是老账号就直接签发恢复码`（同上，换了个马甲）
所以本文件里 `request_reset` **不会**生成、也不会返回任何恢复码 ——
它只签发一张**绑定在挑战上、绑定在（可能为空的）账户上**的 reset_token。

════════════════════════════════════════════════════════════════════════
【老账号怎么走（评审 Q3 冻结）】
════════════════════════════════════════════════════════════════════════
    能登录的用户        → 登录后生成恢复码（旧码立即失效、新码生效）
    忘记密码且没有恢复码 → **不能自助补发** → 走管理员临时密码
                        → 用临时密码登录 → 强制改密 → 自己生成恢复码
用户没有恢复码时，`verify` 只会得到一句"恢复码不对或已失效"（**不区分**"没有码"和"码不对"）。

════════════════════════════════════════════════════════════════════════
【存哪儿、活多久（评审 Q2：全部内存，重启全失效 = fail-closed）】
════════════════════════════════════════════════════════════════════════
    reset_token → 内存，10 分钟，单次使用，只存 SHA-256(token)
    ticket      → 内存， 5 分钟，单次使用，只存 SHA-256(ticket)
    session     → 内存（在 app/accounts.py），重启失效
    recovery code / 临时密码 → **落盘**（它们必须活过重启），但只存哈希
不落盘的三个都是"流程中的临时状态"，重启即失效正是想要的行为；
落盘的两个是"用户以后还要用的凭证"，所以必须持久化 —— 但同样只存哈希。

`recovery code` 的哈希是 SHA-256（它是 100 bit 的随机串，不存在"被猜出来"的字典攻击面，
不需要 PBKDF2 那种慢哈希）；`临时密码` 复用正式密码那套 PBKDF2（见 app/accounts.py）。

════════════════════════════════════════════════════════════════════════
【本文件必须堵死的并发口子（评审新发现的 6 类里占了 5 类）】
════════════════════════════════════════════════════════════════════════
    ① ticket 不是"半授权 token"：它**内部**绑定 account_id，commit 只收 ticket + 新密码
    ② reset_token × challenge 交叉绑定：token.challenge_id 必须等于本次提交的 challenge_id
    ③ 恢复码轮换是**原子替换**（走 Repository 的进程内锁，见 state.update_account_fields）
    ④ 恢复码 verify 的"检查 + 消费"是**一个临界区**（见 state.consume_account_recovery_code）
    ⑤ ticket 并发重放：check → consume → 改密码 是一条单次消费路径（本文件 pop 掉才继续）
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import threading
import time

from app import accounts, challenge, state

# ════════════════════════════════════════════════════════════════════════
# 参数（评审冻结，别改）
# ════════════════════════════════════════════════════════════════════════
# ── 恢复码 ─────────────────────────────────────────────────────────────
GROUP_LENGTH = 4                     # 每组 4 个字符
GROUP_COUNT = 5                      # 5 组 = 20 个字符（4-4-4-4-4）
CODE_LENGTH = GROUP_LENGTH * GROUP_COUNT        # 20
# 32 个符号 × 20 位 = 100 bit。字符集按"给人抄在纸上"来选（Crockford Base32 那一套）：
# 数字 10 个 + 字母 22 个，**去掉 I / L / O / U** —— I 与 1、O 与 0、L 与 1 在纸上分不清，
# U 是为了避免拼出脏词造成"用户不好意思念出来"。留下的 0 与 1 因此不会跟谁撞脸。
# ⚠️ 必须正好 32 个：这是"20 位 = 100 bit"的那个 32（少了它，熵就不够）。
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
assert len(RECOVERY_ALPHABET) == 32, "恢复码字符集必须是 32 个（20×5=100 bit 靠它撑着）"

# ── 两个一次性凭证 ─────────────────────────────────────────────────────
RESET_TOKEN_TTL_SECONDS = 600        # reset_token：10 分钟
TICKET_TTL_SECONDS = 300             # ticket：5 分钟
TOKEN_BYTES = 32                     # secrets.token_urlsafe(32) ≈ 256 bit

# ── 恢复码失败：**独立**计数（评审 Q4-3：绝不许污染登录失败计数）──────────
RECOVERY_MAX_ATTEMPTS = 5
RECOVERY_COOLDOWN_SECONDS = 60

# ── 提示语（**不泄露任何存在性**：账号在不在、有没有恢复码、是不是被停用，全说这一句）──
GENERIC_RESET_MESSAGE = "恢复码不对或已失效。请检查后重新输入；如果你没有恢复码，请联系管理员。"
GENERIC_REQUEST_MESSAGE = "如果账户信息有效且满足恢复条件，将继续下一步。"


class RecoveryError(RuntimeError):
    """恢复层的可预期错误（HTTP 状态码由 api_auth.py 映射，消息本来就是人话）。

    ⚠️ 这里的所有消息都是**固定字符串**：绝不许把 token / 恢复码 / 临时密码拼进消息里
    —— 异常消息会被写进日志、也会被返回给调用方（评审 ⑩：日志泄露的入口常常就在这里）。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 恢复码：生成 / 规整 / 摘要
# ════════════════════════════════════════════════════════════════════════
def _digest(text: str) -> str:
    """统一的一套摘要：SHA-256 十六进制。

    reset_token / ticket / 恢复码 三样都用它 —— 三样都不需要"慢哈希"
    （它们是密码学随机串，没有可被字典攻击的口令空间），要的是"进程内存被 dump 也读不回原文"。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_recovery_code(raw: str | None) -> str:
    """把用户抄下来的恢复码规整成一个可比较的形态：**大写 + 去掉一切分隔符**。

    允许的写法（评审 Q6 点名要同时支持）：
        J7KD-X4PM-Q8TW-2NFC   与   J7KD X4PM Q8TW 2NFC   与   j7kdx4pmq8tw2nfc

    ★ 前端也会做一次同样的规整（那是**体验**：让用户看到自己输对了），
      但**服务端这一遍绝不能省** —— 只在前端做等于没有做（改个请求就绕过去了）。
    """
    text = str(raw or "").upper()
    return re.sub(r"[^0-9A-Z]", "", text)              # 空格 / 连字符 / 标点全部丢掉


def make_recovery_code() -> str:
    """生成一串新的恢复码（分组形态，如 `J7KD-X4PM-Q8TW-2NFC-9H3M`）。用 secrets，不是 random。"""
    chars = [secrets.choice(RECOVERY_ALPHABET) for _ in range(CODE_LENGTH)]
    return "-".join("".join(chars[i:i + GROUP_LENGTH])
                    for i in range(0, CODE_LENGTH, GROUP_LENGTH))


def code_hash(raw_code: str | None) -> str:
    """恢复码的落盘形态（先规整、再 SHA-256）。"""
    return _digest(normalize_recovery_code(raw_code))


# ════════════════════════════════════════════════════════════════════════
# 恢复码失败计数（**与登录失败计数完全分开的两本账**）
# ════════════════════════════════════════════════════════════════════════
# 为什么要分开（评审 Q4-3 的原话）：如果恢复失败也去记登录失败，攻击者只要拿着
# 一个已知用户名猛打恢复接口，就能把这个人**锁在登录页外面** —— 用一个不需要密码的
# 接口造成拒绝服务。两本账、两套阈值，各管各的。
_failures: dict[str, tuple[int, float]] = {}      # 账号名(casefold) -> (连续失败次数, 解锁时间戳)
_failure_lock = threading.Lock()


def _failure_key(username: str | None) -> str:
    return accounts.normalize_username(username or "").casefold()


def recovery_lock_remaining(username: str | None) -> int:
    """还要等多少秒才能再试恢复码（0 = 现在可以试）。向上取整到秒，好读。"""
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
            if count >= RECOVERY_MAX_ATTEMPTS:
                _failures.pop(key, None)              # 冷却结束：从头开始记
            return 0
        return int(math.ceil(locked_until - now))


def note_recovery_failure(username: str | None) -> int:
    """记一次恢复码失败；连着错满就进冷却。返回当前连续失败次数。"""
    key = _failure_key(username)
    if not key:
        return 0
    now = time.time()
    with _failure_lock:
        count, locked_until = _failures.get(key, (0, 0.0))
        # 只有"进过冷却、而且冷却已经结束"才从头数（同 app/accounts.py 里的写法与理由）
        if locked_until and locked_until <= now:
            count = 0
        count += 1
        locked_until = (now + RECOVERY_COOLDOWN_SECONDS) if count >= RECOVERY_MAX_ATTEMPTS else 0.0
        _failures[key] = (count, locked_until)
    return count


def clear_recovery_failures(username: str | None) -> None:
    """恢复码验证成功：把这本账上关于他的记录清掉。"""
    key = _failure_key(username)
    if key:
        with _failure_lock:
            _failures.pop(key, None)


def reset_recovery_failures() -> None:
    """全清（测试隔离用；产品代码不需要调它）。"""
    with _failure_lock:
        _failures.clear()


def failure_count(username: str | None) -> int:
    """这个账号当前连续错了几次（排查/测试用；不进任何响应体）。"""
    with _failure_lock:
        return _failures.get(_failure_key(username), (0, 0.0))[0]


# ════════════════════════════════════════════════════════════════════════
# reset_token（内存，只存 SHA-256）与 ticket（内存，只存 SHA-256）
# ════════════════════════════════════════════════════════════════════════
# 结构按评审 Q2 的推荐写：
#   _reset_tokens[SHA-256(token)] = {account_id, challenge_id, issued_at, expires_at, used}
# 里面**没有明文 token**，也没有明文恢复码 —— 一份被误 dump 的内存快照读不出可用凭证。
_reset_tokens: dict[str, dict] = {}
_tickets: dict[str, dict] = {}
_lock = threading.Lock()


def _purge(now: float) -> None:
    """清掉过期的一次性凭证（顺手做，不另起定时器）。调用方必须已持有 `_lock`。"""
    for store in (_reset_tokens, _tickets):
        for key in [key for key, entry in store.items() if entry["expires_at"] <= now]:
            store.pop(key, None)


def issue_reset_token(*, account_id: str | None, challenge_id: str) -> dict:
    """签发一张 reset_token（明文只在这一刻存在，之后服务端只留摘要）。

    ★ `account_id=None` 是**正常路径**，不是错误：用户名不存在、账号没被批准、
      账号被停用 —— 这些情况一律照常签发一张**绑不到任何真实账号**的 token。
      调用方拿到的响应与"账号存在"时逐字节同形（评审 A1/A2/A3）：
      枚举的人从"这一步过了没有"里读不出任何东西，只有走到 verify 才会知道。
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = time.time()
    with _lock:
        _purge(now)
        _reset_tokens[_digest(token)] = {
            "account_id": account_id,
            "challenge_id": str(challenge_id or ""),
            "issued_at": now,
            "expires_at": now + RESET_TOKEN_TTL_SECONDS,
            "used": False,
        }
    return {"reset_token": token,
            "expires_in": RESET_TOKEN_TTL_SECONDS,
            "message": GENERIC_REQUEST_MESSAGE}


def _load_token(token: str | None) -> tuple[str, dict] | None:
    """按明文 token 找它在册的那条记录（找不到返回 None）。找到**不代表可以用**。"""
    if not token:
        return None
    key = _digest(str(token))
    now = time.time()
    with _lock:
        _purge(now)
        entry = _reset_tokens.get(key)
        if entry is None:
            return None
        return key, dict(entry)


def _consume_token(key: str) -> None:
    """把这张 token 标记成用过了（verify 成功那一刻就作废，评审 C5/A4）。"""
    with _lock:
        entry = _reset_tokens.get(key)
        if entry is not None:
            entry["used"] = True


def _issue_ticket(account_id: str) -> dict:
    """发一张一次性票据 —— 它**内部**绑定 account_id，前端无从指定目标账户（评审 ①）。"""
    ticket = secrets.token_urlsafe(TOKEN_BYTES)
    now = time.time()
    with _lock:
        _purge(now)
        _tickets[_digest(ticket)] = {
            "account_id": account_id,
            "issued_at": now,
            "expires_at": now + TICKET_TTL_SECONDS,
        }
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


def _consume_ticket(ticket: str | None) -> str | None:
    """**原子地**取出并作废一张票据，返回它绑定的 account_id（取不到返回 None）。

    为什么是 `pop` 而不是"打个标记"：评审 ⑤ 要的是"check → consume → 改密码"
    形成一条受保护的单次消费路径。pop 掉之后，并发的第二个 commit 连票据都找不到，
    自然不可能出现"两个请求都改了密码"。
    """
    if not ticket:
        return None
    key = _digest(str(ticket))
    now = time.time()
    with _lock:
        _purge(now)
        entry = _tickets.pop(key, None)
    return entry["account_id"] if entry else None


def token_count() -> int:
    """当前在册的 reset_token 数（排查用，不进响应体）。"""
    now = time.time()
    with _lock:
        _purge(now)
        return len(_reset_tokens)


def ticket_count() -> int:
    """当前在册的 ticket 数（排查用，不进响应体）。"""
    now = time.time()
    with _lock:
        _purge(now)
        return len(_tickets)


def reset() -> None:
    """清空全部一次性凭证与失败计数（测试隔离用；进程重启本来就会清）。"""
    with _lock:
        _reset_tokens.clear()
        _tickets.clear()
    reset_recovery_failures()


# ════════════════════════════════════════════════════════════════════════
# 第①步：请求恢复（匿名）—— 只回答"下一步能不能走"，不回答"这个账号在不在"
# ════════════════════════════════════════════════════════════════════════
def request_reset(username: str | None, challenge_id: str | None) -> dict:
    """签发 reset_token（`challenge` 必须先过了关，由接口层保证）。

    这里**唯一**决定 account_id 的地方（三条都要满足才绑得上真实账号）：
        ① 账号存在；② 状态是 active；③ （不需要看有没有恢复码 —— 没有码的人在
           verify 那一步自然会失败，在这里分叉只会多出一条能被观察到的路径）

    ★ 无论绑没绑上，返回的形状与数字都一样。请求方无法从这一步区分：
      账号不存在 / 账号没被批准 / 账号被停用 / 账号正常 —— 这四种情况在这里长得**一模一样**。
    """
    name = accounts.normalize_username(username or "")
    account_id: str | None = None
    if name:
        record = state.get_account(name)
        if record is not None and (record.get("status") or accounts.STATUS_ACTIVE) == accounts.STATUS_ACTIVE:
            account_id = str(record.get("username") or name)
    return issue_reset_token(account_id=account_id, challenge_id=str(challenge_id or ""))


# ════════════════════════════════════════════════════════════════════════
# 第②步：用恢复码换票据（匿名）
# ════════════════════════════════════════════════════════════════════════
def verify_reset(reset_token: str | None, recovery_code: str | None,
                 challenge_id: str | None) -> dict:
    """核对恢复码 → 作废它 → 发一张一次性票据。

    五道校验（评审 Q7 逐条点名，缺一条就是一个洞）：
        ① token 存在（不存在的 token 与"用过的 token"给同一句话）
        ② token 未过期
        ③ token 未被使用
        ④ token.challenge_id == 本次提交的 challenge_id（**交叉绑定**，评审 ②）
           并且那张挑战仍然"在册 + 未过期 + 确实被答对过"
        ⑤ token 对应账户存在、且状态仍允许恢复（用户可能在这中间被停用）

    最后一件事是评审 ④ 要的**原子**：`state.consume_account_recovery_code` 内部
    "比对 + 消费"是一个临界区 —— 同一个恢复码的两个并发请求，只有一个能返回 True。
    """
    loaded = _load_token(reset_token)
    if loaded is None:
        raise RecoveryError("reset_token_invalid", "这次恢复请求已失效，请从第一步重新开始。")
    key, entry = loaded
    now = time.time()
    if entry["used"]:
        raise RecoveryError("reset_token_invalid", "这次恢复请求已经用过了，请从第一步重新开始。")
    if entry["expires_at"] <= now:
        raise RecoveryError("reset_token_invalid", "这次恢复请求已过期，请从第一步重新开始。")
    # ④ 交叉绑定：这张 token 认的是**签发它的那张挑战**，不是"随便一张有效的挑战"
    submitted = str(challenge_id or "")
    if not submitted or submitted != entry["challenge_id"] or not challenge.is_passed(submitted):
        raise RecoveryError("challenge_invalid", "验证信息已失效，请从第一步重新开始。")
    account_id = entry["account_id"]
    if not account_id:
        # 账号不存在 / 没被批准 / 被停用：走到这里也是一句通用话，不暴露任何东西
        raise RecoveryError("recovery_code_invalid", GENERIC_RESET_MESSAGE)
    # 独立冷却（与登录失败计数是两本账）
    remaining = recovery_lock_remaining(account_id)
    if remaining > 0:
        raise RecoveryError("recovery_too_many_attempts",
                            f"尝试次数过多，请 {remaining} 秒后再试。")
    record = state.get_account(account_id)
    if record is None or (record.get("status") or accounts.STATUS_ACTIVE) != accounts.STATUS_ACTIVE:
        # 签发 token 之后账号被删除 / 停用 / 退回未批准 → 这一条恢复作废
        raise RecoveryError("recovery_code_invalid", GENERIC_RESET_MESSAGE)
    # ★ 原子：比对 + 消费（评审 ④）
    if not state.consume_account_recovery_code(account_id, code_hash(recovery_code), state.now_iso()):
        note_recovery_failure(account_id)             # ★ 只是记一笔，**绝不销毁**那个正确的码
        raise RecoveryError("recovery_code_invalid", GENERIC_RESET_MESSAGE)
    clear_recovery_failures(account_id)
    _consume_token(key)                               # 这一张 token 到此为止（评审 C5/A4）
    issued = _issue_ticket(account_id)
    issued["message"] = "恢复码已验证。请设置新密码。"
    return issued


# ════════════════════════════════════════════════════════════════════════
# 第③步：用票据改密码（匿名）—— ★ body 里只有 ticket 与新密码，没有"改谁的"
# ════════════════════════════════════════════════════════════════════════
def commit_reset(ticket: str | None, new_password: str | None) -> dict:
    """把票据换成一次真正的密码修改。

    ★ 改的是**谁**，完全由票据内部那个 account_id 决定 —— 请求体里就算塞了
      `username`，本函数也**一个字段都不读**（评审 ①/C3：绝不给 IDOR 留入口）。

    改完之后（评审 C5/15/16）：
        · 票据已作废（`_consume_ticket` 是 pop）；
        · reset_token 早在 verify 成功时就作废了；
        · 该账号**全部**旧会话作废（`close_sessions_for`）——别人正拿着旧会话也得下线；
        · 这一条是匿名路径，所以**不发新会话**：用户回到登录页，用新密码登录时自然会拿到新会话。
    """
    # ① 先看新密码形态（这一步与票据无关）：太短的密码不该让用户重走前两步，
    #    也不该把一张好票据浪费掉 —— 所以它排在"消费票据"之前。
    validated = accounts.validate_password(new_password or "")
    # ② 再消费票据：pop 即作废，这是评审 ⑤ 要的"check → consume → 改密码"单次消费
    account_id = _consume_ticket(ticket)
    if not account_id:
        raise RecoveryError("ticket_invalid", "这次恢复已失效，请从第一步重新开始。")
    record = state.get_account(account_id)
    if record is None:
        raise RecoveryError("ticket_invalid", "这次恢复已失效，请从第一步重新开始。")
    if (record.get("status") or accounts.STATUS_ACTIVE) != accounts.STATUS_ACTIVE:
        # 票据有效期内账号被停用/退回 → 不许借恢复流程绕过审核状态（评审 ⑧）
        raise RecoveryError("account_not_active", "这个账号当前无法登录，请先联系管理员。")
    state.update_account_fields(account_id, {
        **accounts.make_secret(validated),
        **{field: None for field in accounts.TEMP_PWD_FIELDS},   # 顺带清掉临时密码那套字段
    })
    closed = accounts.close_sessions_for(account_id)
    return {"username": record.get("username") or account_id,
            "sessions_closed": closed,
            "message": "密码已更新，请用新密码登录。"}


# ════════════════════════════════════════════════════════════════════════
# 登录用户：生成 / 轮换自己的恢复码
# ════════════════════════════════════════════════════════════════════════
def rotate_recovery_code(username: str) -> dict:
    """生成一张新恢复码并**原子地**顶掉旧的，返回明文（**只返回这一次**）。

    ★ 为什么必须原子（评审 ③）：用户手抖连点两次"生成"，两个请求并发跑，
      如果实现是"先查旧的、再写新的"，就可能出现"旧码还有效、新码也有效"——
      等于账户上凭空多了一把没人数的钥匙。这里整个写盘走仓库那把进程内锁。

    ★ 谁在调这件事由接口层保证（必须已登录、且账号名取自**会话**，不从请求体里读 ——
      评审 B2：不能出现 `username → recovery-code`）。
    """
    name = accounts.normalize_username(username)
    record = state.get_account(name) if name else None
    if record is None:
        raise RecoveryError("account_not_found", "找不到这个账号。")
    plaintext = make_recovery_code()
    stored_name = record.get("username") or name
    state.update_account_fields(stored_name, {
        "recovery_code_hash": code_hash(plaintext),
        "recovery_code_updated_at": state.now_iso(),
        "recovery_code_used_at": None,                 # 换了新码，旧的"用过了"的痕迹一并抹掉
    })
    clear_recovery_failures(stored_name)                # 换了码，之前输错的账一笔勾销
    return {"username": stored_name,
            "recovery_code": plaintext,                 # ★ 明文只在这里出现这一次
            "groups": GROUP_COUNT,
            "message": "请立刻把这串恢复码抄下来保存。它只显示这一次，关闭后无法再查看。"}


# ════════════════════════════════════════════════════════════════════════
# 管理员：给普通账号发一张一次性临时密码
# ════════════════════════════════════════════════════════════════════════
def issue_temp_password(username: str, *, actor: str) -> dict:
    """给一个**普通**账号发临时密码，返回明文（只返回一次）。

    三条限制（评审 ⑦/⑧ 与裁决第 18 条）：
        · 只能发给 `role=user` 的账号 —— 管理员给管理员发临时密码 = 管理员之间可以互相接管，
          这是本 TASK 明确不做的权限放大（管理员账号恢复暂不纳入）；
        · 目标必须存在且状态正常（不给未批准/已停用的账号发凭证）；
        · **只发凭证，不动 role / status** —— 恢复密码与账号审核是两件事。
    `actor` 只用于把"谁发的"记进响应与审计说明，它**不参与**任何授权判断（那是 require_admin 的事）。
    """
    name = accounts.normalize_username(username)
    record = state.get_account(name) if name else None
    if record is None:
        raise RecoveryError("account_not_found", "找不到这个账号。")
    if record.get("role") == accounts.ROLE_ADMIN:
        raise RecoveryError("admin_target_forbidden",
                            "不能给管理员账号发临时密码。管理员账号的恢复不在本功能范围内，"
                            "请用另一条管理员账号操作，或直接重建该账号。")
    if (record.get("status") or accounts.STATUS_ACTIVE) != accounts.STATUS_ACTIVE:
        raise RecoveryError("account_not_active",
                            "这个账号当前不是「正常」状态，发临时密码也登不进去。请先批准或恢复它。")
    plaintext = accounts.make_temp_password()
    stored_name = record.get("username") or name
    state.update_account_fields(stored_name,
                                accounts.temp_secret(plaintext, now=time.time()))
    return {
        "account": accounts.public(state.get_account(stored_name) or record),
        "temp_password": plaintext,                     # ★ 明文只在这里出现这一次
        "expires_in": accounts.TEMP_PWD_TTL_SECONDS,
        "issued_by": actor,
        "message": ("请把这串临时密码交给本人。它只能登录一次，登录后必须先设置新密码；"
                    f"{accounts.TEMP_PWD_TTL_SECONDS // 60} 分钟内有效。"),
    }


__all__ = [
    "CODE_LENGTH",
    "GENERIC_REQUEST_MESSAGE",
    "GENERIC_RESET_MESSAGE",
    "GROUP_COUNT",
    "GROUP_LENGTH",
    "RECOVERY_ALPHABET",
    "RECOVERY_COOLDOWN_SECONDS",
    "RECOVERY_MAX_ATTEMPTS",
    "RESET_TOKEN_TTL_SECONDS",
    "RecoveryError",
    "TICKET_TTL_SECONDS",
    "clear_recovery_failures",
    "code_hash",
    "commit_reset",
    "failure_count",
    "issue_reset_token",
    "issue_temp_password",
    "make_recovery_code",
    "normalize_recovery_code",
    "note_recovery_failure",
    "recovery_lock_remaining",
    "request_reset",
    "reset",
    "reset_recovery_failures",
    "rotate_recovery_code",
    "ticket_count",
    "token_count",
    "verify_reset",
]
