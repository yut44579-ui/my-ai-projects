"""LLM 调用层：查询改写 + 回答生成 + 降级链。

★★ 这个文件里最重要的是**提示词**，而提示词里最重要的是**黑名单**。

    "用大白话回答"这种正面指令几乎没用（模型本来就觉得自己的话很白）。
    真正有效的是明确列出**禁止出现的句式** —— 这些是从真实评审里攒出来的：

      · 开场白「您好！根据…」       → 客服腔
      · 念条款原文不改成人话         → 机器味
      · 「我叫个同事帮你看下」       → ★ AI 在解释自己（评审原话：
                                        "你人工就直接转就得了，还同事"）
      · 「财务部」                  → ★ 泄漏内部结构（评审原话：
                                        "干嘛还要暴露职业名称跟岗位"）
      · 「感谢您的咨询」             → 填充句
      · markdown 星号井号            → 企微/飞书是纯文本，会原样显示

★★ 第二重要的是**事实边界**：
    模型只能转述检索到的内容，不能自己补充。
    所以提示词里要把资料明确框起来，并说清"资料里没有的就不许说"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import config, store

try:
    from openai import OpenAI
except Exception:  # noqa: BLE001 —— 没装 SDK 时也要能跑（走原文直出）
    OpenAI = None  # type: ignore[assignment]

_client: Any = None


def _cfg() -> dict[str, str]:
    """取当前模型配置。★ 优先用界面上配的（落库的），其次环境变量。

    这样"在控制台改模型"才真的生效，而不是要重启服务。
    """
    from . import settings as _settings

    try:
        c = _settings.get_model_config(mask=False)
        return {
            "base_url": c.get("base_url") or config.LLM_BASE_URL,
            "api_key": c.get("api_key") or config.LLM_API_KEY,
            "model": c.get("model") or config.LLM_MODEL,
            "model_cheap": c.get("model_cheap") or config.LLM_MODEL_CHEAP,
        }
    except Exception:  # noqa: BLE001 —— 设置表还没建好时退回环境变量
        return {
            "base_url": config.LLM_BASE_URL,
            "api_key": config.LLM_API_KEY,
            "model": config.LLM_MODEL,
            "model_cheap": config.LLM_MODEL_CHEAP,
        }


def reset_client() -> None:
    """配置改了之后要清掉缓存的 client，否则还是用旧 key。"""
    global _client
    _client = None


def client() -> Any:
    global _client
    c = _cfg()
    if _client is None and OpenAI is not None and c["api_key"]:
        _client = OpenAI(
            api_key=c["api_key"],
            base_url=c["base_url"],
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    return _client


def available() -> bool:
    return client() is not None


# ══════════════════════════════════════════════════════════════════════
# 提示词
# ══════════════════════════════════════════════════════════════════════

# ★ 禁止清单。分成两组，因为处理方式不同：
#   · 话术类 → 出站检查删掉就行
#   · 身份类 → 一旦出现说明整段都在讲"谁在处理"，要转人工
BANNED_STYLE = """- 不许有开场白（"您好""根据您的问题""我帮您查"一律不要）
- 不许有结束语（"感谢您的咨询""如有其他问题""希望有帮助"）
- 不许说"根据《XX》第X条规定"这种念条款的句式，要翻译成人话
- 不许复述用户的问题
- 不许用 markdown（不要 **加粗**、不要 # 标题、不要短横线列表）
- 不许说"我"做了什么（"我查了一下""我叫了同事""我已经为您转接""我给你走流程"）
- 不许用"亲爱的用户""亲"这类称呼"""

BANNED_IDENTITY = """- 不许提内部部门（财务部/法务部/技术部/售后部…），只说"帮你处理"
- 不许提内部职位（主管/经理/专员）
- 不许提同事姓名、工号
- 不许说问题转给了谁"""

SYSTEM_PROMPT = f"""你是一家公司的客服，在一个聊天窗口里回答员工或客户的问题。

