"""知识库服务（TASK-026，需求 §二十二）。

═══════════════════════════════════════════════════════════════════════
【§二十二 第 ② 条怎么落地：知识 ≠ 业务事实】
═══════════════════════════════════════════════════════════════════════
    §二十二 原文："但不能把知识库内容伪装成真实业务事实。"

    落地方式（**结构性**，不是靠提示词）：
      · 存储：知识在 knowledge_documents/chunks，业务数据在 customers/opportunities/…
        两张完全不同的表，没有互相冒充的字段。
      · 返回：`analyze()` 的响应把两类内容分成两个字段返回：
          knowledge_used —— 命中的资料片段（带 knowledge_chunk:{id} 锚点）
          business_data  —— 真实业务数字（EvidenceValue，带各自锚点）
      · 提示词：明确规定"资料内容属于背景知识，**不得说成是本公司的业务事实**"，
        且模型输出里的每条结论要能对应到上面某一类。

═══════════════════════════════════════════════════════════════════════
【检索：关键词 + 标签 + 生效期，不用向量库（§五）】
═══════════════════════════════════════════════════════════════════════
    §五 明确禁止向量数据库 / RAG Pipeline，用户也裁定走 A 方案。
    所以：
      · 把查询切成词（中文按字符 2-gram + 整串，英文按单词）
      · 每个切片按"命中了几个词"打分，命中整串额外加权
      · 支持按 doc_type / 标签 / 生效期过滤
    ★ 优点：不需要额外模型、不需要外部服务、**命中原因可解释**
      （"这段包含了你问的词"），人工能核对为什么这段被选中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.customer import Customer, CustomerSourceType
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocType,
    KnowledgeDocument,
    KnowledgeSourceType,
)
from app.models.opportunity import CLOSED_STAGES, Opportunity
from app.services.llm import LLMUnavailable, generate_reply

#: 切片长度上限（字符）。太长会让喂给模型的上下文过大，太短会丢语义。
CHUNK_MAX_CHARS = 500
#: 小于这个长度的片段并进上一片（避免"一句话一片"这种碎片）
CHUNK_MIN_CHARS = 60
#: 检索默认返回的片段数上限
DEFAULT_TOP_K = 6

#: 分析用提示词。★ 关键：要求模型把"资料"与"业务数据"分开陈述。
ANALYZE_SYSTEM_PROMPT = """你是商业分析助手。你会收到两类**性质完全不同**的输入：

【A. 参考资料】—— 这是从知识库里检索出来的文本片段，属于**背景知识**。
    ★ 它们**不是本公司的业务事实**，不许说成"我们的数据表明…"。
    引用时要说清是"资料里提到…"。

【B. 真实业务数据】—— 这是系统从数据库里查出来的**真实数字**。
    可以当作事实陈述，但**不许改动、不许推算新数字**。

