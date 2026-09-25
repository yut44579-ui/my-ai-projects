"""api_auth.py · 本地账号的 HTTP 端点（注册 / 登录 / 注册页查重）。

════════════════════════════════════════════════════════════════════════
【端点清单（全是新增路径，与既有 12 个端点零交集）】
════════════════════════════════════════════════════════════════════════
    GET  /api/auth/captcha                 要一张图形验证码（SVG，2 分钟有效，用一次作废）
    POST /api/auth/register                注册一个账号（重复 → 409；验证码必填；**注册后等管理员批**）
    POST /api/auth/login                   登录校验（密码不对 → 401；账号未获批 → 403 且三种情况三句话）
    POST /api/auth/logout                  退出登录（把服务端那条本机会话也清掉）
    GET  /api/auth/accounts/exists         注册页失焦查重 / 首次使用引导
    GET  /api/auth/accounts                账号列表（**管理员**）：待批准的排在最前
    POST /api/auth/accounts/{u}/review     审批账号（**管理员**）：批准 / 拒绝 / 停用 / 恢复

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

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import accounts, captcha

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
    "last_admin": 400,              # 不能让最后一个管理员下台
    "account_not_found": 404,
    "review_action_invalid": 400,
    "review_no_change": 400,
    "username_too_short": 400,
    "username_too_long": 400,
    "username_invalid": 400,
    "password_too_short": 400,
    "password_too_long": 400,
    "display_name_too_long": 400,
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


@router.post("/register", summary="注册账号（注册后等管理员批准，不能直接登录）")
def register(payload: RegisterRequest) -> dict[str, Any]:
    """注册一个账号，返回它的**状态**（`等待批准` / 第一个账号直接是管理员可用）。

    · 验证码不对/过期 → 400 + 人话
    · 账号重复 → 409 + 「这个账号已被注册」
    · 账号/密码形态不合格 → 400 + 人话
    · 响应里绝不含盐与校验值（见 app/accounts.py::public）
    · **不再自动登录**：注册成功之后要等管理员批准（第一个账号除外，它自己就是管理员）
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


@router.get("/accounts/exists", summary="账号是否已被注册（注册页查重 / 首次使用引导）")
def account_exists(
    username: str | None = Query(default=None, description="要查的账号名；不传就只回账号总数"),
) -> dict[str, Any]:
    """`{exists, account_count}`。

    · 传了 `username` → `exists` 是"这个账号有没有被注册过"（注册表单失焦时用）
    · 不传 → `exists` 恒为 false，只用 `account_count`

    为什么要带 `account_count`：系统里一个账号都没有时（首次使用），登录页要给出
    「先注册一个」的明显引导 —— 前端据此判断，而不是"进去发现登不上再猜"。
    """
    return {"exists": accounts.exists(username) if username else False,
            "account_count": accounts.account_count()}
