"""api_auth.py · 本地账号的 HTTP 端点（注册 / 登录 / 注册页查重）。

════════════════════════════════════════════════════════════════════════
【端点清单（全是新增路径，与既有 12 个端点零交集）】
════════════════════════════════════════════════════════════════════════
    GET  /api/auth/captcha                 要一张图形验证码（SVG，2 分钟有效，用一次作废）
    POST /api/auth/register                注册一个账号（重复 → 409；验证码必填）
                                           ★ FR-002A：**默认本机单用户 = 注册即生效**；
                                             只有 SRA_REQUIRE_APPROVAL=1（真实审批流）才 pending 待批
    POST /api/auth/login                   登录校验（密码不对 → 401；账号未获批 → 403 且三种情况三句话）
    POST /api/auth/logout                  退出登录（把服务端那条本机会话也清掉）
    GET  /api/auth/accounts/exists         注册页失焦查重 / 首次使用引导
    GET  /api/auth/accounts                账号列表（**管理员**）：待批准的排在最前
    POST /api/auth/accounts/{u}/review     审批账号（**管理员**）：批准 / 拒绝 / 停用 / 恢复
    DELETE /api/auth/accounts/{u}          删除账号（**管理员**）：整条记录消失，不可逆

【FR-001A 新增的 6 条（密码恢复闭环：忘记密码 → 真能重置）】
    POST /api/auth/reset/request           第①步（匿名）账号 + 验证码 → reset_token
    POST /api/auth/reset/verify            第②步（匿名）恢复码 → 一次性 ticket
    POST /api/auth/reset/commit            第③步（匿名）ticket + 新密码（**只收这两个字段**）
    POST /api/auth/recovery-code           生成/轮换自己的恢复码（**已登录**，明文只返回一次）
    POST /api/auth/accounts/{u}/temp-password  发一次性临时密码（**仅管理员**，只发给普通账号）
    POST /api/auth/password/change         改密码（已登录；**临时密码登录后的唯一出路**）

三层授权严格分离（评审冻结，不许合并）：
    challenge 通过 → 只允许【进入恢复流程】；recovery code → 证明【账户所有权】；
    ticket → 只允许【修改密码】。任何"用户名 + 验证码 = 所有权"的实现都是账户接管漏洞。

【验证码是**必填**的（统一口径，不留后门）】
注册与登录都要求带 `captcha_id` + `captcha_text`，缺了直接 400 + 人话。
为什么不留"不带验证码也能登"的旁路：留了口子等于没做这道闸门（脚本走旁路就行），
而本轮所有测试与验收脚本都已经改成"先取一张图、把上面的字符填进去"——
它们走的就是**用户走的那条路**，这比给脚本开小门更有价值。

【为什么又是独立文件 + 只往 api.py 加两行】
同 TASK-003/004 与 STEP A 的理由：api.py 里那些端点是**冻结的 Legacy Contract**，
响应形状一个字不许动。账号能力走新文件、新路径，api.py 只多一行 import + 一行
include_router（位置在 `mount("/")` 之前，否则会被静态目录吃掉）。

【响应里有什么、没有什么（这条是硬要求）】
有的：`{username, display_name, created_at, last_login_at}` —— 白名单由
      `app/accounts.py::PUBLIC_FIELDS` 决定，**pwd_salt / pwd_hash / pwd_iterations
      永远不会出现在任何响应里**（本文件从头到尾没碰过这三个字段）。
没有的：令牌、会话 id、角色 —— 本地单机模式不发通行证，接口也不按账号鉴权。
      登录成功后前端得到的是一条"这次是谁"的展示记录，仅此而已（见 app/accounts.py 开头）。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import accounts, captcha, challenge, recovery, state

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 账号层错误码 → HTTP 状态码。
# 只在这里映射一次：业务层只说"发生了什么"（哪条人话），HTTP 语义由接口层决定。
_STATUS_BY_CODE = {
    "username_taken": 409,          # 已被注册（不是参数错，是冲突）
    "bad_credentials": 401,         # 账号或密码不对
    "too_many_attempts": 429,       # 连续错太多次，正在冷却（不是"你错了"，是"先等等"）
    # 账号能对上密码、但状态不允许登录 —— 用 403（"你没被允许"，不是"你没通过校验"）
    "account_pending": 403,
    "account_rejected": 403,
    "account_disabled": 403,
    # 管理动作的守卫
    "session_invalid": 401,         # 会话失效（服务重启过 / 本来就没登录）→ 重新登录
    "not_admin": 403,               # 登录了，但不是管理员
    "last_admin": 400,              # 不能让最后一个管理员下台（拒绝 / 停用 / 删除都走这条）
    "cannot_delete_self": 400,      # 删除的账号正是当前登录的这个（见 delete_account）
    "account_not_found": 404,
    "review_action_invalid": 400,
    "review_no_change": 400,
    "username_too_short": 400,
    "username_too_long": 400,
    "username_invalid": 400,
    "password_too_short": 400,
    "password_too_long": 400,
    "display_name_too_long": 400,
    # ── 密码恢复（FR-001A）──────────────────────────────────────────────
    # 「临时密码登录进来的，还没改密」：403 是刻意的 —— 不是"你没登录"（401），
    # 而是"你登录了、但这条会话目前只准做一件事"。前端据此直接把人引到改密那一步。
    "must_change_password": 403,
    "reset_token_invalid": 400,      # reset_token 不存在 / 过期 / 已用过（**三合一，不分家**）
    "challenge_invalid": 400,        # 挑战过期 / 与 token 绑定的那张不一致
    "recovery_code_invalid": 400,    # 恢复码不对（也用于"这个账号根本没有恢复码"）
    "recovery_too_many_attempts": 429,   # 恢复码连着错太多次（**独立于登录的冷却**）
    "ticket_invalid": 400,           # 票据不存在 / 过期 / 已用过
    "admin_target_forbidden": 403,   # 管理员账号不走临时密码恢复（避免管理员之间互相接管）
    "account_not_active": 400,       # 账号不是「正常」状态：不给发临时密码 / 不给走恢复
    "too_many_requests": 429,        # 查重端点被批量扫（枚举旁路节流，见 account_exists）
}


class AuthApiError(HTTPException):
    """带机器可读 `code` 的 HTTPException（与 api.py / api_datasets.py 同形，但不 import 它们）。

    api.py 的统一错误处理器靠鸭子类型接住带 `code`/`message` 的异常 → 产出同一个错误体
    `{error:{code,message,detail}, detail}`，前端 api.js 的 request() 因此不用为账号能力写第二套。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