【最重要的一条：只能转述资料里的事实】
下面会给你【资料】。你的每一句事实都要能在资料里找到依据。
资料里没有的，一个字都不许自己补。
★ 如果资料不足以回答，直接输出：〔无法回答〕
（不要道歉，不要解释，不要猜，就这四个字）

【语气】
像同事在微信上回你消息那样说。短。
一句话能说清的，就一句话，不要分点。
要分步的（比如"怎么填"），最多三步。
用"你"，不用"您"。

【禁止出现的句式】
{BANNED_STYLE}

【关于内部信息】
{BANNED_IDENTITY}

【出处 —— ★ 绝对不要写】
★★ 铁律：**回答里不许出现任何文件名、资料名、条款号、来源标注。**
   括号里的〔出处：《xxx》〕这东西**一句都不许有**。

★ 为什么（这是用户明确提的要求）：
  · 那些文件名是公司内部的，写出来**等于把内部资料结构告诉客户**
    （光看名字就能猜出你有哪些内部制度，比如《报价底线》）
  · 客户看到出处会以为我们在念文档，反而显得不专业
  · 客户要的是答案，不是我们的资料清单

★ 溯源是我们**内部**的事（控制台和人工工作台里会单独显示），
  不需要写进给客户看的话里。

【示例】
资料：第3.2条 报销单经审批通过后，财务部应于5个工作日内完成付款。
用户：报销多久到账
你：审批过了 5 个工作日到账。

