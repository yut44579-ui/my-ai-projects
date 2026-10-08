"""真实在线状态（TASK-040）。

═══════════════════════════════════════════════════════════════════════
【用户要求】
═══════════════════════════════════════════════════════════════════════
    「状态那里我希望是实时变化的，就是在线/忙碌/离线等」

    此前（TASK-037）我做的是**手动设置**的自我声明，并在界面上如实写明
    "系统没有实时在线探测"。用户明确要求改成真的会自己变，所以本模块
    实现**基于心跳与活动检测**的推算。

═══════════════════════════════════════════════════════════════════════
【判定规则】（界面上必须原文写出来，否则用户看不懂为什么自己是"忙碌"）
═══════════════════════════════════════════════════════════════════════
    在线  最近 IDLE_AFTER 秒内有真实操作（鼠标/键盘/滚动/切回标签页）
    忙碌  页面还开着（心跳正常），但超过 IDLE_AFTER 秒没有操作
    离线  超过 OFFLINE_AFTER 秒没有心跳（页面关了 / 网络断了）

    ★ 为什么"忙碌"用"无操作"来判定：这是唯一**可观测**的信号。
      系统不知道用户是不是真的在开会，只能观察到"页面开着但没人动"。
      所以界面上的措辞是「页面开着但暂时没操作」，而不是断言"此人在开会"。
      把观测说成事实就是编造（§二十六）。

    ★ 阈值选择：
      · 心跳间隔 30 秒 → 离线阈值取 3 倍（90 秒），容忍一次丢包/切网
      · 空闲阈值 5 分钟：正常阅读/思考不会误判，离开工位则会
      这两个数字在 `presence_rules()` 里暴露给前端，界面上直接显示。

═══════════════════════════════════════════════════════════════════════
【为什么"推算"而不是"存储"】
═══════════════════════════════════════════════════════════════════════
    如果把状态算好写进 `users.presence`，就必须有个定时任务不停扫描并更新 ——
    而那需要 Celery / cron（本项目刻意没上，见 §二十三 偏差记录）。
    更关键的是：**推算的结果永远是最新的**，存下来的结果在两次扫描之间必然是旧的。
    所以读的时候按 `last_seen_at` / `last_active_at` 现算。
    ★ 代价是每次读用户列表都要算一遍 —— 纯内存减法，相比查询本身可以忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.user import User
from app.models.user_profile import PresenceStatus

#: 前端心跳间隔（秒）。前端与后端共用这个值，避免两边不一致。
HEARTBEAT_INTERVAL_SECONDS = 30

#: 超过这个时间没有心跳 → 离线。
#: ★ 取心跳间隔的 3 倍：容忍一次网络抖动或标签页被浏览器节流，
#:   否则用户会看到自己状态在"在线/离线"之间反复跳。
OFFLINE_AFTER_SECONDS = 90

#: 超过这个时间没有真实操作 → 忙碌。
#: ★ 5 分钟足够长，正常阅读与思考不会误判；离开工位则会。
IDLE_AFTER_SECONDS = 300

#: 手动覆盖的默认时长（分钟）。界面提供 30 / 60 / 180 分钟。
OVERRIDE_CHOICES_MINUTES = (30, 60, 180)


@dataclass
class PresenceInfo:
    """一个用户当前的实时状态及其**依据**。"""

    status: PresenceStatus
    label: str
    #: ★ 依据说明：告诉用户"为什么是忙碌"，而不是只给一个颜色点
    reason: str
    #: 是否来自手动覆盖（未过期）
    is_override: bool
    #: 覆盖到期时间（仅 is_override 为真时有值）
    override_until: datetime | None
    #: 距今多少秒没有心跳 / 没有操作（无记录时为 None）
    seconds_since_seen: int | None
    seconds_since_active: int | None
    #: 最近活跃时间的可读形式（用于列表展示）
    last_active_at: datetime | None


LABELS: dict[PresenceStatus, str] = {
    PresenceStatus.ONLINE: "在线",
    PresenceStatus.BUSY: "忙碌",
    PresenceStatus.OFFLINE: "离线",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """数据库取回的时间可能是 naive（MySQL DATETIME 不带时区），统一按 UTC 处理。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def presence_rules() -> dict:
    """把判定规则暴露给前端（界面要原文展示，不能只给结论）。"""
    return {
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "offline_after_seconds": OFFLINE_AFTER_SECONDS,
        "idle_after_seconds": IDLE_AFTER_SECONDS,
        "override_choices_minutes": list(OVERRIDE_CHOICES_MINUTES),
        "rules": [
            {
                "status": "ONLINE",
                "label": "在线",
                "rule": f"最近 {IDLE_AFTER_SECONDS // 60} 分钟内有操作（鼠标、键盘、滚动）",
            },
            {
                "status": "BUSY",
                "label": "忙碌",
                "rule": f"页面还开着（心跳正常），但超过 {IDLE_AFTER_SECONDS // 60} 分钟没有操作",
            },
            {
                "status": "OFFLINE",
                "label": "离线",
                "rule": f"超过 {OFFLINE_AFTER_SECONDS} 秒没有心跳（页面关闭或网络中断）",
            },
        ],
        "note": (
            "状态由系统按你的实际使用情况自动推算，不用手动设置。"
            "「忙碌」表示页面开着但暂时没有操作 —— 系统无法知道你是在开会还是离开工位，"
            "只能观察到「页面开着但没动静」这个事实。"
            "你也可以手动覆盖一段时间（到期自动恢复）。"
        ),
    }