def _as_http(exc: accounts.AccountError) -> AuthApiError:
    """账号层错误 → HTTP（人话原样带出去，不翻译成"内部错误"）。"""
    return AuthApiError(_STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message)


def _recovery_http(exc: recovery.RecoveryError) -> AuthApiError:
    """恢复层错误 → HTTP（与账号层同一套映射表、同一套统一错误体）。"""
    return AuthApiError(_STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message)


class CaptchaFields(BaseModel):
    """图形验证码的两个入参（注册与登录**都要**）。

    为什么字段可空：可空才能给出**人话错误**（"请先填写验证码"），
    而不是让 pydantic 直接回一个 422 —— 那种报错用户看不懂（AC 里点名要的是人话）。
    """

    captcha_id: str | None = Field(default=None, description="GET /api/auth/captcha 返回的那张图的编号")
    captcha_text: str | None = Field(default=None, description="用户照着图填的 4 个字符（大小写不敏感）")


class RegisterRequest(CaptchaFields):
    """注册请求。

    不做 `extra="forbid"`：注册/登录的入参本来就几个字段，多带一个字段
    （比如表单里的"记住账号"复选框顺手带上来）不该让整个注册 422 失败 ——
    少一个字段是**用户**的错，多一个字段是前端的事，别把它变成用户看不懂的报错。
    """

    username: str = Field(..., description="账号：2~40 个字符，中文/字母/数字/下划线/点/中划线")
    password: str = Field(..., description="密码：至少 6 位")
    display_name: str | None = Field(default=None, description="显示名（顶栏那个名字）；留空用账号名")


