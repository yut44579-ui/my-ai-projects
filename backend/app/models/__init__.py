"""ORM 模型包。

D11：V1 数据模型冻结 7 表 —— users / customers / customer_messages /
customer_events / risk_events / import_batches / reports。
已落地：import_batches、customers（TASK-001）、customer_events（TASK-006）。

★ 模型必须在包内 import 一次，Alembic autogenerate 才能看到表（migrations/env.py 只 import 本包）。
"""

from app.db.base import Base
# ★ 并行线合并（integration）：各线导出取并集，四条线一个都不丢。
#   handover.*        —— TASK-005：人工接管三态 + 接管事件
#   customer_event.*  —— TASK-006：客户状态机通用事件
#   customer.LifecycleStatus —— TASK-006：生命周期状态枚举
#   customer_message.* —— TASK-003/004：沟通记录 + AI 回复
#   report.*          —— TASK-007：自动汇报
from app.models.customer import (
    AcquisitionChannel,
    Customer,
    CustomerSourceType,
    DedupeState,
    LifecycleStatus,
)
from app.models.customer_event import (
    ActorType,
    CustomerEvent,
    CustomerEventType,
    EventSourceType,
)
from app.models.customer_message import (
    AiStatus,
    CustomerMessage,
    MessageSourceType,
    MessageType,
    SenderType,
    message_evidence_ref,
)
from app.models.content import (
    LIVE_STATUSES,
    STATUS_ORDER as CONTENT_STATUS_ORDER,
    TERMINAL_STATUSES,
    ContentChannel,
    ContentStatus,
    ContentType,
    MarketingContent,
)
from app.models.handover import (
    CustomerHandoverEvent,
    HandoverActorType,
    HandoverEventType,
    HandoverState,
)
from app.models.import_batch import (
    BatchSourceType,
    ImportBatch,
    ImportBatchStatus,
    SkipReason,
)
from app.models.report import REPORT_TIMEZONE, Report, ReportSourceType, ReportType
from app.models.risk_event import RiskEvent, RiskEventType, RiskLevel, RiskStatus
from app.models.user import User, UserRole
from app.models.user_profile import (
    PROVIDER_PRESETS,
    AiProtocol,
    AiProviderConfig,
    AiProviderKind,
    PresenceStatus,
    ThemePreference,
)
from app.models.prospect_research import (
    DraftStatus,
    ProspectResearch,
    ResearchSourceType,
    ResearchTargetType,
)
from app.models.data_connection import (
    ConnectionStatus,
    ConnectorKind,
    DataConnection,
    SyncRecord,
    SyncResult,
    SyncTrigger,
)
from app.models.proposal import Proposal, ProposalKind, ProposalStatus
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocType,
    KnowledgeDocument,
    KnowledgeSourceType,
)
from app.models.opportunity import (
    CLOSED_STAGES,
    STAGE_ORDER,
    STAGE_RANK,
    WON_STAGE,
    Opportunity,
    OpportunityPriority,
    OpportunityStage,
)
from app.models.project import (
    ACTIVE_STATUSES,
    CLOSED_STATUSES,
    STATUS_ORDER,
    STATUS_RANK,
    Project,
    ProjectCustomer,
    ProjectPriority,
    ProjectStatus,
)

__all__ = [
    "AcquisitionChannel",
    "ActorType",
    "AiStatus",
    "Base",
    "BatchSourceType",
    "ContentChannel",
    "ContentStatus",
    "ContentType",
    "ConnectionStatus",
    "ConnectorKind",
    "Customer",
    "CustomerEvent",
    "CustomerEventType",
    "CustomerHandoverEvent",
    "CustomerMessage",
    "CustomerSourceType",
    "DataConnection",
    "DraftStatus",
    "DedupeState",
    "EventSourceType",
    "HandoverActorType",
    "HandoverEventType",
    "HandoverState",
    "ImportBatch",
    "ImportBatchStatus",
    "KnowledgeChunk",
    "KnowledgeDocType",
    "KnowledgeDocument",
    "KnowledgeSourceType",
    "LifecycleStatus",
    "MarketingContent",
    "MessageSourceType",
    "MessageType",
    "Opportunity",
    "OpportunityPriority",
    "OpportunityStage",
    "Project",
    "Proposal",
    "ProposalKind",
    "ProposalStatus",
    "ProspectResearch",
    "ProjectCustomer",
    "ProjectPriority",
    "ProjectStatus",
    "REPORT_TIMEZONE",
    "Report",
    "ReportSourceType",
    "ResearchSourceType",
    "ResearchTargetType",
    "ReportType",
    "RiskEvent",
    "RiskEventType",
    "RiskLevel",
    "RiskStatus",
    "SenderType",
    "SkipReason",
    "SyncRecord",
    "SyncResult",
    "SyncTrigger",
    "User",
    "UserRole",
    "AiProtocol",
    "AiProviderConfig",
    "AiProviderKind",
    "PresenceStatus",
    "ThemePreference",
    "UserRole",
    "message_evidence_ref",
]