资料：第1.1条 工作时间：上午九点至下午六点，中午休息一小时。
用户：上班时间是几点来着
你：九点到六点，中午休一个小时。
"""


@dataclass
class GenResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    degraded: bool = False
    notes: list[str] = field(default_factory=list)


def _call(model: str, system: str, user: str, *, temperature: float = 0.3) -> GenResult:
    c = client()
    if c is None:
        return GenResult(text="", model="none", degraded=True, notes=["没有可用的模型"])
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    usage = getattr(resp, "usage", None)
    return GenResult(
        text=(resp.choices[0].message.content or "").strip(),
        model=model,
        tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
        tokens_out=getattr(usage, "completion_tokens", 0) or 0,
    )


def rewrite_query(question: str, context: list[str] | None = None) -> GenResult:
    """口语 → 检索词。

    ★ 这一步看起来很土（就是"翻译一下"），但它决定了后面所有环节的上限：
      不先改写，"我那个啥时候能好"这种话直接拿去检索，什么都查不到。
      而且它是**只做翻译、不产生事实**，所以可以用便宜模型，很划算。
    """
    ctx = ""
    if context:
        ctx = "\n最近几轮对话：\n" + "\n".join(f"- {c}" for c in context[-4:])
    system = (
        "把用户这句话改写成适合在企业内部资料库里搜索的关键词。\n"
        "只输出关键词，用空格分开，不要解释，不要回答用户。\n"
        "如果有代词（那个、这个、它），结合上下文替换成具体事物。\n"
        "关键词里要同时包含口语说法和可能的书面说法。"
    )
    user = f"用户说的：{question}{ctx}"
    try:
        return _call(_cfg()["model_cheap"], system, user, temperature=0.1)
    except Exception as exc:  # noqa: BLE001
        return GenResult(text=question, model="fallback", degraded=True, notes=[str(exc)[:120]])


def generate_answer(
    question: str,
    context_text: str,
    citations: list[str],
    *,
    style_examples: list[dict[str, Any]] | None = None,
) -> GenResult:
    """生成回答。带**降级链**。

    降级顺序（★ 刻意不把"免费模型"放在默认位置，见 docs/决策记录.md D9）：
        主模型 → 同厂商便宜档 → 返回空（由上层走"原文直出"）
    """
    style_block = ""
    if style_examples:
        lines = []
        for ex in style_examples[:5]:
            lines.append(f"用户：{ex['question']}\n同事当时是这么回他的：{ex['answer_after']}")
        style_block = (
            "\n\n【这家公司的人平时是这么说话的，照着这个风格】\n" + "\n\n".join(lines)
        )

    user = f"【资料】\n{context_text}\n\n【用户的问题】\n{question}{style_block}"
    system = SYSTEM_PROMPT

    c = _cfg()
    attempts = [c["model"], c["model_cheap"]]
    seen: set[str] = set()
    notes: list[str] = []
    for i, model in enumerate(attempts):
        if model in seen:
            continue
        seen.add(model)
        try:
            r = _call(model, system, user)
            if r.text:
                if i > 0:
                    r.degraded = True
                    r.notes.append(f"主模型不可用，已降级到 {model}")
                return r
            notes.append(f"{model} 返回空")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{model} 失败：{str(exc)[:100]}")

    # ★ 第三档：不调模型。由上层直接把检索原文给用户。
    return GenResult(text="", model="none", degraded=True, notes=notes)


def raw_fallback(hits: list[Any]) -> str:
    """★ 第三档降级：不调模型，直接把检索到的原文给用户。

    为什么这一档很重要（见 D9）：
      · 零成本
      · 零编造风险（原样返回，不经模型改写）
      · 用户至少拿到了信息，而不是"系统忙"

    代价是"不像人话"，所以要**明确告诉用户这是什么**，
    不能假装是回答 —— 假装的话用户会以为 AI 就这水平。
    """
    if not hits:
        return "这个我查不到，我让人来回你。"
    # ★★ 降级也不能暴露内部资料名。
    #    用户的明确要求：「不要标明出处，来源泄露信息」。
    #    第一版这里写了《文件名》 —— 等于降级的时候把内部文档结构全抖出去了。
    #    ★ 而原文直出该标明的是**这是什么**（是资料原文，不是回答），
    #      不是这是哪份资料。
    parts = ["模型暂时用不了，我把查到的原文直接给你："]
    for h in hits[:3]:
        text = getattr(h, "text", "") or ""
        parts.append(f"\n{text[:300]}")
    return "\n".join(parts)


def is_unanswerable(text: str) -> bool:
    """模型按约定输出了〔无法回答〕。

    ★ 用约定的**固定标记**判断，而不是去猜模型的话什么意思：
      猜的话各种措辞都会出现（"抱歉我不知道""这个问题我无法回答"…），
      漏掉一个就会把"答不了"当成"答了"，然后自动发出去。
    """
    t = (text or "").strip()
    return t.startswith("〔无法回答〕") or t.startswith("[无法回答]") or "无法回答" in t[:12]


def self_intro(question: str) -> GenResult:
    """回答"你是谁 / 你能做什么"这类**元问题**。

    ★★ 这个函数存在的意义，是修一个真实的失败：
      用户问「你是做什么的」，系统答不上来 ——
      因为这个问题的词（你 / 做 / 什么）和资料内容
      （公司简介写的是"恒信精工成立于 2006 年…"）**一个字都不重合**，
      中文 2-gram 检索必然命中不了。

    ★ 所以这类问题**根本不该走检索**。
      它们问的不是"公司的业务事实"，而是"你是什么" ——
      答案是我们自己定义的，不需要查任何资料。

    ★★ 但必须上一道硬锁：
      只允许它介绍自己，**不许借机回答任何业务事实**。
      否则就成了"模型想说什么就说什么"，违反第一条纪律
      （事实由程序算，不由模型编）。
    """
    from .tiering import SIMPLE_SYSTEM

    c = _cfg()
    if not c["api_key"] or client() is None:
        return GenResult(text="", model="", degraded=True, notes=["没有配置模型"])
    try:
        return _call(c["model_cheap"] or c["model"], SIMPLE_SYSTEM, question,
                     temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        return GenResult(text="", model="", degraded=True, notes=[f"自我介绍生成失败：{exc}"])


# ★ 「反复确认」的话术尾巴。
#   中级问题的处理方式是"先答一次，但把不确定说出来，请用户确认" ——
#   这不是客套，有实际作用：
#     · 用户确认了      → 答对了，不用转人工
#     · 用户说"不是这个" → 我们**立刻知道第一次理解错了**，
#       比让他换个说法再问一遍（然后我们才发现）要早一整轮
CONFIRM_SUFFIX = "（我理解你是想问这个。如果不是你的意思，你再说一句，我给你转人工。）"


def confirm_suffix() -> str:
    return CONFIRM_SUFFIX


# ★★ 「软风险」的处理提示词。
#
#    用户的批评是：「不是我随便发一句话就直接接入人工，
#    这样不是会加重客服或者销售的压力？」
#
#    所以"能报个价吗"这类**不该直接甩给销售** —— 销售拿到也没用，
#    他还是得回头问"什么零件、什么材料、多少件"。
#    让 AI 先答流程、把该问的问全，人工接手时才是**一条完整的需求**。
#
#    ★ 但硬边界一点都不能松：**不许出现任何具体数字或承诺**。
PROCESS_SYSTEM = """你在给一个企业做客服。用户问的是**办事流程**，不是具体条件。