class LoginRequest(CaptchaFields):
    """登录请求（账号 + 密码 + 验证码）。"""

    username: str = Field(..., description="注册时用的账号")
    password: str = Field(..., description="密码")


class LogoutRequest(BaseModel):
    """退出登录（把服务端那条本机会话清掉）。"""

    session_id: str | None = Field(default=None, description="登录时返回的那个会话编号")


class ReviewRequest(BaseModel):
    """审批一个账号（管理员）。"""

    action: str = Field(..., description="approve / reject / disable / enable")
    session_id: str | None = Field(default=None, description="管理员的会话编号（登录时拿到的）")


class ChallengeFields(BaseModel):
    """「挑战」的四个入参 —— 前两个是**冻结的标准名**，后两个是兼容写法。

    评审冻结的请求体是：
        {"username": ..., "captcha": ..., "challenge_id": ..., "challenge_proof": ...}
    `challenge_id` / `challenge_proof` 是"只认一个抽象"的那一半（FR-001B 换滑块时接口不变）；
    `captcha` / `captcha_id` 是同一件事的另一种写法（本机图形验证码这一阶段的实际载体），
    用户在页面上填的那张图，既可以说"这是 captcha"，也可以说"这是 challenge 的当前实现"。
    两种写法都收，取到哪个用哪个 —— **不收任何第三种**，也不在这层做任何判断。
    """

    challenge_id: str | None = Field(default=None, description="挑战编号（当前 = 图形验证码的编号）")
    challenge_proof: str | None = Field(default=None, description="挑战的答案（当前 = 图上的 4 个字符）")
    captcha_id: str | None = Field(default=None, description="challenge_id 的另一种写法")
    captcha: str | None = Field(default=None, description="challenge_proof 的另一种写法")


class ResetRequest(ChallengeFields):
    """第①步：提交账号 + 挑战，换一张 reset_token（匿名）。"""

    username: str | None = Field(default=None, description="要恢复的账号名")


class ResetVerifyRequest(BaseModel):
    """第②步：用 reset_token + 恢复码换一张一次性票据（匿名）。

    `challenge_id` 必须与签发 token 时绑定的那一个是**同一个** —— 这就是评审点名的
    "reset_token × challenge 交叉绑定"，Token A + Challenge B 必须失败。
    """

    reset_token: str | None = Field(default=None, description="第①步返回的 reset_token")
    recovery_code: str | None = Field(default=None, description="恢复码，20 位；空格/连字符随便填")
    challenge_id: str | None = Field(default=None, description="第①步用的那个挑战编号")
    captcha_id: str | None = Field(default=None, description="challenge_id 的另一种写法")


class ResetCommitRequest(BaseModel):
    """第③步：用票据设置新密码（匿名）。

    ★ **只有这两个字段**（评审冻结）：改的是**谁**完全由票据内部决定。
      请求体里就算塞了 `username` / `account_id` / `role`，本端点的代码也**一个都不读**
      —— 那条 IDOR 的路从"接收入参"这一步就不存在。
    """

    ticket: str | None = Field(default=None, description="第②步返回的一次性票据")
    new_password: str | None = Field(default=None, description="新密码：至少 6 位")


class SessionOnlyRequest(BaseModel):
    """只要一条会话编号的请求（生成恢复码）。

    ★ 刻意**没有** `username` 字段：生成谁的恢复码只能由会话决定（评审 B2：
      "不能 username → recovery-code"）。这一条不是靠"读到了也不理"，是压根不给这个口子。
    """

    session_id: str | None = Field(default=None, description="登录时返回的本机会话编号")