【铁律】
1. 严格区分 A 与 B：不许把资料内容当作公司业务事实陈述。
2. 不许编造任何数字。业务数据里没有的数字一律不提。
3. 不许给业务承诺（价格/折扣/交期/账期/赔偿）。
4. 如果资料与业务数据都不足以回答问题，就**明确说信息不足**，不要硬答。
5. 只输出 JSON，不要解释文字：
{
  "analysis": "分析正文（3~6 句）",
  "from_knowledge": ["哪几条结论来自参考资料"],
  "from_business_data": ["哪几条结论来自真实业务数据"],
  "gaps": ["还缺什么信息才能给出更完整结论"]
}"""


class KnowledgeError(Exception):
    """知识库相关业务错误（路由层翻译成 400）。"""


@dataclass
class SearchHit:
    """一条检索命中。"""

    chunk_id: int
    document_id: int
    document_title: str
    doc_type: KnowledgeDocType
    seq: int
    content: str
    score: int
    matched_terms: list[str]
    evidence_ref: str


@dataclass
class AnalyzeOutcome:
    analysis: str = ""
    from_knowledge: list[str] = field(default_factory=list)
    from_business_data: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)
    business_data: dict = field(default_factory=dict)
    llm_called: bool = False
    llm_error: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 切片
# ══════════════════════════════════════════════════════════════════════

def split_into_chunks(text: str) -> list[tuple[str, int, int]]:
    """把原文切成片段，返回 [(content, char_start, char_end)]。

    ★ 核心不变量：**content 必须精确等于 text[char_start:char_end]**。
      这样"片段能回溯到原文"才是可验证的事实，而不是近似。
      （实现方式：先把要合并的区间在原文里算准，最后再按区间取子串，
        而不是把若干 strip 过的段落拼起来 —— 拼接会因空白差异破坏偏移。）

    规则（简单可解释，不引入分词器）：
      · 先按行分段（段落是文本天然的语义边界）
      · 过短的段落向后合并，直到达到 CHUNK_MIN_CHARS
      · 超过 CHUNK_MAX_CHARS 的段落按句末标点再切
    """
    normalized = text or ""
    if not normalized.strip():
        return []

    # 第一步：按行切段，保留每段在原文中的**精确**区间
    segments: list[tuple[int, int]] = []
    offset = 0
    for line in normalized.splitlines(keepends=True):
        start = offset
        offset += len(line)
        if line.strip():
            segments.append((start, offset))

    if not segments:
        return []

    chunks: list[tuple[int, int]] = []  # 先只算区间，最后统一取原文子串

    def emit(start: int, end: int) -> None:
        if end > start and normalized[start:end].strip():
            chunks.append((start, end))

    # 第二步：合并短段 / 细切超长段（全程只操作区间）
    buf_start: int | None = None
    buf_end: int | None = None

    for seg_start, seg_end in segments:
        seg_len = seg_end - seg_start

        if seg_len > CHUNK_MAX_CHARS:
            # 超长段：先把缓冲落盘，再按句末标点细切
            if buf_start is not None and buf_end is not None:
                emit(buf_start, buf_end)
                buf_start = buf_end = None

            seg_text = normalized[seg_start:seg_end]
            piece_offset = seg_start
            for piece in re.split(r"(?<=[。！？；.!?;])", seg_text):
                if not piece:
                    continue
                piece_end = piece_offset + len(piece)
                if piece.strip():
                    emit(piece_offset, piece_end)
                piece_offset = piece_end
            continue

        if buf_start is None:
            buf_start, buf_end = seg_start, seg_end
        else:
            combined_len = seg_end - buf_start
            if combined_len <= CHUNK_MAX_CHARS:
                buf_end = seg_end
            else:
                emit(buf_start, buf_end)  # type: ignore[arg-type]
                buf_start, buf_end = seg_start, seg_end

        # 攒够最小长度就落盘，避免"一句话一片"的碎片
        if buf_start is not None and buf_end is not None and (buf_end - buf_start) >= CHUNK_MIN_CHARS:
            emit(buf_start, buf_end)
            buf_start = buf_end = None

    if buf_start is not None and buf_end is not None:
        emit(buf_start, buf_end)

    # ★ 最后统一按区间取原文子串 —— 保证 content == text[start:end]
    return [(normalized[s:e], s, e) for s, e in chunks]


def import_document(
    db: Session,
    *,
    title: str,
    doc_type: KnowledgeDocType,
    source_text: str,
    source_type: KnowledgeSourceType = KnowledgeSourceType.PASTED,
    source_note: str | None = None,
    tags: list[str] | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
) -> KnowledgeDocument:
    """导入一份资料：**保存原文** + 切片。

    ★ 原文必须够长：太短的资料切不出有意义的片段，也支撑不了分析。
    """
    text = (source_text or "").strip()
    if len(text) < 50:
        raise KnowledgeError("资料原文太短（至少 50 字）：切不出有意义的片段，也无法支撑分析")

    doc = KnowledgeDocument(
        title=title.strip(),
        doc_type=doc_type,
        source_type=source_type,
        source_note=source_note,
        source_text=text,
        tags_json=[t.strip() for t in (tags or []) if t.strip()] or None,
        effective_from=effective_from,
        effective_to=effective_to,
        is_active=True,
    )
    db.add(doc)
    db.flush()

    pieces = split_into_chunks(text)
    for i, (content, start, end) in enumerate(pieces, start=1):
        chunk = KnowledgeChunk(
            document_id=doc.id,
            seq=i,
            content=content,
            char_start=start,
            char_end=end,
        )
        db.add(chunk)
        db.flush()
        chunk.evidence_ref = f"knowledge_chunk:{chunk.id}"

    doc.chunk_count = len(pieces)
    db.commit()
    db.refresh(doc)
    return doc


# ══════════════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════════════

def extract_terms(query: str) -> list[str]:
    """把查询切成检索词。

    · 英文/数字：按非字母数字切，保留长度 ≥2 的词
    · 中文：取 2-gram（中文没有空格，"算力服务" → 算力/力服/服务）
      ★ 不用分词器，避免引依赖；2-gram 对中文检索够用且完全可解释。
    """
    q = (query or "").strip()
    if not q:
        return []

    terms: list[str] = []
    # 英文单词与数字
    for w in re.findall(r"[A-Za-z0-9_]{2,}", q):
        terms.append(w.lower())
    # 中文 2-gram（只对连续的汉字段做，避免跨标点拼词）
    for seg in re.findall(r"[\u4e00-\u9fa5]{2,}", q):
        for i in range(len(seg) - 1):
            terms.append(seg[i : i + 2])

    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def search(
    db: Session,
    query: str,
    *,
    doc_type: KnowledgeDocType | None = None,
    tag: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    include_expired: bool = False,
) -> list[SearchHit]:
    """关键词检索。

    ★ 打分规则完全可解释：命中词数 + 命中整串的额外加权。
      返回结果带 matched_terms，让人能看懂"为什么这段被选中"。
    """
    terms = extract_terms(query)
    if not terms:
        return []

    today = datetime.now(timezone.utc).date()

    stmt = (
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeDocument.is_active.is_(True))
    )
    if doc_type is not None:
        stmt = stmt.where(KnowledgeDocument.doc_type == doc_type)
    if not include_expired:
        # 失效资料默认不参与检索：过期信息不该继续影响判断
        stmt = stmt.where(
            or_(
                KnowledgeDocument.effective_to.is_(None),
                KnowledgeDocument.effective_to >= today,
            )
        )
    # 先用 SQL 粗筛：任一命中词出现在切片里（缩小候选集）
    stmt = stmt.where(or_(*[KnowledgeChunk.content.like(f"%{t}%") for t in terms]))

    rows = db.execute(stmt).all()

    hits: list[SearchHit] = []
    phrase = (query or "").strip().lower()
    for chunk, doc in rows:
        content_lower = (chunk.content or "").lower()
        matched = [t for t in terms if t in content_lower]
        if not matched:
            continue
        score = len(matched)
        # 整串命中额外加权：说明这段和问题高度相关，不只是零散命中几个字
        if len(phrase) >= 3 and phrase in content_lower:
            score += 3 * len(matched)
        hits.append(
            SearchHit(
                chunk_id=chunk.id,
                document_id=doc.id,
                document_title=doc.title,
                doc_type=doc.doc_type,
                seq=chunk.seq,
                content=chunk.content,
                score=score,
                matched_terms=matched,
                evidence_ref=chunk.evidence_ref or f"knowledge_chunk:{chunk.id}",
            )
        )

    # 标签过滤放在 Python 侧：标签是 JSON 列，跨数据库的 JSON 查询不可靠
    if tag:
        allowed_ids = {
            d.id
            for d in db.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True))
            ).all()
            if tag in (d.tags_json or [])
        }
        hits = [h for h in hits if h.document_id in allowed_ids]

    hits.sort(key=lambda h: (-h.score, h.document_id, h.seq))
    return hits[:top_k]


def list_documents(
    db: Session,
    *,
    doc_type: KnowledgeDocType | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[KnowledgeDocument], int]:
    conditions = [KnowledgeDocument.is_active.is_(True)]
    if doc_type is not None:
        conditions.append(KnowledgeDocument.doc_type == doc_type)

    base = select(KnowledgeDocument)
    counter = select(func.count(KnowledgeDocument.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_document(db: Session, document_id: int) -> KnowledgeDocument | None:
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None or not doc.is_active:
        return None
    return doc


def list_chunks(db: Session, document_id: int) -> list[KnowledgeChunk]:
    return list(
        db.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.seq.asc())
        ).all()
    )


# ══════════════════════════════════════════════════════════════════════
# 结合真实业务数据做分析
# ══════════════════════════════════════════════════════════════════════

def collect_business_data(db: Session) -> dict:
    """取一小份**真实业务数据**作为分析的业务侧输入。

    ★ 只用真实的计数，**不推算、不估计**。取不到的项不放进结果
      （而不是填 0 —— 0 和"没取到"是两件事）。
    """
    data: dict = {}

    real_customers = db.scalar(
        select(func.count(Customer.id)).where(Customer.source_type == CustomerSourceType.REAL)
    ) or 0
    total_customers = db.scalar(select(func.count(Customer.id))) or 0
    if total_customers:
        data["customers_total"] = total_customers
        data["customers_real"] = real_customers

    open_opps = db.scalar(
        select(func.count(Opportunity.id)).where(
            Opportunity.is_active.is_(True), Opportunity.stage.notin_(list(CLOSED_STAGES))
        )
    )
    if open_opps:
        data["opportunities_open"] = open_opps

    return data


def analyze(
    db: Session,
    *,
    question: str,
    doc_type: KnowledgeDocType | None = None,
    tag: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> AnalyzeOutcome:
    """结合【知识库片段】与【真实业务数据】回答问题。

    ★ 两类输入在 prompt 里分段标注，在响应里也分成两个字段返回 ——
      防止"把知识库内容伪装成真实业务事实"（§二十二）。
    """
    outcome = AnalyzeOutcome()
    outcome.hits = search(db, question, doc_type=doc_type, tag=tag, top_k=top_k)
    outcome.business_data = collect_business_data(db)

    if not outcome.hits and not outcome.business_data:
        # 两侧都没料：明说信息不足，不调模型（没有输入的"分析"必然是编的）
        outcome.analysis = "知识库里没有检索到相关资料，业务数据也取不到，无法给出分析。"
        return outcome

    knowledge_block = "\n".join(
        f"[资料{i + 1}｜{h.document_title}｜{h.evidence_ref}] {h.content}"
        for i, h in enumerate(outcome.hits)
    ) or "（无命中资料）"
    business_block = (
        "\n".join(f"- {k}: {v}" for k, v in outcome.business_data.items())
        or "（无可用的业务数据）"
    )

    prompt = (
        f"用户问题：{question}\n\n"
        f"=== A. 参考资料（背景知识，**不是本公司业务事实**）===\n{knowledge_block}\n\n"
        f"=== B. 真实业务数据（系统查库得到，可作事实陈述）===\n{business_block}"
    )

    try:
        raw = generate_reply(prompt, system_prompt=ANALYZE_SYSTEM_PROMPT)
        outcome.llm_called = True
    except LLMUnavailable as exc:
        outcome.llm_called = True
        outcome.llm_error = exc.reason
        outcome.analysis = (
            "AI 暂时不可用，无法生成分析。以下是检索到的原始资料与业务数据，供你自行判断。"
        )
        return outcome

    try:
        import json as _json

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        data = _json.loads(cleaned[start : end + 1]) if start != -1 else {}
    except Exception:  # noqa: BLE001 - 模型没给合法 JSON 时，把原文作为分析返回
        outcome.analysis = raw.strip()
        return outcome

    outcome.analysis = str(data.get("analysis") or "").strip()
    outcome.from_knowledge = [str(x) for x in (data.get("from_knowledge") or [])]
    outcome.from_business_data = [str(x) for x in (data.get("from_business_data") or [])]
    outcome.gaps = [str(x) for x in (data.get("gaps") or [])]
    return outcome


__all__ = [
    "ANALYZE_SYSTEM_PROMPT",
    "CHUNK_MAX_CHARS",
    "CHUNK_MIN_CHARS",
    "DEFAULT_TOP_K",
    "AnalyzeOutcome",
    "KnowledgeError",
    "SearchHit",
    "analyze",
    "collect_business_data",
    "extract_terms",
    "get_document",
    "import_document",
    "list_chunks",
    "list_documents",
    "search",
    "split_into_chunks",
]