def compute_presence(user: User, *, now: datetime | None = None) -> PresenceInfo:
    """算出一个用户此刻的真实状态。

    ★ 手动覆盖优先：只要 `presence_override_until` 还没过期就用覆盖值。
      这样"我在开会"这种系统观测不到的情况也能如实表达。
    """
    moment = now or _now()

    seen = _as_aware(user.last_seen_at)
    active = _as_aware(user.last_active_at)
    until = _as_aware(user.presence_override_until)

    since_seen = int((moment - seen).total_seconds()) if seen else None
    since_active = int((moment - active).total_seconds()) if active else None

    # ① 手动覆盖优先（未过期）
    if until is not None and until > moment:
        status = user.presence
        remain = int((until - moment).total_seconds() // 60)
        return PresenceInfo(
            status=status,
            label=LABELS.get(status, str(status)),
            reason=f"你手动设置的状态，约 {remain} 分钟后自动恢复为系统推算",
            is_override=True,
            override_until=until,
            seconds_since_seen=since_seen,
            seconds_since_active=since_active,
            last_active_at=active,
        )

    # ② 从没发过心跳 → 离线（新账号或从未登录过）
    if seen is None:
        return PresenceInfo(
            status=PresenceStatus.OFFLINE,
            label=LABELS[PresenceStatus.OFFLINE],
            reason="还没有收到过这个账号的心跳（未登录或从未打开页面）",
            is_override=False,
            override_until=None,
            seconds_since_seen=None,
            seconds_since_active=None,
            last_active_at=active,
        )

    # ③ 心跳超时 → 离线
    if since_seen is not None and since_seen > OFFLINE_AFTER_SECONDS:
        mins = since_seen // 60
        reason = (
            f"已有 {mins} 分钟没有心跳（页面已关闭或网络中断）"
            if mins >= 1
            else f"已有 {since_seen} 秒没有心跳"
        )
        return PresenceInfo(
            status=PresenceStatus.OFFLINE,
            label=LABELS[PresenceStatus.OFFLINE],
            reason=reason,
            is_override=False,
            override_until=None,
            seconds_since_seen=since_seen,
            seconds_since_active=since_active,
            last_active_at=active,
        )

    # ④ 心跳正常但长时间无操作 → 忙碌
    if since_active is None or since_active > IDLE_AFTER_SECONDS:
        if since_active is None:
            reason = "页面开着，但还没有记录到操作"
        else:
            reason = f"页面开着，但已 {since_active // 60} 分钟没有操作"
        return PresenceInfo(
            status=PresenceStatus.BUSY,
            label=LABELS[PresenceStatus.BUSY],
            reason=reason,
            is_override=False,
            override_until=None,
            seconds_since_seen=since_seen,
            seconds_since_active=since_active,
            last_active_at=active,
        )

    # ⑤ 心跳正常 + 近期有操作 → 在线
    mins = (since_active or 0) // 60
    reason = "刚刚有操作" if mins < 1 else f"{mins} 分钟前有操作"
    return PresenceInfo(
        status=PresenceStatus.ONLINE,
        label=LABELS[PresenceStatus.ONLINE],
        reason=reason,
        is_override=False,
        override_until=None,
        seconds_since_seen=since_seen,
        seconds_since_active=since_active,
        last_active_at=active,
    )


def record_heartbeat(db: User | None, user: User, *, active: bool) -> PresenceInfo:
    """记录一次心跳。

    ★ `active=True` 表示这次心跳携带了**真实用户操作**（前端的活动监听器触发），
      此时同时刷新 `last_active_at`；否则只刷新 `last_seen_at`。
      把两者分开正是"在线"与"忙碌"能区分开的原因。
    """
    moment = _now()
    user.last_seen_at = moment
    if active:
        user.last_active_at = moment

    # ★ 心跳本身也是一种"人还在"的信号，但**不解除手动覆盖**：
    #   覆盖有明确到期时间，由时间决定何时恢复，不该被心跳意外清掉
    #   （否则用户设了"忙碌 1 小时"，一刷新页面就变回在线，设置形同虚设）。
    db.commit()
    db.refresh(user)
    return compute_presence(user, now=moment)


def set_override(
    db, user: User, status: PresenceStatus, *, minutes: int = 60
) -> PresenceInfo:
    """设置手动覆盖（带到期时间）。

    ★ `minutes` 必须在允许的档位里：不给任意值是为了避免
      "设成 100000 分钟"这种实质上永不过期的用法，
      那等于把自动状态废掉。
    """
    if minutes not in OVERRIDE_CHOICES_MINUTES:
        minutes = 60
    moment = _now()
    user.presence = status
    user.presence_override_until = moment + timedelta(minutes=minutes)
    db.commit()
    db.refresh(user)
    return compute_presence(user, now=moment)


def clear_override(db, user: User) -> PresenceInfo:
    """立即取消手动覆盖，回到自动推算。"""
    user.presence_override_until = None
    db.commit()
    db.refresh(user)
    return compute_presence(user)


def offline_cutoff(*, now: datetime | None = None) -> datetime:
    """离线判定的时间界线（供 SQL 层做批量筛选）。"""
    return (now or _now()) - timedelta(seconds=OFFLINE_AFTER_SECONDS)


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "IDLE_AFTER_SECONDS",
    "LABELS",
    "OFFLINE_AFTER_SECONDS",
    "OVERRIDE_CHOICES_MINUTES",
    "PresenceInfo",
    "clear_override",
    "compute_presence",
    "offline_cutoff",
    "presence_rules",
    "record_heartbeat",
    "set_override",
]