class PasswordChangeRequest(BaseModel):
    """登录用户改密码（也是临时密码登录后的**唯一**出路）。"""

    old_password: str = Field(..., description="原密码（临时密码登录时，这里填那串临时密码）")
    new_password: str = Field(..., description="新密码：至少 6 位")
    session_id: str | None = Field(default=None, description="本机会话编号")


def _challenge_of(payload: ChallengeFields) -> tuple[str | None, str | None]:
    """从两种写法里取出 `(challenge_id, challenge_proof)`（都没有就是 None，交给校验去说人话）。"""
    return (payload.challenge_id or payload.captcha_id,
            payload.challenge_proof or payload.captcha)


def _check_challenge(challenge_id: str | None, proof: str | None) -> None:
    """挑战没过关就抛（三种情况三句人话，与 `_check_captcha` 同一套）。

    只回答"这道题过了没有"。★ 过了**不代表**这个账号是谁的（评审 B：challenge ≠ ownership）
    —— 它换来的只是"可以进入恢复流程"这一件事。

    ★ 这里**不**记任何失败计数：恢复这条路失败绝不许污染登录失败计数（评审 C/Q4-3），
      恢复码那本账是独立的，见 `app/recovery.py`。
    """
    outcome = challenge.verify(challenge_id, proof)
    if outcome == "ok":
        return
    if outcome == "wrong":
        raise AuthApiError(400, "captcha_wrong", "验证码不对，请重新输入。")
    if outcome == "missing":
        raise AuthApiError(400, "captcha_missing", "请先填写验证码。")
    raise AuthApiError(400, "captcha_expired", "验证码已过期，请点一下刷新。")


def _check_captcha(payload: CaptchaFields, *, count_failure_for: str | None = None) -> None:
    """验证码不过关就抛（三种情况给三句不同的人话）。

    `count_failure_for` 传账号名时，"填错验证码"会记一次失败 —— 它**也是**一次登录尝试；
    而"过期/没带"不记（那是页面放太久/脚本没带，不是有人在猜）。见 app/accounts.py 的说明。
    """
    outcome = captcha.verify(payload.captcha_id, payload.captcha_text)
    if outcome == "ok":
        return
    if outcome == "wrong":
        if count_failure_for:
            accounts.note_failure(count_failure_for)
        raise AuthApiError(400, "captcha_wrong", "验证码不对，请重新输入。")
    if outcome == "missing":
        raise AuthApiError(400, "captcha_missing", "请先填写验证码。")
    # 过期 / 已经用过一次 / 压根没发过 —— 都归这一种，前端收到就自动换一张
    raise AuthApiError(400, "captcha_expired", "验证码已过期，请点一下刷新。")


@router.get("/captcha", summary="要一张图形验证码（SVG，本机生成，零依赖）")
def get_captcha() -> dict[str, Any]:
    """返回 `{captcha_id, image_svg, expires_in}`。

    · `image_svg`：整段 SVG 文本，前端把它当**图片**显示（塞进 <img>，不是当 HTML 插页面）
    · 有效期 `expires_in` 秒，**用一次即作废**；点一下图可以换一张（再调一次这个端点）
    · 为什么是 SVG 不是 PNG：本机不一定有图片库，而"加一个依赖"在本项目是要挨骂的（见 app/captcha.py）
    """
    return captcha.issue()


@router.post("/register", summary="注册账号（默认本机单用户：注册即生效；审批流模式：待批准）")
def register(payload: RegisterRequest) -> dict[str, Any]:
    """注册一个账号，返回它的**状态**（可用 `active` / 待批准 `pending`）。

    · 验证码不对/过期 → 400 + 人话
    · 账号重复 → 409 + 「这个账号已被注册」
    · 账号/密码形态不合格 → 400 + 人话
    · 响应里绝不含盐与校验值（见 app/accounts.py::public）
    · ★ FR-002A：**要不要等审批由模式决定**（见 app/accounts.py 的 MODE 一节）——
      默认（本机单用户）注册即生效，第一个账号是管理员、其余是可用普通账号；
      `SRA_REQUIRE_APPROVAL=1`（真实审批流）才落在 pending 上等管理员批准。
    · 无论哪种模式都**不自动登录**：注册只建账号，登录仍要单独走一次（验证码照旧）。
    """
    _check_captcha(payload)
    try:
        account = accounts.register(payload.username, payload.password, payload.display_name)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    return {"account": account}


