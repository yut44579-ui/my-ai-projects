"""客户访问追踪服务（TASK-010）。

═══════════════════════════════════════════════════════════════════════
【访问 = customer_events 的一条 VISIT 事件】
═══════════════════════════════════════════════════════════════════════
不新建表（D11 冻结 7 表）。访问本身就是「客户发生了什么」，与状态变更/消息/接管同类。
结构化补充放 metadata_json：{"page": ..., "channel": ..., "referrer": ..., "note": ...}

═══════════════════════════════════════════════════════════════════════
【只记可识别的访问（§十）】
═══════════════════════════════════════════════════════════════════════
★ 接口必须给 customer_id。匿名访问**不落库** ——
  记下来既不能算指标也不能驱动跟进，还会污染客户动态。

═══════════════════════════════════════════════════════════════════════
【渠道取值】
═══════════════════════════════════════════════════════════════════════
未知渠道**收敛为 OTHER** 而不是报错：渠道来自外部埋点，不该因为多一个新值就丢数据；
但也不许自由文本入库（否则统计会碎成一堆同义字符串）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

#: D6：汇报与日期口径固定 Asia/Shanghai
REPORT_TZ = ZoneInfo("Asia/Shanghai")

from app.models.customer import Customer
from app.models.customer_event import ActorType, CustomerEvent, CustomerEventType, EventSourceType
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.schemas.visits import (
    VISIT_CHANNELS,
    VisitChannelSlice,
    VisitItem,
    VisitPageSlice,
    VisitSummaryResponse,
)

VISIT = CustomerEventType.VISIT


def normalize_channel(raw: str | None) -> str:
    """渠道规范化：未知一律收敛为 OTHER（不报错、也不自由文本入库）。"""
    key = (raw or "WEBSITE").strip().upper()
    return key if key in VISIT_CHANNELS else "OTHER"


def record_visit(
    db: Session,
    customer: Customer,
    *,
    page: str,
    channel: str = "WEBSITE",
    referrer: str | None = None,
    note: str | None = None,
) -> CustomerEvent:
    """记录一次访问（写一条 VISIT 事件）。

    actor_type=SYSTEM：这次访问**不是** AI 也不是人工的动作，而是客户侧行为被系统记录，
    因此不许写成 AI（否则「AI 不许自行改客户状态」那条边界会被污染）。
    """
    meta: dict = {"page": page.strip(), "channel": normalize_channel(channel)}
    if referrer:
        meta["referrer"] = referrer.strip()
    if note:
        meta["note"] = note.strip()

    event = CustomerEvent(
        customer_id=customer.id,
        event_type=VISIT,
        actor_type=ActorType.SYSTEM,
        source_type=EventSourceType.SYSTEM,
        metadata_json=meta,
    )
    db.add(event)
    db.flush()  # 取到自增 id，才能生成证据锚点
    event.evidence_ref = f"customer_event:{event.id}"
    db.commit()
    db.refresh(event)
    return event


def to_item(event: CustomerEvent, customer_name: str) -> VisitItem:
    meta = event.metadata_json or {}
    channel = normalize_channel(meta.get("channel"))
    return VisitItem(
        event_id=event.id,
        customer_id=event.customer_id,
        customer_name=customer_name,
        page=str(meta.get("page") or "—"),
        channel=channel,
        channel_label=VISIT_CHANNELS.get(channel, channel),
        referrer=meta.get("referrer"),
        note=meta.get("note"),
        occurred_at=event.created_at,
        evidence_ref=event.evidence_ref or f"customer_event:{event.id}",
    )


def list_visits(
    db: Session,
    *,
    customer_id: int | None = None,
    channel: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[VisitItem], int]:
    """访问列表（新的在前）+ 总数。

    ★ 渠道筛选在 Python 侧做：渠道存于 metadata_json，MySQL 上对 JSON 做等值筛选
      会因版本/排序规则差异不可靠；访问量级小（V1 单租户），先取回再筛更稳。
      若将来量级上来了，应该在表上开一个生成列并加索引，而不是硬拼 JSON 查询。
    """
    conditions = [CustomerEvent.event_type == VISIT]
    if customer_id is not None:
        conditions.append(CustomerEvent.customer_id == customer_id)

    base = select(CustomerEvent)
    counter = select(func.count(CustomerEvent.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(CustomerEvent.created_at.desc(), CustomerEvent.id.desc())
        .limit(limit if not channel else 500)
        .offset(0 if channel else offset)
    ).all()

    names = {
        cid: name
        for cid, name in db.execute(
            select(Customer.id, Customer.name).where(
                Customer.id.in_([r.customer_id for r in rows] or [0])
            )
        ).all()
    }
    items = [to_item(r, names.get(r.customer_id, f"#{r.customer_id}")) for r in rows]

    if channel:
        want = normalize_channel(channel)
        items = [i for i in items if i.channel == want]
        total = len(items)
        items = items[offset : offset + limit]

    return items[:limit], total


def visits_summary(db: Session, *, days: int = 7) -> VisitSummaryResponse:
    """访问概览：总数 / 今日 / 有访问的客户数 / 热门页面 / 渠道分布 / 近 N 天走势。

    ★ 日期一律按 **Asia/Shanghai** 计算（D6：时区固定）：
      之前用 UTC 算，凌晨时段（UTC 还是前一天）会把"今天"漏出区间 ——
      实测今天的访问落在 0 条、序列却停在前一天。这是真 bug，不是显示问题。
    """
    now = datetime.now(timezone.utc)
    now_local = now.astimezone(REPORT_TZ)
    base_cond = [CustomerEvent.event_type == VISIT]

    total = db.scalar(select(func.count(CustomerEvent.id)).where(*base_cond)) or 0
    visited_customers = (
        db.scalar(select(func.count(func.distinct(CustomerEvent.customer_id))).where(*base_cond)) or 0
    )

    # "今天"的边界取本地零点，再换回 UTC 去比数据库里的时间戳
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = (
        db.scalar(
            select(func.count(CustomerEvent.id)).where(
                *base_cond, CustomerEvent.created_at >= today_start
            )
        )
        or 0
    )

    rows = db.scalars(
        select(CustomerEvent)
        .where(*base_cond)
        .order_by(CustomerEvent.created_at.desc())
        .limit(2000)
    ).all()

    page_counter: dict[str, int] = {}
    channel_counter: dict[str, int] = {}
    daily_counter: dict[str, int] = {}
    start_day = (now_local - timedelta(days=days - 1)).date()
    for ev in rows:
        meta = ev.metadata_json or {}
        page = str(meta.get("page") or "—")
        page_counter[page] = page_counter.get(page, 0) + 1
        ch = normalize_channel(meta.get("channel"))
        channel_counter[ch] = channel_counter.get(ch, 0) + 1
        # ★ 按本地日期归日，而不是 UTC 日期
        if isinstance(ev.created_at, datetime):
            day = ev.created_at.astimezone(REPORT_TZ).date()
            if day >= start_day:
                key = day.isoformat()
                daily_counter[key] = daily_counter.get(key, 0) + 1

    top_pages = [
        VisitPageSlice(page=p, count=c)
        for p, c in sorted(page_counter.items(), key=lambda kv: kv[1], reverse=True)[:6]
    ]
    by_channel = [
        VisitChannelSlice(channel=ch, label=VISIT_CHANNELS.get(ch, ch), count=c)
        for ch, c in sorted(channel_counter.items(), key=lambda kv: kv[1], reverse=True)
    ]
    daily = [
        {"day": (start_day + timedelta(days=i)).isoformat(),
         "count": daily_counter.get((start_day + timedelta(days=i)).isoformat(), 0)}
        for i in range(days)
    ]

    def ev_or_no_data(count: int, ref: str, empty_reason: str):
        if count:
            return build_evidence(count, source_type=SourceType.SYSTEM, evidence_ref=ref)
        return no_data_evidence(empty_reason, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return VisitSummaryResponse(
        total=ev_or_no_data(total, "visits:total", "还没有访问记录"),
        today=ev_or_no_data(today, "visits:today", "今天没有访问"),
        visited_customers=ev_or_no_data(
            visited_customers, "visits:customers", "还没有客户访问过"
        ),
        top_pages=top_pages,
        by_channel=by_channel,
        daily=daily,
        note=(
            "只记录能识别到客户的访问（匿名访问不落库）。"
            "访问作为 customer_events 的 VISIT 事件留痕，证据锚点 = customer_event:{id}。"
        ),
    )
