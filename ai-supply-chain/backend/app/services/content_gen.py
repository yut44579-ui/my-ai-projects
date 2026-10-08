"""内容 AI 生成服务（TASK-038）。

═══════════════════════════════════════════════════════════════════════
【用户需求】
═══════════════════════════════════════════════════════════════════════
    「写内容的可以加一个 AI 自动化生成」
    「这里的内容可以参考知识库里面的内容，也可以是市面上通用场景适用的提示词
      来生成适配的场景内容」
    「生成的字数是有限制的，这里是指**总范围**的，并不是 AI 生成的限制」

    → 所以本模块做两件事：
      ① **可选接知识库**：命中资料时作为事实依据进入提示词；
         没命中时**不硬编**，改用通用营销文案框架生成（并如实告知"未引用知识库"）
      ② **字数是一个目标区间**，不是硬上限：
         用户选 300/600/1000 字，就按这个篇幅写；
         字段本身用 Text 存储，不受这个数字约束。

═══════════════════════════════════════════════════════════════════════
【不编造：有资料就只依据资料，没资料就不假装有】
═══════════════════════════════════════════════════════════════════════
    ★ 提示词里明确写：**不许编造产品参数、案例、客户名、数据**。
      知识库没命中时，只允许写"通用方法论/行业通识"层面的话，
      且**不得出现具体数字与客户名**。
    ★ 生成物落库时标记 `body_ai_generated=True` 并记下引用的资料锚点 ——
      让人知道这段是 AI 写的、依据是什么，需要人工复核（§二十六）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.content import ContentType, MarketingContent
from app.models.knowledge import KnowledgeDocType
from app.services.knowledge import search
from app.services.llm import LLMUnavailable, generate_reply

#: 可选篇幅（字数目标区间）。
#: ★ 键是"目标字数"，值是给模型的区间描述。用户看到的就是这几个选项，
#:   所以不做任意数字输入 —— 避免"写 473 字"这种模型无法稳定满足的要求。
LENGTH_PRESETS: dict[str, dict] = {
    "SHORT": {"label": "短（约 300 字）", "target": 300, "range": "250~350 字"},
    "MEDIUM": {"label": "中（约 600 字）", "target": 600, "range": "500~700 字"},
    "LONG": {"label": "长（约 1000 字）", "target": 1000, "range": "900~1200 字"},
}

#: 内容类型 → 写作体裁说明（让模型知道"文章"和"白皮书"该怎么写）
TYPE_GUIDE: dict[ContentType, str] = {
    ContentType.ARTICLE: "一篇对外发布的文章：有观点、有论据、段落分明，可直接发官网或公众号。",
    ContentType.CASE_STUDY: "一篇案例叙述：讲清背景、做法、结果。★ 若没有真实客户与数据，"
    "就写成「方法示范」并明确说明不涉及具体客户，不许编客户名与数字。",
    ContentType.WHITEPAPER: "一份偏专业的白皮书：有背景、有方法框架、有结论，语言正式。",
    ContentType.VIDEO: "一个短视频脚本：分镜/口播稿形式，句子短、口语化。",
    ContentType.EVENT: "一场活动的宣传文案：讲清是谁办、给谁看、能获得什么。",
    ContentType.LANDING_PAGE: "一个落地页文案：主标题 + 卖点小节 + 行动号召。",
    ContentType.OTHER: "一段通用的业务内容。",
}

#: 渠道 → 语气与格式要求
CHANNEL_GUIDE: dict[str, str] = {
    "WEBSITE": "官网：正式、完整、适合长期留存。",
    "WECHAT": "微信公众号：口语一些，段落短，适合手机阅读。",
    "LINKEDIN": "职场/行业平台：专业、克制，少用感叹号。",
    "EMAIL": "邮件：开头一句说明来意，正文分点，结尾一个明确动作。",
    "OFFLINE": "线下物料：短句、大字感，信息密度低。",
    "OTHER": "通用渠道。",
}

SYSTEM_PROMPT = """你是企业市场内容的写作助手。你要按给定的**篇幅、体裁、渠道**写一篇完整内容。

【铁律】
1. **不许编造**：
   · 不许编产品参数、技术指标、价格、客户名、合作方、案例数字。
   · 不许写"我们已服务 500+ 企业"这类无法核实的话。
   · 【参考资料】里有的信息可以用；没有的**不许补**。
2. **参考资料为空时**：只写行业通识与方法论层面的话，**不出现任何具体数字与客户名**，
   也不要假装我们做过某个项目。
3. 不许给承诺：不写保证效果、保证收益、保证周期这类话。
4. 语言：面向业务人员的中文，具体、可读，**不要空话套话**
   （避免"赋能""闭环""抓手""生态"这类词堆砌）。