@router.post("/login", summary="登录校验（本地账号 + 图形验证码）")
def login(payload: LoginRequest) -> dict[str, Any]:
    """对一下验证码、账号与密码。三道闸门按顺序过：

        ① 冷却中？        → 429 +「尝试次数过多，请 N 秒后再试」（连着错太多次了）
        ② 验证码对不对？  → 400 + 人话（填错 / 过期各有各的话）
        ③ 账号密码对不对？→ 401 +「账号或密码不对」
                            （**账号不存在与密码错给的是同一句话**，不给探测口）
    ④ 这个账号能登录吗？→ 403，三种情况三句话（用户要能逐个分辨）：
                            待批准 / 未通过审批 / 已被停用

    顺序是有意的：**先密码、后状态** —— 否则"待批准"这句话就成了"这个账号存在吗"的探针，
    谁都能拿一个账号名试出状态；现在只有密码对的人才知道自己账号的状态。

    全过了 → 返回账号展示信息 + 一个**本机会话编号**（管理动作用它回答"是谁按的"），
             刷新 `last_login_at`，并把连续失败清零。
    """
    # ① 先看是不是还在冷却（这时连验证码都不必校验：先让人等）
    remaining = accounts.lock_remaining(payload.username)
    if remaining > 0:
        raise AuthApiError(429, "too_many_attempts", f"尝试次数过多，请 {remaining} 秒后再试。")
    # ② 验证码（填错也算一次失败的尝试）
    _check_captcha(payload, count_failure_for=payload.username)
    # ③④ 账号、密码、能不能登录（都在 accounts.login 里，状态不允许时抛对应的错）
    try:
        account = accounts.login(payload.username, payload.password)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    return {"account": account}


@router.post("/logout", summary="退出登录（把服务端这条本机会话也清掉）")
def logout(payload: LogoutRequest) -> dict[str, Any]:
    """前端退出登录时顺手调一下：服务端那条会话清掉。

    清不到（服务重启过、编号是假的）也返回成功 —— 退出登录**不该因为这种事失败**，
    用户的目的达到了（本机记录前端自己会清）。
    """
    return {"closed": accounts.close_session(payload.session_id)}


# ════════════════════════════════════════════════════════════════════════
# 密码恢复（FR-001A）—— **匿名**的三步 + **登录用户**的两条
# ════════════════════════════════════════════════════════════════════════
# 三层授权严格分离（评审冻结，不许合并成一条路）：
#     challenge 通过  → 只允许【进入恢复流程】   （/reset/request）
#     recovery code   → 证明【账户所有权】       （/reset/verify）
#     ticket          → 只允许【修改密码】       （/reset/commit）
# 为什么非要拆成三个端点而不是一个 /reset：评审原话 —— "恢复码验证逻辑"和"修改密码逻辑"
# 共享一条状态路径时，某个字段判断遗漏就会直接形成 reset bypass。
@router.post("/reset/request", summary="忘记密码第①步：账号 + 验证码 → 换一张 reset_token")
def reset_request(payload: ResetRequest) -> dict[str, Any]:
    """`{reset_token, expires_in, message}` —— **不回答"这个账号在不在"**。

    不泄露存在性（评审 A1–A3）靠三件事一起做到：
        ① 挑战没过关的错误只跟挑战有关，跟账号名无关；
        ② 账号不存在 / 未批准 / 已停用 —— 照样签发一张 token（只是它绑不到任何真实账号），
           状态码、字段、数字与正常情况**逐字节同形**；
        ③ 没有任何"不存在就提前 return"的分支（两条路的代码路径一样长）。

    统一话术（评审 Q6 原文）：**「如果账户信息有效且满足恢复条件，将继续下一步。」**
    ★ 这一步**绝不**生成、也绝不返回恢复码 —— 那等于"用户名 + 验证码 = 账户所有权"。
    """
    challenge_id, proof = _challenge_of(payload)
    _check_challenge(challenge_id, proof)
    return recovery.request_reset(payload.username, challenge_id)


