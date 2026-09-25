"""captcha.py · 图形验证码（本机生成，**零新依赖**）。

════════════════════════════════════════════════════════════════════════
【为什么是 SVG 而不是 PNG】
════════════════════════════════════════════════════════════════════════
PNG 要一个图片库（Pillow），那是一个新依赖 —— 本项目明确"用不到的不加"。
图形验证码的本质是"**一张人看得见、机器不好抄的图**"，SVG 用纯文本就能拼出来：
    · 服务端拼字符串 → 前端把它塞进 <img> 当图片显示，浏览器负责渲染；
    · 不用装任何东西，也不落盘（不产生临时文件）。
代价：SVG 是文本，**能读接口的人就能把字符读出来**。这一点必须说清楚（见下面的边界）。

════════════════════════════════════════════════════════════════════════
【这道验证码挡的是什么、挡不住什么（不许吹）】
════════════════════════════════════════════════════════════════════════
挡的是：**照着接口写脚本、批量试密码** —— 脚本得先"看图把 4 个字符认出来"（本机没有 OCR，
       这一步就没法自动完成），于是试密码的速度掉到"人手工敲"的量级。
挡不住：能直接读接口响应的人（字符就在图里）。所以真正兜底的是**连续尝试冷却**
       （见 api_auth 的 MAX_ATTEMPTS/COOLDOWN_SECONDS）—— 验证码只是把门槛抬高一截。
也不做：文字扭曲到人眼都难认（用户明确要"简便、看得清"）。干扰只加"能看清的前提下"的那一点。

════════════════════════════════════════════════════════════════════════
【一次性 + 会过期】
════════════════════════════════════════════════════════════════════════
    · 有效期 2 分钟（TTL_SECONDS）
    · **用一次就作废**：校验通过立刻删掉，同一个 id 再来一次一律算"过期/失效"（防重放）
    · 存在**进程内**字典里：单进程（scripts/serve.py 写死 workers=1）够用；
      多进程会各存一份（那台机器上本来也不该跑多进程，理由同 JSON 状态文件）。
"""

from __future__ import annotations

import random
import secrets
import threading
import time

# ── 参数（改这几行就够了）──────────────────────────────────────────────
LENGTH = 4                       # 4 位：用户偏好"简便"，够用
TTL_SECONDS = 120                # 有效期 2 分钟
# 去掉容易看错的字符：0 与 O、1 与 I 与 L —— 用户看不清就得换一张，白折腾一遍
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

WIDTH, HEIGHT = 132, 44          # 与登录框那一行等高（前端按 44px 显示）
CHAR_BOX = 30                    # 每个字符占的横向格子（4×30 + 左右留白 = 132）
FONT_SIZE = 26
INK = "#2b3445"                  # 深色字（浅底深字，最好认）
PAPER = "#f4f5f7"
NOISE_LINES = 2                  # 干扰线：2 条，压在字下面，浅色
NOISE_DOTS = 6

_store: dict[str, tuple[str, float]] = {}      # captcha_id -> (code, 过期时间戳)
_lock = threading.Lock()
_rng = random.Random()                         # 只用于画图（干扰线/倾斜），不用于生成字符


# ════════════════════════════════════════════════════════════════════════
# 画图
# ════════════════════════════════════════════════════════════════════════
def render_svg(code: str) -> str:
    """把 4 个字符画成一张 SVG。

    每个字符是**独立的 <text> 元素**，带一点点随机倾斜与上下浮动 ——
    这样"一眼能认"，同时不是一整行规规矩矩的机器字（脚本直接抄 DOM 也拿不到天然的顺序）。
    """
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="3" fill="{PAPER}"/>',
    ]
    # 干扰线先画（在字下面），颜色浅、位置随机，不挡字
    for _ in range(NOISE_LINES):
        x1, x2 = _rng.randint(0, 20), _rng.randint(WIDTH - 20, WIDTH)
        y1, y2 = _rng.randint(6, HEIGHT - 6), _rng.randint(6, HEIGHT - 6)
        parts.append(
            f'<path d="M{x1} {y1} Q {WIDTH // 2} {_rng.randint(0, HEIGHT)} {x2} {y2}" '
            f'stroke="#b9c2d0" stroke-width="1" fill="none"/>'
        )
    for index, char in enumerate(code):
        x = 14 + index * CHAR_BOX
        y = 30 + _rng.randint(-3, 3)
        tilt = _rng.randint(-11, 11)                      # 倾斜 ±11°：能认，又不呆板
        parts.append(
            f'<text x="{x}" y="{y}" font-family="Consolas,Menlo,monospace" '
            f'font-size="{FONT_SIZE}" font-weight="700" fill="{INK}" '
            f'transform="rotate({tilt} {x} {y})">{char}</text>'
        )
    for _ in range(NOISE_DOTS):
        parts.append(
            f'<circle cx="{_rng.randint(4, WIDTH - 4)}" cy="{_rng.randint(4, HEIGHT - 4)}" '
            f'r="1" fill="#aab4c4"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def new_code() -> str:
    """随机取 4 个字符（用 secrets，不是 random —— 这是安全相关的取值）。"""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def _purge(now: float) -> None:
    """清掉过期的（顺手做，不另起定时器）。"""
    for key in [key for key, (_, expires) in _store.items() if expires <= now]:
        _store.pop(key, None)


# ════════════════════════════════════════════════════════════════════════
# 发一张 / 校验一次
# ════════════════════════════════════════════════════════════════════════
def issue() -> dict:
    """发一张新验证码：返回 `{captcha_id, image_svg, expires_in}`。

    `image_svg` 是**整段 SVG 文本**，前端把它塞进 <img> 当图片显示（不是当 HTML 插进页面）。
    """
    code = new_code()
    captcha_id = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        _purge(now)
        _store[captcha_id] = (code, now + TTL_SECONDS)
    return {"captcha_id": captcha_id, "image_svg": render_svg(code), "expires_in": TTL_SECONDS}


def verify(captcha_id: str | None, text: str | None) -> str:
    """校验一次，返回四种结论之一（**只多不少地如实回答，调用方据此给不同的人话**）：

        "ok"      对上了 —— 并且**立刻作废**（同一个 id 不能再用第二次）
        "wrong"   没对上（验证码还在，可以重输）
        "expired" 这个 id 不在了：过期了、或者**刚才已经用过一次**、或者压根没发过
        "missing" 没带 id 或没填字符（前端的表单不会走到这儿，脚本可能）

    大小写不敏感、前后空格忽略（用户看得清就能输对，不该因为大小写卡人）。
    """
    if not captcha_id or text is None or not str(text).strip():
        return "missing"
    now = time.time()
    with _lock:
        _purge(now)
        entry = _store.get(captcha_id)
        if entry is None:
            return "expired"                       # 过期 / 已用过 / 没发过 —— 都归这一种
        code, expires = entry
        if expires <= now:
            _store.pop(captcha_id, None)
            return "expired"
        if str(text).strip().upper() != code:
            return "wrong"
        _store.pop(captcha_id, None)               # 通过即作废：防重放
        return "ok"


def pending_count() -> int:
    """当前还活着的验证码数量（**排查/健康检查用**，不参与业务）。"""
    now = time.time()
    with _lock:
        _purge(now)
        return len(_store)


def reset() -> None:
    """清空（测试隔离用；产品代码不需要调它）。"""
    with _lock:
        _store.clear()


__all__ = ["ALPHABET", "LENGTH", "TTL_SECONDS", "issue", "new_code", "pending_count",
           "render_svg", "reset", "verify"]