5. 篇幅必须落在要求区间内；宁可信息密度高一点，也不要为凑字数注水。

【输出格式】只输出 JSON，不要解释文字：
{
  "title": "标题（若用户已给标题就沿用/微调）",
  "summary": "一两句话的摘要，不超过 80 字",
  "body": "正文。用 \\n\\n 分段；小标题单独一行",
  "used_reference": true 或 false（是否真的用到了参考资料）
}"""


class ContentGenError(Exception):
    """生成相关业务错误（路由层翻译成 400）。"""


@dataclass
class GenerateOutcome:
    title: str = ""
    summary: str = ""
    body: str = ""
    hits: list = field(default_factory=list)
    llm_called: bool = False
    llm_error: str | None = None

    @property
    def char_count(self) -> int:
        """正文字数（不含空白）。

        ★ 用"非空白字符数"而不是 len(body)：中文文章里换行与空格很多，
          直接 len 会让人以为字数虚高，与实际阅读量对不上。
        """
        return len(re.sub(r"\s", "", self.body or ""))


def generate_content(
    db: Session,
    *,
    topic: str,
    content_type: ContentType,
    channel: str,
    target_length: str = "MEDIUM",
    doc_type: KnowledgeDocType | None = None,
    top_k: int = 5,
    use_knowledge: bool = True,
) -> GenerateOutcome:
    """生成一篇内容。

    ★ `use_knowledge=False` 时**不检索知识库**，纯按通用场景写 ——
      这就是用户说的"也可以是市面上通用场景适用的提示词"。
      两种情况都会在提示词里如实说明有无参考资料，避免模型把通识当自家事实。
    """
    preset = LENGTH_PRESETS.get(target_length) or LENGTH_PRESETS["MEDIUM"]
    outcome = GenerateOutcome()

    if use_knowledge:
        outcome.hits = search(db, topic, doc_type=doc_type, top_k=top_k)

    if outcome.hits:
        reference_block = "\n".join(
            f"[资料{i + 1}｜{h.document_title}] {h.content}" for i, h in enumerate(outcome.hits)
        )
    else:
        reference_block = (
            "（无参考资料）"
            if use_knowledge
            else "（本次按通用场景写作，未使用知识库）"
        )

    prompt = (
        f"写作主题：{topic}\n"
        f"篇幅要求：{preset['range']}（这是一个目标区间，不要明显超出）\n"
        f"体裁：{TYPE_GUIDE.get(content_type, TYPE_GUIDE[ContentType.OTHER])}\n"
        f"渠道语气：{CHANNEL_GUIDE.get(channel, CHANNEL_GUIDE['OTHER'])}\n\n"
        f"=== 参考资料（可用于内容的事实依据；为空时按铁律第 2 条处理）===\n{reference_block}"
    )

    try:
        raw = generate_reply(prompt, system_prompt=SYSTEM_PROMPT)
        outcome.llm_called = True
    except LLMUnavailable as exc:
        outcome.llm_called = True
        outcome.llm_error = exc.reason
        return outcome

    data = _extract_json(raw)
    outcome.title = str(data.get("title") or topic).strip()
    outcome.summary = str(data.get("summary") or "").strip()
    outcome.body = str(data.get("body") or "").strip()

    if not outcome.body:
        raise ContentGenError("模型没有产出正文，请换个主题或重试")
    return outcome


def _extract_json(text: str) -> dict:
    """从模型输出取 JSON（容错 ```json 包裹与前后杂话）。"""
    import json

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ContentGenError(f"模型输出不是合法 JSON：{exc}") from exc
    raise ContentGenError("模型输出里找不到 JSON")


def apply_generated(
    db: Session,
    row: MarketingContent,
    outcome: GenerateOutcome,
) -> MarketingContent:
    """把生成结果写进内容记录。

    ★ 落库时标记 `body_ai_generated=True` 并记下引用的资料锚点 ——
      让人知道这段是 AI 写的、依据是什么（需人工复核）。
    """
    row.body = outcome.body
    row.body_ai_generated = True
    row.body_sources_json = [
        {
            "evidence_ref": h.evidence_ref,
            "document_title": h.document_title,
            "seq": h.seq,
        }
        for h in outcome.hits
    ]
    if outcome.summary:
        row.summary = outcome.summary
    if outcome.title:
        row.title = outcome.title[:300]
    db.commit()
    db.refresh(row)
    return row


__all__ = [
    "CHANNEL_GUIDE",
    "LENGTH_PRESETS",
    "SYSTEM_PROMPT",
    "TYPE_GUIDE",
    "ContentGenError",
    "GenerateOutcome",
    "apply_generated",
    "generate_content",
]