@router.post("/reset/verify", summary="忘记密码第②步：恢复码 → 换一张一次性票据")
def reset_verify(payload: ResetVerifyRequest) -> dict[str, Any]:
    """`{ticket, expires_in, message}`。

    成功的一刻同时做三件事（评审 B8 / C1 / C5）：
        · **立即作废这个恢复码**（原子地"比对 + 消费"，同一个码的两个并发请求只有一个成功）；
        · 作废这张 reset_token；
        · 发一张**内部绑定 account_id** 的一次性票据。

    失败一律「恢复码不对或已失效」—— 分不清"没这个码"和"码打错了"，也就没得枚举。
    连着错 `RECOVERY_MAX_ATTEMPTS` 次 → 60 秒冷却（**这本账与登录失败计数完全分开**）。
    """
    challenge_id = payload.challenge_id or payload.captcha_id
    try:
        return recovery.verify_reset(payload.reset_token, payload.recovery_code, challenge_id)
    except recovery.RecoveryError as exc:
        raise _recovery_http(exc) from exc


@router.post("/reset/commit", summary="忘记密码第③步：票据 + 新密码 → 改密（只收这两个字段）")
def reset_commit(payload: ResetCommitRequest) -> dict[str, Any]:
    """把票据换成一次真正的密码修改；返回改了哪个账号（给页面显示"谁的密码改了"）。

    ★ 改的是**谁**由票据内部决定：请求体多带 `username` / `role` / `account_id` 也不会被读
      （评审 ①/C3/F11/F12 —— 这条 IDOR 路从接收入参就不存在）。

    成功后（评审 C5 / 15 / 16）：票据作废、reset_token 早在第②步就作废了、
    该账号**全部**旧会话一并作废（别人正拿着旧会话也得下线）。
    这是匿名路径，所以不发新会话 —— 用户回登录页，用新密码登录时自然会拿到。
    """
    try:
        return recovery.commit_reset(payload.ticket, payload.new_password)
    except recovery.RecoveryError as exc:
        raise _recovery_http(exc) from exc
    except accounts.AccountError as exc:
        # 新密码形态不合格（太短/太长）走的是**账号层**的那套校验 ——
        # 那是"密码本身不行"，与"这张票据行不行"是两件事，错误码也各是各的。
        raise _as_http(exc) from exc


@router.post("/recovery-code", summary="生成 / 轮换**自己**的恢复码（必须已登录）")
def recovery_code(payload: SessionOnlyRequest) -> dict[str, Any]:
    """生成一张新恢复码，**明文只返回这一次**（评审 B6）。

    · 给谁生成**由会话决定**，请求体里根本没有账号名字段（评审 B1/B2）
    · 生成新码 = **原子地**替换旧 hash（评审 ③：连点两次不会出现两个都有效的码）
    · 旧码立刻失效、新码生效（评审 B7）
    · 恢复码只用 `secrets` 生成、只存 SHA-256（评审 B3/B4/B5）
    · 临时密码登录进来的那条会话会被守卫挡在 403（先去改密）
    """
    try:
        guarded = accounts.require_session(payload.session_id)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    try:
        return recovery.rotate_recovery_code(guarded["account"].get("username") or "")
    except recovery.RecoveryError as exc:
        raise _recovery_http(exc) from exc