★★ 铁律（违反即事故）：
1. **绝对不许**说出任何具体数字：价格、折扣、比例、天数、金额、期限。一个都不能有。
2. **绝对不许**做出任何承诺："可以""没问题""一定""保证""肯定行"。
3. **绝对不许**替公司下结论（"我们能满足""这个能批"）。

★ 你只做两件事：
  一、把**流程**说清楚（要走哪几步、需要准备什么）
  二、把**该问的信息问全**（比如报价要问：零件类型、材料、精度要求、数量）

格式：2 到 4 句，口语，不要分点，不要 markdown。结尾自然地把需要的信息问出来。"""


def answer_process(question: str, context: str = "") -> GenResult:
    """回答"流程类"问题（软风险）。

    ★ 和普通回答的区别：普通回答可以引用资料里的具体内容，
      这个**只允许说流程** —— 即使资料里写了"报价 3 天内给"，
      也不许原样说出来，因为这类口径涉及对客户的承诺。
    """
    c = _cfg()
    if not c["api_key"] or client() is None:
        return GenResult(text="", model="", degraded=True, notes=["没有配置模型"])
    user = question
    if context:
        user = (f"【参考资料（**只能用来判断流程，里面的数字一律不要引用**）】\n{context}\n\n"
                f"【用户问】{question}")
    try:
        return _call(c["model_cheap"] or c["model"], PROCESS_SYSTEM, user, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        return GenResult(text="", model="", degraded=True, notes=[f"流程回答生成失败：{exc}"])


# ★ 模型不可用时的流程类固定话术。
#   ★ 按类别分开写死 —— 让程序现编一句就是"事实由模型编"，违反第一条纪律。
PROCESS_FALLBACK = {
    "报价流程": "报价要工程师看过图纸才能定。你把零件类型、材料、精度要求和预计数量告诉我，"
                "我整理好转给工程师。",
    "合同流程": "合同要先确认合作内容和条款，再走内部审批。你先把需要的服务范围告诉我，"
                "我让人跟你对接具体条款。",
    "退换流程": "退换要先确认是什么问题、什么时候收到的货。你把订单信息说一下，"
                "我让人查了之后再跟你确认怎么处理。",
}


def process_fallback(kind: str = "") -> str:
    return PROCESS_FALLBACK.get(kind) or (
        "这个我需要先了解你的具体情况才能答。你先说一下想办什么事，我帮你对接。"
    )


def strip_marker(text: str) -> str:
    t = (text or "").strip()
    for m in ("〔无法回答〕", "[无法回答]"):
        if t.startswith(m):
            return t[len(m) :].strip()
    return t
