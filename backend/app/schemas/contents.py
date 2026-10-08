"""营销与内容接口的 Pydantic 契约（TASK-018）。

★ 计数类业务数字走 EvidenceValue（D4）；
  标题、类型、状态、渠道、日期是元数据，返回裸值。
★ view_count 是**匿名聚合计数**，与 customer_events.VISIT 不是一回事
  （详见 models/content.py 的模块说明）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.content import ContentChannel, ContentStatus, ContentType
from app.schemas.evidence import EvidenceValue


class ContentItem(BaseModel):
    """一条内容 / 活动。"""

    id: int
    title: str
    content_type: ContentType
    type_label: str
    status: ContentStatus
    status_label: str
    channel: ContentChannel
    channel_label: str
    published_on: date | None = None
    view_count: EvidenceValue = Field(
        description="浏览量（匿名聚合计数）；未统计为 NO_DATA，不是 0"
    )
    author: str | None = None
    url: str | None = None
    summary: str | None = None
    # ── TASK-038 正文 ──
    body: str | None = Field(default=None, description="正文；可由 AI 生成，也可人工写")
    body_ai_generated: bool = Field(
        default=False, description="正文是否为 AI 生成（尚未人工复核）"
    )
    body_sources: list[dict] = Field(
        default_factory=list, description="AI 生成时引用的知识库片段锚点（可核对）"
    )
    is_active: bool
    evidence_ref: str


class ContentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content_type: ContentType = ContentType.ARTICLE
    status: ContentStatus = ContentStatus.DRAFT
    channel: ContentChannel = ContentChannel.WEBSITE
    published_on: date | None = Field(default=None, description="已发布的内容应填真实发布日期")
    view_count: int | None = Field(default=None, ge=0, description="不填 = 未统计（不是 0）")
    author: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=500)
    summary: str | None = None
    body: str | None = Field(
        default=None,
        description=(
            "正文。★ 刻意不设 max_length：用户明确说过字数是指**整体篇幅目标**，"
            "不是存储或 AI 的限制；数据库用 Text 承载。"
        ),
    )


class ContentUpdateRequest(BaseModel):
    """更新内容。★ status 请走 /status，以便校验流转合法性。"""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    content_type: ContentType | None = None
    channel: ContentChannel | None = None
    published_on: date | None = None
    view_count: int | None = Field(default=None, ge=0)
    author: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=500)
    summary: str | None = None
    body: str | None = Field(default=None, description="人工修改正文；改后会清掉 AI 标记")
    body_ai_generated: bool | None = Field(
        default=None,
        description="★ 人工确认过 AI 正文后可传 false，表示已复核不再是「未复核的 AI 稿」",
    )


class ContentStatusRequest(BaseModel):
    status: ContentStatus
    published_on: date | None = Field(
        default=None, description="转为已发布时可指定发布日期；不填则用今天"
    )


class ContentGenerateRequest(BaseModel):
    """AI 生成正文的入参（TASK-038）。"""

    topic: str = Field(min_length=2, max_length=200, description="写作主题；标题可留空由 AI 起")
    content_type: ContentType = ContentType.ARTICLE
    channel: ContentChannel = ContentChannel.WEBSITE
    target_length: str = Field(
        default="MEDIUM",
        description="篇幅档位 SHORT/MEDIUM/LONG。★ 这是**写作目标区间**，不是字数上限",
    )
    use_knowledge: bool = Field(
        default=True,
        description="true=先检索知识库作为事实依据；false=纯按通用场景写作",
    )
    doc_type: str | None = Field(default=None, description="只在某类知识库资料里检索")
    top_k: int = Field(default=5, ge=1, le=20)


class ContentStatusResponse(BaseModel):
    content: ContentItem
    changed: bool = Field(description="false = 状态本来就等于目标值（no-op）")
    message: str


class ContentListResponse(BaseModel):
    items: list[ContentItem]
    page: int
    page_size: int
    total: EvidenceValue


class ContentTypeSlice(BaseModel):
    key: str
    label: str
    count: int


class ContentChannelSlice(BaseModel):
    key: str
    label: str
    count: int


class ContentSummaryResponse(BaseModel):
    """内容概览。"""

    total: EvidenceValue
    published_total: EvidenceValue
    draft_total: EvidenceValue
    by_type: list[ContentTypeSlice] = Field(default_factory=list)
    by_channel: list[ContentChannelSlice] = Field(default_factory=list)
    total_views: EvidenceValue = Field(
        description="已统计内容的浏览量合计；一条都没统计 → NO_DATA（不是 0）"
    )
    top_contents: list[ContentItem] = Field(default_factory=list, description="浏览量前 5")
    note: str