@router.post("/password/change", summary="修改密码（登录用户；也是临时密码登录后的唯一出路）")
def password_change(payload: PasswordChangeRequest) -> dict[str, Any]:
    """核对原密码 → 落新校验值 → **作废该账号全部旧会话** → 返回**一条新会话**。

    为什么必须换会话（评审 15：密码变了 = 认证状态重建）：旧会话是"用旧密码/临时密码
    换来的"，密码一改它就不该再代表这个人。不换的话，用户改完密码自己反而被踢下线。

    临时密码登录进来的那条路（`must_change_password=true`）**只能走这里**：
    它的"原密码"就是那串临时密码（登录时已经被消费掉了，所以这里单独放行那一个哈希）。
    """
    try:
        guarded = accounts.require_session_any(payload.session_id, allow_must_change=True)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    record = guarded["account"]
    name = record.get("username") or ""
    try:
        session_id = accounts.change_password(
            name, payload.old_password, payload.new_password,
            allow_temp_old=bool(guarded["session"]["must_change"]))
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    updated = state.get_account(name) or record
    return {
        "account": accounts.public(updated),
        "session_id": session_id,                  # ★ 新会话：前端要拿它接着用
        "must_change_password": False,
        "message": "密码已更新。其它设备上的登录状态已失效。",
    }


# ════════════════════════════════════════════════════════════════════════
# 账号管理（**只有管理员能调**）
# ════════════════════════════════════════════════════════════════════════
@router.get("/accounts", summary="账号列表（管理员）：待批准的排在最前")
def list_accounts(
    session_id: str | None = Query(default=None, description="登录时拿到的本机会话编号"),
    status: str | None = Query(default=None, description="只看某个状态；不传就全部"),
) -> dict[str, Any]:
    """`{accounts: [...], pending: N}`。

    · 只有管理员能调（会话 → 账号 → 角色，三道都要过）
    · 每条都走 `accounts.public()` 白名单裁剪：**盐与校验值永远不会出现在这里**
    """
    try:
        accounts.require_admin(session_id)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    items = accounts.list_accounts()
    if status:
        items = [item for item in items if item.get("status") == status]
    return {"accounts": items,
            "pending": sum(1 for item in accounts.list_accounts()
                           if item.get("status") == accounts.STATUS_PENDING),
            "status_text": accounts.STATUS_TEXT}


@router.post("/accounts/{username}/review", summary="审批账号（管理员）：批准 / 拒绝 / 停用 / 恢复")
def review_account(username: str, payload: ReviewRequest) -> dict[str, Any]:
    """批准 / 拒绝 / 停用 / 恢复一个账号。

    · 管理员账号不能被拒绝或停用（否则没人能批账号了）
    · 记下"谁、什么时候批的"（`reviewed_by` / `reviewed_at`）—— 审批是要能追溯的
    """
    admin = None
    try:
        admin = accounts.require_admin(payload.session_id)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    try:
        account = accounts.review(username, payload.action,
                                  actor=admin.get("username") or "")
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    return {"account": account, "status_text": accounts.STATUS_TEXT}


@router.delete("/accounts/{username}", summary="删除账号（管理员）：整条记录从账号表里消失")
def delete_account(
    username: str,
    session_id: str | None = Query(default=None, description="管理员的会话编号（登录时拿到的）"),
) -> dict[str, Any]:
    """**彻底删掉**一个账号（已批准列表里的「删除」）。

    · 只有管理员能调（会话 → 账号 → 角色）
    · 这是**不可逆**动作：记录从账号文件里消失，账号名随即可以被重新注册
      （与「停用」不同 —— 停用还留着重启的机会，见 `app/accounts.py::review`）
    · 唯一的管理员账号不许删：删掉就没人能批账号了（系统会退化成"得手改账号文件"）
    """
    try:
        admin = accounts.require_admin(session_id)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    actor = admin.get("username") or ""
    if accounts.normalize_username(username).casefold() == str(actor).casefold():
        raise AuthApiError(400, "cannot_delete_self",
                           "不能删掉自己当前登录的这个账号。要删它，先用别的管理员账号登录。")
    try:
        account = accounts.delete_account(username)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    return {"deleted": True, "account": account}


@router.post("/accounts/{username}/temp-password",
             summary="给普通账号发一张一次性临时密码（**仅管理员**）")
