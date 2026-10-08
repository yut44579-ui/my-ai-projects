"""营销与内容服务（TASK-018）。

═══════════════════════════════════════════════════════════════════════
【状态流转规则（不做工作流引擎，D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    允许：
      · 主线向前：草稿 → 待审核 → 已发布
      · 已发布 → 已下线（UNPUBLISHED）→ 可重新发布
      · 任意状态 → 已归档（ARCHIVED）
      · 已归档 → 草稿（重新启用）
    拒绝：
      · 同状态（no-op，接口返回 changed=false）
      · **待审核 → 草稿**？★ 这是允许的：审核没通过就要退回草稿改，是真实业务流程。
        所以主线内允许"退回草稿"这一条特例，其余向后回退一律拒绝。

    ★ 与商机/项目不同（那两者向后回退一律拒绝），这里刻意留了"退回草稿"：
      内容是要反复改的，审核不通过退回修改是日常；而商机阶段回退没有业务含义。

═══════════════════════════════════════════════════════════════════════
【浏览量】
═══════════════════════════════════════════════════════════════════════
    view_count 由**人工填写或外部统计回填**，本服务**不由 VISIT 事件推算**：
    那 100 次浏览可能来自同一个人，用它当"客户数"是错的（见 models/content.py）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import (
    LIVE_STATUSES,
    TERMINAL_STATUSES,
    ContentChannel,
    ContentStatus,
    ContentType,
    MarketingContent,
)
from app.schemas.contents import (
    ContentChannelSlice,
    ContentItem,
    ContentSummaryResponse,
    ContentTypeSlice,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence

TZ = ZoneInfo("Asia/Shanghai")

TYPE_LABELS: dict[ContentType, str] = {
    ContentType.ARTICLE: "文章",
    ContentType.CASE_STUDY: "客户案例",
    ContentType.WHITEPAPER: "白皮书",
    ContentType.VIDEO: "视频",
    ContentType.EVENT: "活动",
    ContentType.LANDING_PAGE: "落地页",
    ContentType.OTHER: "其他",
}

STATUS_LABELS: dict[ContentStatus, str] = {
    ContentStatus.DRAFT: "草稿",
    ContentStatus.REVIEW: "待审核",
    ContentStatus.PUBLISHED: "已发布",
    ContentStatus.UNPUBLISHED: "已下线",
    ContentStatus.ARCHIVED: "已归档",
}

CHANNEL_LABELS: dict[ContentChannel, str] = {
    ContentChannel.WEBSITE: "官网",
    ContentChannel.WECHAT: "公众号",
    ContentChannel.ZHIHU: "知乎",
    ContentChannel.VIDEO_CHANNEL: "视频号",
    ContentChannel.OFFLINE: "线下活动",
    ContentChannel.OTHER: "其他",
}


class ContentStatusError(Exception):
    """非法状态流转。路由层翻译成 400。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def type_label(t: ContentType) -> str:
    return TYPE_LABELS.get(t, str(t))


def status_label(s: ContentStatus) -> str:
    return STATUS_LABELS.get(s, str(s))


def channel_label(c: ContentChannel) -> str:
    return CHANNEL_LABELS.get(c, str(c))


def evidence_ref_for(content_id: int) -> str:
    return f"content:{content_id}"


def view_evidence(view_count: int | None, ref: str):
    """浏览量 → EvidenceValue。★ NULL（未统计）→ NO_DATA，不是 0。"""
    if view_count is None:
        return no_data_evidence(
            "尚未统计浏览量", source_type=SourceType.MANUAL, evidence_ref=ref
        )
    return build_evidence(view_count, source_type=SourceType.MANUAL, evidence_ref=ref)


def to_item(row: MarketingContent) -> ContentItem:
    ref = row.evidence_ref or evidence_ref_for(row.id)
    return ContentItem(
        id=row.id,
        title=row.title,
        content_type=row.content_type,
        type_label=type_label(row.content_type),
        status=row.status,
        status_label=status_label(row.status),
        channel=row.channel,
        channel_label=channel_label(row.channel),
        published_on=row.published_on,
        view_count=view_evidence(row.view_count, f"{ref}:views"),
        author=row.author,
        url=row.url,
        summary=row.summary,
        # ★ TASK-038：正文与 AI 生成标记一起透出，
        #   让界面能提示"这段是 AI 写的、依据哪几条资料、还没人工复核"
        body=row.body,
        body_ai_generated=row.body_ai_generated,
        body_sources=list(row.body_sources_json or []),
        is_active=row.is_active,
        evidence_ref=ref,
    )


def assert_transition_ok(current: ContentStatus, target: ContentStatus) -> None:
    """校验状态流转。非法则抛 ContentStatusError。"""
    if current == target:
        return

    # 归档是"收纳"，任意状态都能进；归档也能回到草稿重新启用
    if target == ContentStatus.ARCHIVED:
        return
    if current == ContentStatus.ARCHIVED:
        if target != ContentStatus.DRAFT:
            raise ContentStatusError(
                "已归档的内容只能先恢复到草稿，再重新走审核发布流程"
            )
        return

    # 已发布 → 已下线：允许；已下线 → 已发布：允许（重新发布）
    if {current, target} == {ContentStatus.PUBLISHED, ContentStatus.UNPUBLISHED}:
        return

    # 审核不通过退回草稿：允许（这是真实业务流程）
    if current == ContentStatus.REVIEW and target == ContentStatus.DRAFT:
        return

    # 其余：只允许沿主线向前（草稿 → 待审核 → 已发布）
    from app.models.content import STATUS_RANK

    if current in STATUS_RANK and target in STATUS_RANK:
        if STATUS_RANK[target] > STATUS_RANK[current]:
            return
        raise ContentStatusError(
            f"不允许从「{status_label(current)}」回退到「{status_label(target)}」；"
            "审核不通过可以退回草稿，其余回退没有业务含义"
        )

    raise ContentStatusError(
        f"不支持从「{status_label(current)}」变更为「{status_label(target)}」"
    )


