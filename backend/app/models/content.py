"""营销与内容模型（TASK-018）。★ D28 解冻新增的表。

═══════════════════════════════════════════════════════════════════════
【与 customer_events.VISIT（TASK-010）的区别 —— 极易混淆，务必分清】
═══════════════════════════════════════════════════════════════════════
    customer_events.VISIT  = 「**某个可识别的客户**访问了某个页面」
                              → 能归属到人，能驱动跟进（所以只记可识别的，D20）
    contents.view_count    = 「这篇内容**被看了多少次**」（匿名聚合计数）
                              → 归不到人，只是内容效果指标

两者**不是同一份数据的不同视图**，绝不能互相换算：
    不能因为 view_count=100 就说"有 100 个客户看过"（那 100 次可能来自同一人）。

★ 因此 view_count 由人工填写或后续由外部统计回填，**不由 VISIT 事件推算**。

═══════════════════════════════════════════════════════════════════════
【内容与客户没有从属关系】
═══════════════════════════════════════════════════════════════════════
    内容/活动是**产出物**，不挂在某个客户下（与商机、项目的语义都不同）。
    所以本表没有 customer_id。

═══════════════════════════════════════════════════════════════════════
【不做工作流引擎（D8 同一原则）】
═══════════════════════════════════════════════════════════════════════
    发布状态流转用固定枚举 + 显式接口，合法性写死在 services/content.py。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class ContentType(str, Enum):
    """内容类型。★ 冻结枚举，禁止自由文本。"""

    ARTICLE = "ARTICLE"        # 文章
    CASE_STUDY = "CASE_STUDY"  # 客户案例
    WHITEPAPER = "WHITEPAPER"  # 白皮书
    VIDEO = "VIDEO"            # 视频
    EVENT = "EVENT"            # 活动（线上/线下）
    LANDING_PAGE = "LANDING_PAGE"  # 落地页
    OTHER = "OTHER"


class ContentStatus(str, Enum):
    """发布状态。★ 冻结枚举。

    DRAFT → REVIEW → PUBLISHED → ARCHIVED；UNPUBLISHED 是"发布后下线"。
    """

    DRAFT = "DRAFT"              # 草稿
    REVIEW = "REVIEW"            # 待审核
    PUBLISHED = "PUBLISHED"      # 已发布
    UNPUBLISHED = "UNPUBLISHED"  # 已下线
    ARCHIVED = "ARCHIVED"        # 已归档


class ContentChannel(str, Enum):
    """发布渠道。与 VISIT_CHANNELS 同名但语义不同：

    VISIT_CHANNELS 答的是「**客户**从哪个渠道来」（获客来源），
    ContentChannel 答的是「**内容**发在哪个渠道」（分发渠道）。
    ★ 刻意不共用：共用会让"官网"这个词在两张表里有两种含义，统计时必错。
    """

    WEBSITE = "WEBSITE"        # 官网
    WECHAT = "WECHAT"          # 公众号
    ZHIHU = "ZHIHU"            # 知乎
    VIDEO_CHANNEL = "VIDEO_CHANNEL"  # 视频号
    OFFLINE = "OFFLINE"        # 线下活动
    OTHER = "OTHER"


#: 主线推进顺序（UNPUBLISHED 可从 PUBLISHED 进入，也可重新发布）
STATUS_ORDER: tuple[ContentStatus, ...] = (
    ContentStatus.DRAFT,
    ContentStatus.REVIEW,
    ContentStatus.PUBLISHED,
)
STATUS_RANK: dict[ContentStatus, int] = {s: i + 1 for i, s in enumerate(STATUS_ORDER)}

#: 已发布过的状态（这类内容可以有发布时间与浏览量）
LIVE_STATUSES: frozenset[ContentStatus] = frozenset(
    {ContentStatus.PUBLISHED, ContentStatus.UNPUBLISHED, ContentStatus.ARCHIVED}
)

#: 终态（归档后要重新用必须先回到草稿）
TERMINAL_STATUSES: frozenset[ContentStatus] = frozenset({ContentStatus.ARCHIVED})


class MarketingContent(Base):
    """一条内容 / 活动。"""

    __tablename__ = "marketing_contents"

    __table_args__ = (
        Index("ix_marketing_contents_status", "status"),
        Index("ix_marketing_contents_type_channel", "content_type", "channel"),
        Index("ix_marketing_contents_published", "published_on"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)

    content_type: Mapped[ContentType] = mapped_column(
        SAEnum(ContentType, name="content_type", values_callable=_enum_values),
        nullable=False,
        default=ContentType.ARTICLE,
    )
    status: Mapped[ContentStatus] = mapped_column(
        SAEnum(ContentStatus, name="content_status", values_callable=_enum_values),
        nullable=False,
        default=ContentStatus.DRAFT,
    )
    channel: Mapped[ContentChannel] = mapped_column(
        SAEnum(ContentChannel, name="content_channel", values_callable=_enum_values),
        nullable=False,
        default=ContentChannel.WEBSITE,
    )

    published_on: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="发布日期；未发布为 NULL（不是今天）"
    )
    #: ★ 人工填写或外部统计回填；**不由 VISIT 事件推算**（见模块 docstring）
    view_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="浏览量；NULL = 未统计（与 0 不同）"
    )

    author: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="作者/负责人")
    url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="内容链接")
    #: 简介/摘要；与 body 是两件事：摘要是列表页展示的一两句话
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── TASK-038 正文 ──
    #: 正文（可用 AI 生成，也可人工写）。
    #: ★ 用 Text 而不是 VARCHAR(n)：MySQL 的 VARCHAR 上限受行大小限制，
    #:   一篇长文很容易超；Text 也能避免"字数限制"其实是**存储限制**的误解。
    #: ★ 用户明确说过：字数是指**整体范围**（一篇文章合理多长），
    #:   不是给 AI 的生成上限 —— 所以这里只做存储，长度由业务目标决定。
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 正文是否由 AI 生成（未人工修改过）
    #: ★ 记录这个是为了让人知道"这段文字需要人工复核"，
    #:   而不是把 AI 生成的内容当人工撰写的成果（§二十六 不把建议说成已实现）。
    body_ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    #: AI 生成时引用了哪些知识库片段（证据锚点列表，便于核对"这段是从哪来的"）
    body_sources_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="[{evidence_ref, document_title, seq}]"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    evidence_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="证据锚点 = content:{id}"
    )

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MarketingContent id={self.id} {self.title!r} {self.status}>"
