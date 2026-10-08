"""个人资料服务（TASK-037）。

═══════════════════════════════════════════════════════════════════════
【头像上传：按**文件签名**校验，不只看扩展名】
═══════════════════════════════════════════════════════════════════════
    只检查扩展名是可以绕过的（把 .exe 改名成 .png 就能进来）。
    所以这里读**文件头魔数**判断真实类型，扩展名只用于生成存储文件名。

    ★ 允许的类型：PNG / JPEG / WebP。
      **不接受 SVG** —— SVG 可以内嵌 <script>，作为用户上传内容回显时
      是典型的 XSS 载体。这一条是刻意的安全取舍，不是漏做。
    ★ 大小上限 2 MB：头像是几十 KB 的东西，超过说明用户选错了文件。

═══════════════════════════════════════════════════════════════════════
【presence 的语义】
═══════════════════════════════════════════════════════════════════════
    ★ 在线/忙碌/离线是**用户自己选的**，不是系统探测的。
      本模块不做心跳、不做超时自动转离线 —— 那会让"离线"变成系统猜的，
      而系统并不知道用户是不是关了浏览器。界面上如实表述为"我当前的状态"。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_profile import PresenceStatus, ThemePreference

#: 头像存放目录（相对 backend/）。
AVATAR_DIR = Path(__file__).resolve().parents[2] / "uploads" / "avatars"
#: 对外访问前缀（由 main.py 的静态挂载提供）
AVATAR_URL_PREFIX = "/uploads/avatars"

MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB

#: 魔数 → (规范扩展名, MIME)
#: ★ 只列这三种：都是位图格式，不含可执行脚本能力。
_MAGIC: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"RIFF", "webp", "image/webp"),  # WebP 还需在第 8-12 字节见到 'WEBP'，下面再确认
]


class ProfileError(Exception):
    """资料相关业务错误（路由层翻译成 400）。"""


@dataclass
class AvatarInfo:
    """保存后的头像信息。"""

    path: str          # 项目内相对路径（入库用）
    url: str           # 可直接给前端显示的 URL
    content_type: str
    size: int
    sha256: str


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """按魔数判断真实图片类型。识别不了返回 None。"""
    for magic, ext, mime in _MAGIC:
        if data.startswith(magic):
            if ext == "webp":
                # RIFF????WEBP —— 必须看到 WEBP 标记，否则可能只是别的 RIFF 文件（如 wav）
                if len(data) >= 12 and data[8:12] == b"WEBP":
                    return ext, mime
                continue
            return ext, mime
    return None


def save_avatar(db: Session, user: User, *, data: bytes, declared_name: str = "") -> AvatarInfo:
    """保存头像。

    ★ 校验顺序：大小 → 真实类型（魔数）→ 落盘 → 更新用户 → 删旧文件。
      顺序刻意如此：任何一步失败都不留下半个状态。
    """
    if not data:
        raise ProfileError("上传内容为空")

    if len(data) > MAX_AVATAR_BYTES:
        raise ProfileError(
            f"图片过大（{len(data) / 1024 / 1024:.1f} MB），上限 {MAX_AVATAR_BYTES // 1024 // 1024} MB"
        )

    detected = detect_image_type(data)
    if detected is None:
        # ★ 提示里点明"改了扩展名也没用"，否则用户会反复试
        raise ProfileError(
            "只支持 PNG / JPEG / WebP 图片。"
            "（系统按文件内容判断真实类型，改扩展名无效；SVG 因可内嵌脚本不被接受）"
        )
    ext, mime = detected

    # ★ 文件名用「用户ID + 内容哈希前缀」：
    #   带哈希可以让浏览器缓存长期有效，且同一用户重复上传同一张图不会堆文件。
    digest = hashlib.sha256(data).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", declared_name)[:40]
    filename = f"u{user.id}_{digest[:16]}.{ext}"
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    target = AVATAR_DIR / filename
    old = user.avatar_path

    target.write_bytes(data)

    # 入库用相对路径（跨机器迁移不会因为绝对路径失效）
    rel = f"uploads/avatars/{filename}"
    user.avatar_path = rel
    db.commit()
    db.refresh(user)

    # ★ 落盘并更新成功后才删旧文件；失败不回滚删除，避免"删了旧的又没存上新的"
    if old and old != rel:
        _remove_avatar_file(old)

    return AvatarInfo(
        path=rel,
        url=f"{AVATAR_URL_PREFIX}/{filename}",
        content_type=mime,
        size=len(data),
        sha256=digest,
    )


def _remove_avatar_file(rel_path: str) -> None:
    """删除旧头像文件。删不掉也不报错（旧文件残留不影响功能）。"""
    try:
        p = AVATAR_DIR.parent.parent / rel_path
        if p.is_file() and AVATAR_DIR in p.parents:
            p.unlink()
    except OSError:
        pass


def clear_avatar(db: Session, user: User) -> None:
    """移除头像（回到默认首字母头像）。"""
    old = user.avatar_path
    user.avatar_path = None
    db.commit()
    db.refresh(user)
    if old:
        _remove_avatar_file(old)


def avatar_url(user: User) -> str | None:
    """把入库的相对路径转成可访问 URL。"""
    if not user.avatar_path:
        return None
    return f"/{user.avatar_path.lstrip('/')}"


# ══════════════════════════════════════════════════════════════════════
# 资料 / 状态 / 主题
# ══════════════════════════════════════════════════════════════════════

#: 允许改的字段与长度上限（display_name 必填且非空，其余可清空）
_TEXT_LIMITS = {
    "display_name": 64,
    "email": 200,
    "phone": 50,
    "bio": 300,
}


def update_profile(
    db: Session,
    user: User,
    *,
    display_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    bio: str | None = None,
) -> User:
    """改个人资料。

    ★ **不允许通过本接口改 username / role / is_active**：
      用户名是登录凭据的一部分、角色与启停是管理员权限。
      把它们开放给"改自己的资料"会造成提权（用户能把自己改成 ADMIN）。
    """
    values = {"display_name": display_name, "email": email, "phone": phone, "bio": bio}

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ProfileError("显示名不能为空")
        user.display_name = name[: _TEXT_LIMITS["display_name"]]

    for field in ("email", "phone", "bio"):
        v = values[field]
        if v is not None:
            cleaned = v.strip()
            setattr(user, field, cleaned[: _TEXT_LIMITS[field]] or None)

    if email is not None and email.strip() and not _looks_like_email(email.strip()):
        raise ProfileError("邮箱格式不正确")

    db.commit()
    db.refresh(user)
    return user


def _looks_like_email(v: str) -> bool:
    """极简校验：有且仅有一个 @，两侧非空，域名含点。

    ★ 刻意不做完整 RFC 校验：那需要正则地狱或第三方库，
      而这里只是防止明显填错（§五 简单能解决绝不复杂化）。
    """
    if v.count("@") != 1:
        return False
    local, _, domain = v.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def set_presence(db: Session, user: User, status: PresenceStatus) -> User:
    """设置**自我声明**的状态。"""
    user.presence = status
    db.commit()
    db.refresh(user)
    return user


def set_theme(db: Session, user: User, theme: ThemePreference) -> User:
    """设置主题偏好。"""
    user.theme = theme
    db.commit()
    db.refresh(user)
    return user


__all__ = [
    "AVATAR_DIR",
    "AVATAR_URL_PREFIX",
    "MAX_AVATAR_BYTES",
    "AvatarInfo",
    "ProfileError",
    "avatar_url",
    "clear_avatar",
    "detect_image_type",
    "save_avatar",
    "set_presence",
    "set_theme",
    "update_profile",
]