def get_content(db: Session, content_id: int) -> MarketingContent | None:
    row = db.get(MarketingContent, content_id)
    if row is None or not row.is_active:
        return None
    return row


def create_content(
    db: Session,
    *,
    title: str,
    content_type: ContentType = ContentType.ARTICLE,
    status: ContentStatus = ContentStatus.DRAFT,
    channel: ContentChannel = ContentChannel.WEBSITE,
    published_on: date | None = None,
    view_count: int | None = None,
    author: str | None = None,
    url: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    body_ai_generated: bool = False,
    body_sources: list[dict] | None = None,
) -> MarketingContent:
    today = datetime.now(timezone.utc).astimezone(TZ).date()
    row = MarketingContent(
        title=title.strip(),
        content_type=content_type,
        status=status,
        channel=channel,
        # ★ 已发布的内容若没给发布日期，用**今天**（这是"何时发布的"事实，不是编造业务数字）
        published_on=(
            published_on if published_on is not None else (today if status in LIVE_STATUSES else None)
        ),
        view_count=view_count,
        author=author,
        url=url,
        summary=summary,
        body=body,
        body_ai_generated=body_ai_generated,
        body_sources_json=body_sources or None,
        is_active=True,
    )
    db.add(row)
    db.flush()
    row.evidence_ref = evidence_ref_for(row.id)
    db.commit()
    db.refresh(row)
    return row


def change_status(
    db: Session,
    row: MarketingContent,
    *,
    target: ContentStatus,
    published_on: date | None = None,
) -> tuple[MarketingContent, bool, str]:
    """流转状态。返回 (内容, 是否变化, 说明)。★ no-op 不写任何变更。"""
    if row.status == target:
        return row, False, f"状态已经是「{status_label(target)}」，未做变更"

    assert_transition_ok(row.status, target)

    previous = row.status
    row.status = target

    if target == ContentStatus.PUBLISHED:
        # 重新发布时保留原发布日期；首次发布用指定值或今天
        if published_on is not None:
            row.published_on = published_on
        elif row.published_on is None:
            row.published_on = datetime.now(timezone.utc).astimezone(TZ).date()

    db.commit()
    db.refresh(row)
    return row, True, f"状态已从「{status_label(previous)}」变更为「{status_label(target)}」"


def update_content(db: Session, row: MarketingContent, **fields) -> MarketingContent:
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def list_contents(
    db: Session,
    *,
    status: ContentStatus | None = None,
    content_type: ContentType | None = None,
    channel: ContentChannel | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ContentItem], int]:
    conditions = [MarketingContent.is_active.is_(True)]
    if status is not None:
        conditions.append(MarketingContent.status == status)
    if content_type is not None:
        conditions.append(MarketingContent.content_type == content_type)
    if channel is not None:
        conditions.append(MarketingContent.channel == channel)

    base = select(MarketingContent)
    counter = select(func.count(MarketingContent.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(MarketingContent.updated_at.desc(), MarketingContent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [to_item(r) for r in rows], total


def build_summary(db: Session) -> ContentSummaryResponse:
    """内容概览。★ 浏览量合计在一条都没统计时是 NO_DATA（不是 0）。"""
    rows = db.scalars(
        select(MarketingContent)
        .where(MarketingContent.is_active.is_(True))
        .order_by(MarketingContent.updated_at.desc(), MarketingContent.id.desc())
    ).all()

    total = len(rows)
    published = sum(1 for r in rows if r.status in LIVE_STATUSES)
    draft = sum(1 for r in rows if r.status == ContentStatus.DRAFT)

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.SYSTEM, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.SYSTEM, evidence_ref=ref)

    type_counter: dict[ContentType, int] = {}
    channel_counter: dict[ContentChannel, int] = {}
    for r in rows:
        type_counter[r.content_type] = type_counter.get(r.content_type, 0) + 1
        channel_counter[r.channel] = channel_counter.get(r.channel, 0) + 1

    known_views = [r.view_count for r in rows if r.view_count is not None]
    total_views = (
        build_evidence(
            sum(known_views), source_type=SourceType.MANUAL, evidence_ref="contents:views:total"
        )
        if known_views
        else no_data_evidence(
            "还没有内容统计过浏览量", source_type=SourceType.MANUAL, evidence_ref="contents:views:total"
        )
    )

    top = sorted(
        (r for r in rows if r.view_count is not None),
        key=lambda r: r.view_count or 0,
        reverse=True,
    )[:5]

    return ContentSummaryResponse(
        total=ev_or_nodata(total, "contents:count", "还没有内容"),
        published_total=ev_or_nodata(published, "contents:published:count", "还没有内容"),
        draft_total=ev_or_nodata(draft, "contents:draft:count", "还没有内容"),
        by_type=[
            ContentTypeSlice(key=k.value, label=type_label(k), count=v)
            for k, v in sorted(type_counter.items(), key=lambda kv: kv[1], reverse=True)
        ],
        by_channel=[
            ContentChannelSlice(key=k.value, label=channel_label(k), count=v)
            for k, v in sorted(channel_counter.items(), key=lambda kv: kv[1], reverse=True)
        ],
        total_views=total_views,
        top_contents=[to_item(r) for r in top],
        note=(
            "浏览量是匿名聚合计数（一篇内容被看了多少次），"
            "与「客户访问」不是同一份数据——不能由它推算有多少客户看过。"
        ),
    )