def temp_password(username: str, payload: SessionOnlyRequest) -> dict[str, Any]:
    """`{account, temp_password, expires_in, issued_by, message}` —— 明文**只返回这一次**。

    四条限制（评审 D1–D5 / ⑦ / ⑧ / 裁决第 12、18 条）：
        · 只有管理员能调 —— 会话 → 账号 → 角色三道守卫（普通用户 403，不管他填的是谁）
        · **只能发给 role=user 的账号**：管理员给管理员发临时密码 = 管理员之间可以互相接管，
          这条权限放大本 TASK 明确不做（管理员账号的恢复不在本功能范围内）
        · 目标必须存在且状态正常（不给未批准/已停用的账号发凭证）
        · **只发凭证，不动 role / status** —— 恢复密码与账号审批是两件事，不许顺手改

    响应里没有任何哈希：出参走 `accounts.public()` 白名单，临时密码的
    `temp_pwd_*` 字段不在白名单里（评审 D3：管理员也不能读用户的密码或临时密码哈希）。
    """
    try:
        admin = accounts.require_admin(payload.session_id)
    except accounts.AccountError as exc:
        raise _as_http(exc) from exc
    try:
        return recovery.issue_temp_password(username, actor=admin.get("username") or "")
    except recovery.RecoveryError as exc:
        raise _recovery_http(exc) from exc


# ── 查重端点的节流（评审 ⑨：audit /api/auth/accounts/exists 防枚举旁路）──────
# 这个端点是**注册表单失焦查重**要用的，所以行为不能改（前端要靠它给出"✓ 可用"），
# 但它同时是一个"用户名 → 存在性"的直答口。评审的原话是：
#     "至少不能让它成为**无需认证即可批量枚举**的强信号接口"
# 于是这里给它加一道**进程内**的节流闸门：正常人手打几个名字永远不会碰到，
# 脚本批量扫就会被挡住（429 + 人话）。不加 Redis、不加 IP 信誉、不落盘 ——
# 与项目里其它进程内状态（验证码、失败计数、会话）同一条边界：单进程、重启即清零。
EXISTS_WINDOW_SECONDS = 60
EXISTS_MAX_PER_WINDOW = 30
_exists_hits: dict[str, list[float]] = {}          # 进程内：只记一个滑动窗口，不记"谁问过谁"


def _exists_throttled(now: float) -> bool:
    """这个窗口里问得太频繁了吗？（返回 True 表示应当拒绝。）"""
    window = [stamp for stamp in _exists_hits.get("all", []) if now - stamp < EXISTS_WINDOW_SECONDS]
    window.append(now)
    _exists_hits["all"] = window
    return len(window) > EXISTS_MAX_PER_WINDOW


def reset_exists_throttle() -> None:
    """清空节流窗口（测试隔离用；产品代码不需要调它）。"""
    _exists_hits.clear()


@router.get("/accounts/exists", summary="账号是否已被注册（注册页查重 / 首次使用引导）")
def account_exists(
    username: str | None = Query(default=None, description="要查的账号名；不传就只回账号总数"),
) -> dict[str, Any]:
    """`{exists, account_count}`。

    · 传了 `username` → `exists` 是"这个账号有没有被注册过"（注册表单失焦时用）
    · 不传 → `exists` 恒为 false，只用 `account_count`

    为什么要带 `account_count`：系统里一个账号都没有时（首次使用），登录页要给出
    「先注册一个」的明显引导 —— 前端据此判断，而不是"进去发现登不上再猜"。

    ⚠️ 这个端点是**登记在案**的枚举面（评审 ⑨）：它确实回答"这个名字在不在"，
    因为注册表单需要这句话。本 TASK 没有扩大它，只给它加了一道节流闸门（见上面）。
    """
    if _exists_throttled(time.time()):
        raise AuthApiError(429, "too_many_requests",
                           "查得太频繁了，请稍等一会儿再试（这一栏是给注册表单查重用的）。")
    return {"exists": accounts.exists(username) if username else False,
            "account_count": accounts.account_count()}
