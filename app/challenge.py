"""challenge.py · 「挑战」抽象层（FR-001A 用它，FR-001B 的滑块接进同一个口子）。

════════════════════════════════════════════════════════════════════════
【为什么要有这一层，而不是让恢复流程直接用 captcha】
════════════════════════════════════════════════════════════════════════
评审（reviews/010 · Q7）把这条列为**最需要冻结的接口**：密码恢复流程只认一个抽象 ——

    challenge_id（+ challenge_proof）

它**不知道**背后是图形验证码还是滑块（`slider_x` / `background_image` / `puzzle_offset`
这些词在恢复流程里一个都不许出现）。FR-001B 落地时换的是本文件的实现，
`app/api_auth.py` 的 `reset/request` 与 `app/recovery.py` 一行都不用改。

════════════════════════════════════════════════════════════════════════
【★ 本层最要紧的一句话：challenge 通过 ≠ 账户所有权】
════════════════════════════════════════════════════════════════════════
    challenge 通过 → 只允许【进入恢复流程】
    recovery code  → 证明【账户所有权】
    ticket         → 只允许【修改密码】

所以本层**只回答**"这道题过了没有、这张挑战还有效吗"，它不签发任何能改密码的东西，
也拿不到账号名 —— `issue()` 的入参里压根没有账号。这条边界是评审点名要写死的。

════════════════════════════════════════════════════════════════════════
【与 app/captcha.py 的关系：包装，不是重写】
════════════════════════════════════════════════════════════════════════
图形的部分**一个字都不重写**：签发还是既有那条 `GET /api/auth/captcha`，
判题由 `verify()` 转手调 `captcha.verify()`（本文件不碰 `captcha.py` 一行）。
本层只多做一件事：**记住这张挑战"过了没有"**。

为什么必须多记：图形验证码是**用一次即作废**的（那是它该有的样子），
而恢复流程要跨两步 —— 第①步看图填字，第②步手打一串 20 位恢复码。等第②步回来时，
那张图早就被消费掉了。如果恢复流程直接把"图还在不在"当成"挑战还有效吗"，
第②步就永远过不去。所以这里另开一份**带自己有效期**的在册记录：

    TTL_SECONDS = 900（15 分钟）—— 比图本身的 2 分钟长，因为中间那一步是人手抄 20 个字符
    登记时机   = 答对的那一刻（没答对过的 id 永远不会被登记）

于是语义干净地分成两条：
    · `verify(id, proof)`  —— 题答对了吗？（题目本身一次性，跟 captcha 一样）
    · `is_passed(id)`      —— 这张挑战还**在册、没过期、且确实被答对过**吗？
                              （`app/recovery.py` 的 reset/verify 用它做交叉绑定校验）

FR-001B 接滑块时：**新增**一道签发端点 + 把 `verify()` 换成滑块的校验，
`challenge_id` 这个口子与 `app/recovery.py` 一行都不用改。

⚠️ 边界（老实说清）：在册记录只在**进程内**，服务重启全部失效 —— 与 session / 恢复流程
的其余状态同一条 fail-closed 约定（评审 Q2）。多进程部署本来就不被支持（见 scripts/serve.py）。
"""

from __future__ import annotations

import threading
import time

from app import captcha

# ── 参数（改这几行就够了）──────────────────────────────────────────────
TTL_SECONDS = 900                 # 挑战在册 15 分钟（人手抄 20 个恢复码要留够时间）
KIND_CAPTCHA = "captcha"          # 当前唯一的挑战类型（FR-001B 会加 "slider"）

# challenge_id -> 一条在册记录
#   {"kind": 类型, "created_at": 签发时刻, "expires_at": 失效时刻, "passed_at": 答对时刻或 None}
_registry: dict[str, dict] = {}
_lock = threading.Lock()


# ⚠️ 本层**不定义异常类**：它只回四种字符串结论（ok / wrong / expired / missing），
#    翻成人话与错误码是接口层（`app/api_auth.py::_check_challenge`）的事 ——
#    "谁负责说人话"这件事只在一处，不两头都有。


