"""客户去重与行级校验（TASK-001 §二，本轮最危险的点）。

★★ 必须同时防 SQL 侧与 Python 侧：
   MySQL 里 NULL = NULL 得到 NULL（不命中）；但 Python 里 None == None 是 True
   → 一旦用 Python 比较，空邮箱/空电话的行会全表互相误合并。
   本实现的防法：**空值根本不构造查询条件**（`if email:` / `if phone:`），
   候选一律由 SQL 查询产生，Python 侧从不做 None == None 的比较。

判定（★ 与 §二 的差异见 docs/DECISIONS.md D12：§二写"0 条 → CLEAN"，而 AC3c 要求
"email/phone 都空的两行各自 PENDING_REVIEW"。二者只在"有没有可比的键"上冲突，
本实现取 AC 口径：有键但查不到 → CLEAN；压根没有键可比 → PENDING_REVIEW 交人工）：
   有键、0 候选 → 新建，CLEAN
   有键、1 候选 → 命中：刷新 last_seen_at，不新建（计入 rows_deduplicated）
   有键、≥2 候选 → ★冲突：不合并！新建并 PENDING_REVIEW，记录冲突的既有客户 id
   无键（email/phone 都空） → 新建并 PENDING_REVIEW（无法判重，交人工）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType, DedupeState
from app.models.import_batch import SkipReason

TZ = ZoneInfo("Asia/Shanghai")

# 电话：只留数字后，长度 7~20（含区号/国家码的固话与手机都覆盖）
PHONE_RE = re.compile(r"^\d{7,20}$")
# 邮箱：宽松但明确（必须有 @ 与点）
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ACTION_CREATED = "created"
ACTION_DEDUPLICATED = "deduplicated"


def now_local() -> datetime:
    """D6 固定时区 Asia/Shanghai，落库为 naive datetime（与列类型一致）。"""
    return datetime.now(TZ).replace(tzinfo=None)


def is_blank(value: object) -> bool:
    """NULL / 空串 / 全空白 一律视为"没有值"。"""
    return value is None or (isinstance(value, str) and value.strip() == "")


def normalize_email(value: object) -> str | None:
    """去空格 + 转小写；空白 → None（★ 绝不返回空串，空值只以 NULL 存在）。"""
    if is_blank(value):
        return None
    return str(value).strip().lower()


def normalize_phone(value: object) -> str | None:
    """只留数字；没有数字 → None。"""
    if is_blank(value):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


@dataclass
class CustomerRow:
    """一行"业务字段"（已经是字符串，且只含被映射到的列）。"""

    name: str
    company_name: str | None = None
    phone: str | None = None
    email: str | None = None
    region: str | None = None
    note: str | None = None
    source_row: int = 0  # 文件里的物理行号（1 起），仅用于报错定位

    def key_values(self) -> dict[str, str | None]:
        return {"email": normalize_email(self.email), "phone": normalize_phone(self.phone)}


@dataclass
class RowIssue:
    """一行被判为坏行的原因（写进 skipped_reasons_json）。"""

    row: int
    reason: SkipReason
    raw: str

    def to_json(self) -> dict:
        return {"row": self.row, "reason": self.reason.value, "raw": self.raw}


@dataclass
class ImportContext:
    """落库上下文：批次号与来源类型（由 importer 提供；单测可留空）。"""

    batch_id: int | None = None
    source_type: CustomerSourceType = CustomerSourceType.MANUAL


@dataclass
class ResolveResult:
    """resolve_customer 的返回：customer / action / dedupe_state（+ 冲突客户 id）。"""

    customer: Customer | None
    action: str
    dedupe_state: DedupeState
    conflict_ids: list[int] = field(default_factory=list)

    def __iter__(self):
        """允许 `customer, action, state = resolve_customer(...)` 这样解包。"""
        return iter((self.customer, self.action, self.dedupe_state))


def validate_row(fields: dict[str, str], source_row: int) -> tuple[CustomerRow | None, RowIssue | None]:
    """行级校验：坏行不半入库，整行跳过并给出冻结枚举里的原因。

    顺序固定：empty_row → missing_name → invalid_email → invalid_phone，
    保证同一行每次报同一个原因（可复现）。
    """
    values = {key: ("" if value is None else str(value).strip()) for key, value in fields.items()}

    if all(is_blank(v) for v in values.values()):
        return None, RowIssue(row=source_row, reason=SkipReason.EMPTY_ROW, raw="")

    name = values.get("name", "")
    if is_blank(name):
        return None, RowIssue(row=source_row, reason=SkipReason.MISSING_NAME, raw=name)

    raw_email = values.get("email", "")
    if not is_blank(raw_email) and not EMAIL_RE.match(normalize_email(raw_email) or ""):
        return None, RowIssue(row=source_row, reason=SkipReason.INVALID_EMAIL, raw=raw_email[:200])

    raw_phone = values.get("phone", "")
    if not is_blank(raw_phone) and not PHONE_RE.match(normalize_phone(raw_phone) or ""):
        return None, RowIssue(row=source_row, reason=SkipReason.INVALID_PHONE, raw=raw_phone[:200])

    row = CustomerRow(
        name=name,
        company_name=values.get("company_name") or None,
        phone=normalize_phone(raw_phone),
        email=normalize_email(raw_email),
        region=values.get("region") or None,
        note=values.get("note") or None,
        source_row=source_row,
    )
    return row, None


def _find_candidates(row: CustomerRow, db: Session) -> list[Customer]:
    """候选既有客户。★ 空值不构造条件 —— 这是 SQL 侧与 Python 侧双保险的关键。"""
    email = normalize_email(row.email)
    phone = normalize_phone(row.phone)

    found: dict[int, Customer] = {}
    if email:
        for customer in db.execute(select(Customer).where(Customer.email == email)).scalars():
            found[customer.id] = customer
    if phone:
        for customer in db.execute(select(Customer).where(Customer.phone == phone)).scalars():
            found[customer.id] = customer
    return [found[key] for key in sorted(found)]


def _new_customer(row: CustomerRow, state: DedupeState, context: ImportContext) -> Customer:
    stamp = now_local()
    customer = Customer(
        name=row.name,
        company_name=row.company_name,
        phone=normalize_phone(row.phone),
        email=normalize_email(row.email),
        region=row.region,
        note=row.note,
        batch_id=context.batch_id,
        source_type=context.source_type,
        dedupe_state=state,
        first_seen_at=stamp,
        last_seen_at=stamp,
    )
    # ★ evidence_ref 由代码生成，禁止手填：batch_id 与之一致
    customer.evidence_ref = f"import_batch:{context.batch_id}" if context.batch_id else None
    return customer


def resolve_customer(
    row: CustomerRow,
    db: Session,
    context: ImportContext | None = None,
) -> ResolveResult:
    """一行 → (customer, action, dedupe_state)。命中/冲突的判定见模块头注释。"""
    context = context or ImportContext()

    # session 关了 autoflush（见 db/session.py），这里手动 flush：
    # 同一批次里先新增的客户必须对本行的候选查询可见，否则 AC3a 会退化成两条。
    db.flush()

    candidates = _find_candidates(row, db)

    if len(candidates) == 1:
        # 命中唯一既有客户：只刷新 last_seen_at，不做别的
        hit = candidates[0]
        hit.last_seen_at = now_local()
        return ResolveResult(customer=hit, action=ACTION_DEDUPLICATED, dedupe_state=DedupeState.CLEAN)

    if len(candidates) >= 2:
        # ★ 冲突：绝不自动合并 —— 新建 + PENDING_REVIEW，并记下冲突的既有客户 id
        customer = _new_customer(row, DedupeState.PENDING_REVIEW, context)
        db.add(customer)
        db.flush()  # 冲突记录要带新客户 id，先落 id 再返回
        return ResolveResult(
            customer=customer,
            action=ACTION_CREATED,
            dedupe_state=DedupeState.PENDING_REVIEW,
            conflict_ids=[c.id for c in candidates],
        )

    # 0 候选：有可比键 → 独立新建 CLEAN；压根没有可比键 → 无法判重，交人工
    no_keys = normalize_email(row.email) is None and normalize_phone(row.phone) is None
    state = DedupeState.PENDING_REVIEW if no_keys else DedupeState.CLEAN
    customer = _new_customer(row, state, context)
    db.add(customer)
    # 无键行会被记成"等人工裁决"，记录里要带新客户 id，所以先把 id 落出来
    if no_keys:
        db.flush()
    return ResolveResult(customer=customer, action=ACTION_CREATED, dedupe_state=state)