def _purge(now: float) -> None:
    """清掉过期的（顺手做，不另起定时器 —— 与 captcha.py 同一套做法）。"""
    for key in [key for key, entry in _registry.items() if entry["expires_at"] <= now]:
        _registry.pop(key, None)


# ════════════════════════════════════════════════════════════════════════
# 发一张挑战 / 答一次 / 问它还在不在
# ════════════════════════════════════════════════════════════════════════
def verify(challenge_id: str | None, proof: str | None) -> str:
    """回答这张挑战，返回四种结论之一（与 `captcha.verify` 同一套词）：

        "ok"      答对了 —— 在册记录上盖一个 `passed_at` 戳，**并且立刻作废图形那一张**
                  （图是一次性的，不能被拿去答第二次）
        "wrong"   答错了（挑战还在，可以重答）
        "expired" 号对不上任何一张还在的挑战：过期 / 没发过 / 已经答过并用掉了
        "missing" 没带 challenge_id 或没带答案

    ★ 题目本身仍由 `captcha.verify` 判（图形的部分一行没重写）；
      本层只多做一件事：答对之后在**自己的册子上**记一笔"这张挑战通过了"，
      让它能活到第②步 —— 图形那张图在同一刻已经被消费掉了（那是一次性的东西）。
      所以这里**不能**拿"图还在不在"当成"挑战还有效吗"（见文件开头那段解释）。

    ★ 为什么答对时可以**补一条在册记录**：签发走的还是既有那条图形验证码通道
      （`GET /api/auth/captcha`），它不认识本层；与其为了"登记"再造一个端点，
      不如把"通过了"这件事作为登记的入口 —— 没有通过过的 id 永远不会被登记，
      也就永远不会被当成有效挑战（`is_passed` 只认盖过戳的）。
    """
    if not challenge_id or proof is None or not str(proof).strip():
        return "missing"
    outcome = captcha.verify(challenge_id, proof)
    if outcome != "ok":
        return outcome                                # wrong / expired / missing 原样带出去
    now = time.time()
    with _lock:
        _purge(now)
        entry = _registry.get(str(challenge_id))
        if entry is None:
            entry = {"kind": KIND_CAPTCHA, "created_at": now,
                     "expires_at": now + TTL_SECONDS, "passed_at": None}
        entry["passed_at"] = now
        _registry[str(challenge_id)] = entry
    return "ok"


def is_passed(challenge_id: str | None) -> bool:
    """这张挑战**在册 + 未过期 + 确实被答对过**吗？

    `app/recovery.py` 的 `reset/verify` 用它做"reset_token × challenge 交叉绑定"的另一半：
    它校验的不是"这次提交的 challenge 有效"，而是"**当初签发这张 token 时绑的那张挑战**
    仍然有效且已通过"。Token A + Challenge B 之所以会失败，是因为两者的 id 对不上
    （那一半在 recovery 层比），而这一半挡住的是"拿一张压根没答对的 id 来配"。
    """
    if not challenge_id:
        return False
    now = time.time()
    with _lock:
        _purge(now)
        entry = _registry.get(str(challenge_id))
    return bool(entry) and entry["passed_at"] is not None


def status(challenge_id: str | None) -> str:
    """这张挑战现在是什么状态（排查用；不进任何响应体）："passed" / "pending" / "unknown"。"""
    if not challenge_id:
        return "unknown"
    now = time.time()
    with _lock:
        _purge(now)
        entry = _registry.get(str(challenge_id))
    if not entry:
        return "unknown"
    return "passed" if entry["passed_at"] is not None else "pending"


def pending_count() -> int:
    """当前在册的挑战数（**排查 / 健康检查用**，不参与业务）。"""
    now = time.time()
    with _lock:
        _purge(now)
        return len(_registry)


def reset() -> None:
    """清空在册记录（测试隔离用；产品代码不需要调它）。"""
    with _lock:
        _registry.clear()


__all__ = ["KIND_CAPTCHA", "TTL_SECONDS", "is_passed",
           "pending_count", "reset", "status", "verify"]
