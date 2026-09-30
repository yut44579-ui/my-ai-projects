"""doc_qa.py · 外部资料的问答（FR-010-B）—— **只回答资料里真有的内容，并且给出处**。

════════════════════════════════════════════════════════════════════════
【这个能力域干什么】
════════════════════════════════════════════════════════════════════════
用户问"你导入的资料里说的这件事是什么" → 去资料里**检索**（`app/ai/doc_index.py`）
→ 用**检索到的原文**回答 → 附上**文件名 + 位置**。

四种出口，每一种都对应一条诚实底线：

    ① 命中         引用原文（不改写、不概括成结论）+ 出处
    ② 概览         每节各挑一句原文 + 每节各自带出处
    ③ 没找到       明确说"我在当前已导入的资料里没有找到关于 X 的内容" + 说明资料里实际有什么
    ④ 读不出文字   明说这份资料没有可提取的文本层、本系统不做图片识别（→ B6）

════════════════════════════════════════════════════════════════════════
【★ 为什么回答由代码写，而不是让模型组织语言】
════════════════════════════════════════════════════════════════════════
派单 §二 B1 的硬要求是"答案里的每个事实点都能在原文找到对应片段，不许把常识/模型知识混进去"。
**只有"原句摘录"能结构性保证这一条**：模型复述一遍，哪怕意思不错，也已经不是原文了；
要证明它没混进别的东西，就得再写一套句子级溯源核对 —— 那正是本项目在数字上做过的事
（`number_guard`），而文本的"说法对不对"比数字难核得多。

于是本模块做了一个更简单也更强的选择：**资料正文从不进入任何提示词**。

    资料正文 → 只在这里被切、被打分、被**引用**，没有任何一条路径把它送进模型；
    模型要改口径，得先有一句提示词装着资料内容 —— 而那一步不存在。

这同时把另外两条要求变成了结构性事实（不是靠提示词里写一句"请不要…"）：
    · B3 资料不是指令：注入写在正文里也没用，因为正文根本没有机会变成指令；
    · B5 保持原属性：预计/计划/观点**原样引用**，不经过任何改写，属性自然保住。

★ 代价如实写在这里：没有模型参与，回答读起来是"资料原文 + 出处"的清单体，
不像人写的摘要；而且用户问得越口语化，越容易落进"没找到"（检索是按字面匹配的）。
这两条是刻意的取舍 —— 宁可回答朴素，也不给出无法回溯的内容。

════════════════════════════════════════════════════════════════════════
【不做什么（都是别的 TASK 的边界，不许顺手做）】
════════════════════════════════════════════════════════════════════════
    · 不做资料 + 销售数据的**联合分析**（那是 FR-010-C，在 `app/ai/joint.py`）——
      但联合分析的**资料那一半**复用本模块的既有能力（`doc_index.search` 检索 +
      `best_sentences` 抽原句 + `citation_of` 出处 + `attribute_of` 属性词 +
      `overview_answer` 结构概览），**没有第二套出处机制**；
      ★ 判据也是同一处：`points_at_document` / `has_content_ask` 由两边共用，
        所以"问资料里写了什么"永远归本模块，"要把资料与数据放一起看"才归联合分析；
    · 不读销售数据（本模块只 import 文档仓储与导入记录，见 `tests/test_fr010b_documents.py`
      的结构性隔离断言）；
    · 不因文件名猜正文（B4）：文件名只用来**定位**文件，一个字都不进回答的断言部分；
    · 不做 OCR、不假装读过图（B6）；
    · 不改任何系统规则、不碰权限 / 工具白名单 / 黑名单 / 数字闸门（B3）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.ai import doc_index
from app.importer import db, store

# ════════════════════════════════════════════════════════════════════════
# ① 四种出口的种类（调用方按它决定状态与提示语）
# ════════════════════════════════════════════════════════════════════════
KIND_ANSWER = "answer"            # 命中了（含概览）→ 有内容、有出处
KIND_NOT_FOUND = "not_found"      # 检索不到 → 明说没有找到（**不许补内容**）
KIND_NO_TEXT = "no_text"          # 这份资料没有可提取的文字（扫描件）→ 明说读不了（B6）
KIND_EMPTY = "empty"              # 库里还没有任何资料 → 明说没有可检索的内容

#: 回答里**不许出现**的内部名词（派单 §二 B7）。这些词连注释以外的任何文案都不该有 ——
#: 有测试直接扫本模块产生的每一段文字。
BANNED_WORDS: tuple[str, ...] = (
    "BM25", "bm25", "分块", "chunk", "检索得分", "向量", "embedding", "intent",
    "Intent", "document_qa", "tool", "工具", "接口", "路径", "/api/", "耗时",
    "置信度", "response_mode", "score", "top", "TOP",
)

# ════════════════════════════════════════════════════════════════════════
# ② 判据：这句话是在问"资料里写了什么"吗（**判得出来才可能答得上来**）
# ════════════════════════════════════════════════════════════════════════
#: 指向"一份资料"的名词。**刻意不收「文件」两个字**：它在 A3 里已经被"我导入的文件在哪"
#: 那条元信息用着，而且"文件"单独出现时常常指的是数据表，不是资料。
_DOC_NOUNS: tuple[str, ...] = ("资料", "文档", "报告", "手册", "白皮书", "说明书", "材料")

#: 书名号 / 带后缀的文件名 —— 用户点名了一份具体文件（B4：文件名只用来定位）
_BOOK_RE = re.compile(r"《([^》]{1,80})》")
_FILE_NAME_RE = re.compile(r"[\w一-鿿][\w一-鿿 .\-]*\.(?:pdf|docx|doc|md|markdown|pptx|ppt|txt)\b",
                           re.IGNORECASE)

#: 在问**内容**的信号。没有它就不接 —— "怎么导入资料""资料在哪"那两类归使用说明 / 元信息。
_CONTENT_ASKS: tuple[str, ...] = (
    "说了什么", "说了啥", "说的是什么", "讲了什么", "讲了啥", "讲的是什么", "说什么", "讲什么",
    "写了什么", "写了啥", "写的是什么", "有什么内容", "是什么内容", "主要内容", "大意", "概述",
    "摘要", "介绍了什么", "介绍了什么内容", "提到", "提到过", "有没有写", "有没有提到",
    "有没有说", "有没有讲", "有没有", "写了没", "说了吗", "怎么说的", "说了哪些", "讲了哪些",
    "有哪些内容", "说了什么内容", "是什么", "多少", "哪些",
)
#: 概览类（"这份资料讲了什么"）—— 它要的是**分节列表**，不是某一段的检索
_OVERVIEW_ASKS: tuple[str, ...] = (
    "讲了什么", "讲了啥", "说什么", "讲了哪些", "说了哪些", "主要内容", "大意", "概述",
    "介绍了什么", "是什么内容", "有什么内容", "写了什么", "说了什么", "讲的什么", "讲的啥",
)
#: 问"图里的东西"（B6：明说读不了，不许编）
_IMAGE_ASKS: tuple[str, ...] = (
    "图片", "图里", "图中", "截图", "插图", "图表", "图片里", "扫描件", "扫描版", "照片",
)


def has_content_ask(text: str) -> bool:
    """"问的是内容"的信号（判据②，单独拿出来是因为概览与检索都要用它）。"""
    return any(word in text for word in _CONTENT_ASKS)


def points_at_document(text: str) -> bool:
    """这句话**指向一份资料**吗（判据①，单独拿出来是因为联合分析也要用它）。

    指向方式有两种：提到「资料 / 文档 / 报告…」这类名词，或者用书名号 / 带后缀的文件名点名。
    ★ 这是"这份资料"的**唯一一处判据**：FR-010-C 的联合分析也走它 ——
      词表在这里抄第二份，两处早晚会漂移成"判得出来却答不上来"。
    """
    question = (text or "").strip()
    if not question:
        return False
    return bool(_BOOK_RE.search(question) or _FILE_NAME_RE.search(question)) \
        or any(noun in question for noun in _DOC_NOUNS)


def is_document_question(text: str) -> bool:
    """这句话是在问**已导入资料的内容**吗（纯代码，不读数据）。

    两件事同时成立才算：
      ① 指向一份资料 —— 要么提到「资料 / 文档 / 报告…」，要么用书名号或带后缀的文件名点名；
      ② 在问内容 —— 有 `_CONTENT_ASKS` 里的说法。

    为什么必须两道：光有①会把"怎么导入资料"这种**使用说明**抢过来（那一类有静态文案），
    光有②会把"今天天气怎么样"这种没有任何资料指向的问题拖进来。
    """
    question = (text or "").strip()
    if not question:
        return False
    if not points_at_document(question):
        return False
    # ★ FR-012 组 2：**整库清单**的问法（"我有哪些资料 / 资料里写了哪些内容"）不由本模块回答 ——
    #   它问的是"我的资料有哪些"（元信息：真实文件名与条数），由 `system_info` 查 FR-009-A 的
    #   统一文档仓储列出。为什么必须在这里让路：回答侧 `doc_qa.answer_question` 排在元信息**之前**，
    #   不让路就会拿"检索某一段"去回答"我有哪些资料"——实测回的是"没有找到关于「我有」的内容"，
    #   而紧接着屏幕上又列着资料（同一段话自相矛盾）。
    from app.ai import system_info                       # 局部 import：与 `_empty_answer` 同一做法
    if system_info.is_catalog_question(question):
        return False
    return has_content_ask(question)


def is_document_fallback(text: str) -> bool:
    """★ FR-010-D 兜底判据：**点了名一份资料**，只是没用"问内容"那套说法（纯代码，不读数据）。

    为什么要有这一条（Hermes 复验 B 时抓到的漏网，真实问法）：
        「供应商与物流.md 里说成本会怎样？」「资料里说明年物流成本会怎样？」
        这些句子**确实在问那份资料**，但措辞（"会怎样 / 如何 / 怎么变"）不在 `_CONTENT_ASKS` 里，
        于是判据②把它们挡在外面 → 既不是资料问答，也没有任何别的档接得住 → 最后落进
        「没听懂这个问题」的澄清档。**用户问资料，得到的却是"我没听懂"。**

    为什么不放宽 `_CONTENT_ASKS` 让它们直接走 ①∧②：
        那张表同时挡着「怎么导入资料」「我导入的文件在哪」这类**使用说明**（它们也点着"资料"）。
        放宽就是把使用说明抢答成资料内容 —— 那正是当初加判据②的原因，不能拆。
        所以这里的选择是"**允许进、但答不出要如实说没找到**"：进得来 ≠ 答得上，
        检索仍然照实说"没有找到关于 X 的内容"（`NOT_FOUND_TEXT`），不比以前多说一个字。

    与 `is_document_question` 的关系：那道是 ①∧②，这道是 ①∧¬② —— 两道合起来，
    "指向一份资料"的问法就全都有着落了（②那种走既有入口，这一种走兜底）。

    ★ 本判据**只做字符串判断**：库里有不有资料是另一件事（`has_documents()`），
      由调用方（路由）把两件事合起来判断 —— 判据的"纯"与"要不要读一次语料"分得开，
      回答那一侧才能在不读语料的前提下复用同一个判据。
    """
    question = (text or "").strip()
    if not question:
        return False
    if not points_at_document(question):
        return False
    return not is_document_question(question)          # ①∧¬②：②那种走既有入口，这里只收漏网的


def has_documents() -> bool:
    """库里现在有没有**可检索的资料**（FR-010-D 兜底的另一个条件）。

    为什么库为空时**不**兜底：没有资料可检索，"我在资料里没有找到"这句话无从谈起 ——
    那不是"没找到"，而是"根本没有资料"。这时候既有路径（使用说明 / 澄清 / 元信息）里
    本来就有更准确的说法，兜底只会把一句更差的话盖上去。

    读不出来（本机存储暂时不可用）按"没有"处理，同样保持既有路径的原有说法。
    """
    try:
        return bool(doc_index.load_corpus().documents)
    except Exception:                                     # noqa: BLE001 —— 读不出来就是"这次没有"
        return False


def _is_overview(question: str) -> bool:
    """"这份资料讲了什么" —— 要分节列出来，而不是拿最像的那一段回答。"""
    return any(word in question for word in _OVERVIEW_ASKS)


def _asks_about_image(question: str) -> bool:
    return any(word in question for word in _IMAGE_ASKS)


# ════════════════════════════════════════════════════════════════════════
# ③ 回答的形状（**唯一**的出口形状）
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class DocAnswer:
    """一次资料问答的结论（文字 + 种类）。

    `kind` 由调用方决定状态与提示语，见 §①。
    `findings` 是**机器可读**的命中清单（哪份文件、哪个位置、原文哪几句）——
    落在记录里，事后可以逐条回原文核对；它**不进正文**（正文里的话由 `text` 说）。
    """

    kind: str
    title: str
    text: str
    findings: tuple[dict[str, Any], ...] = ()


# ════════════════════════════════════════════════════════════════════════
# ④ 文案常量（全部是给用户看的话，零内部名词）
# ════════════════════════════════════════════════════════════════════════
TITLE_ANSWER = "根据你导入的资料"
TITLE_NOT_FOUND = "资料里没有找到"
TITLE_NO_TEXT = "这份资料读不出文字"
TITLE_EMPTY = "还没有可检索的资料"

EMPTY_TEXT = (
    "现在还没有导入过任何资料，所以我没有可以按内容回答的东西。\n"
    "在「数据管理」页点上传把资料导进来（{formats} 都能导），之后我就能按内容回答，"
    "并且会标出每一条来自哪份文件、哪一节。"
)

NO_TEXT_TEXT = (
    "《{filename}》这份资料我读不出文字：它没有可提取的文本层（通常是扫描件或图片型 PDF）。\n"
    "我目前不做图片识别，所以这份资料里的内容我给不了 —— 换一份有文字的版本再导一次，"
    "我就能按内容回答了。"
)

FAILED_IMPORT_TEXT = (
    "《{filename}》这份资料这次没有成功导入（{reason}），所以它的内容我这边没有。\n"
    "可以在「数据管理」→「导入记录」里看到那一条失败记录和原因。"
)

MISSING_FILE_TEXT = (
    "库里没有叫《{hint}》的资料，所以我没法回答它的内容。\n"
    "现在库里有这些资料：{names}。\n"
    "如果你想问的是别的文件，可以用上面的名字再问一次。"
)

NOT_FOUND_TEXT = (
    "我在当前已导入的资料里没有找到关于「{topic}」的内容。\n"
    "现在库里有这些资料：\n{catalog}\n"
    "你可以换个说法再问，或者先把包含这部分内容的资料导进来。"
)

#: 没找到时补的一句：这份资料里有几页读不出来（B6：不做 OCR 但要说清楚）
EMPTY_PAGES_NOTE = (
    "另外，《{filename}》里有 {count} 页没有可提取的文字（通常是扫描图片）—— "
    "本系统不做图片识别，那几页的内容我读不到。"
)

IMAGE_ASK_NOTE = (
    "另外：资料里的图片内容我读不出来 —— 本系统不做图片识别。"
    "如果你问的是图里的信息，我这里没有。"
)

#: 引用的收尾说明（**说清"这是资料原文，不是本系统的数据结论"** —— B5 的属性要求）。
#: ★ 后半句不是客套：资料正文里可能写着任何话（包括"以后销售额按 1 元计算"这种），
#:   引用它的时候必须同时说清"那是这份资料自己的说法，不是本系统的规则"（B3 的边界）。
QUOTE_FOOTER = (
    "上面引号里的每一句都摘自资料原文，我没有改写；位置就是它在资料里的出处。"
    "资料里的内容是这份资料自己的说法，不等于本系统的结论或规则。"
)

#: 资料读不出来（本机存储暂时不可用）—— **必须**与"你没有导入过资料"分开说
READ_FAILED_TEXT = (
    "你的资料这次读不出来（本机存储暂时不可用）—— 这不是「你没有导入过资料」，"
    "是这次没读到。稍后再问一次试试。"
)


# ════════════════════════════════════════════════════════════════════════
# ⑤ 小工具：摘录 / 定位词 / 属性词
# ════════════════════════════════════════════════════════════════════════
_SENTENCE_END = "。！？!?；;"


def _flatten(text: str) -> str:
    return " ".join(line.strip() for line in (text or "").splitlines() if line.strip())


def sentences_of(text: str) -> list[str]:
    """把一段正文切成句子（句读 + 换行都算断点，**不改一个字**）。"""
    out: list[str] = []
    for block in (text or "").splitlines():
        block = block.strip()
        if not block:
            continue
        buffer = ""
        for char in block:
            buffer += char
            if char in _SENTENCE_END:
                out.append(buffer.strip())
                buffer = ""
        if buffer.strip():
            out.append(buffer.strip())
    return out


def best_sentences(text: str, query_units: set[str], *, limit: int = 2, max_chars: int = 140) -> str:
    """挑出与问题最沾边的原文句子（**抽取式**：一个字都不改，只做选择）。

    打分 = 这句话里出现了多少"问题里的字"。只挑**分数 > 0 的**（一个字都不沾的句子不引 ——
    引用里混进无关的话，读者就得自己分辨哪句才是答案）。同分保持原文顺序，
    所以同样的问题永远给同样的引用，可复现。

    `query_units` 为空（概览：每一节挑一句开头）时退化成"这一节的第一句"。
    """
    candidates = sentences_of(text) or ([_flatten(text)] if text.strip() else [])
    if not candidates:
        return ""
    scored = [
        (len(doc_index.content_units(sentence) & query_units), index, sentence)
        for index, sentence in enumerate(candidates)
    ]
    relevant = [item for item in scored if item[0] > 0]
    if not relevant:
        picked = [candidates[0]]
    else:
        chosen = sorted(relevant, key=lambda item: (-item[0], item[1]))[:limit]
        picked = [sentence for _score, _index, sentence in sorted(chosen, key=lambda item: item[1])]
    joined = " ".join(picked)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rstrip() + "…"
    return joined


#: 资料里的"计划 / 预测 / 观点"属性词 —— 引用时**提到句首**（B5：保持它原本的属性）
_ATTRIBUTE_WORDS: tuple[str, ...] = (
    "预计", "预期", "预测", "预估", "计划", "规划", "打算", "目标", "希望", "拟", "将会", "预计将",
)


def attribute_of(sentence: str) -> tuple[str, str]:
    """`(怎么引述, 引用正文)` —— 句子以"预计/计划/目标"开头时，把它提到引述里。

    例：原文「预计明年销售增长 50%」→ `("资料中预计", "明年销售增长 50%")`
        （正文里就说成「资料中预计：『明年销售增长 50%』」，而不是"明年一定增长 50%"）
    其它句子 → `("资料中写到", 原句)`。
    """
    text = (sentence or "").strip()
    for word in _ATTRIBUTE_WORDS:
        if text.startswith(word):
            rest = text[len(word):].lstrip("，,：: 、")
            if rest:
                return f"资料中{word}", rest
            break
    return "资料中写到", text


#: "没找到"那句里要把问句里的脚手架剥掉，只留下用户问的那个东西（例：「买量成本」）
_TOPIC_STRIP: tuple[str, ...] = (
    # 第一人称的"我导入的…"：用户最常见的说法形式，它整段都是脚手架
    "我刚刚导入的", "我刚才导入的", "我最新导入的", "我导入的", "我上传的", "我放的",
    "刚刚导入的", "导入的", "上传的",
    "资料里有没有写", "资料里有没有提到", "资料里有没有说", "资料里有没有讲", "资料里有没有",
    "资料中有没有", "资料里提到过", "资料里提到", "资料里写了", "资料里说了", "资料里讲的",
    "资料里讲", "资料里说", "资料里写", "资料中写到", "资料中说到", "资料中说", "资料中写",
    "资料中有", "文档里有没有", "文档里", "文件里有没有", "文件里",
    "报告里有没有", "报告里", "资料中", "资料里", "资料说", "资料写到", "资料写",
    "资料", "文档", "报告",
    "有没有写过", "有没有写", "有没有提到", "有没有说", "有没有讲", "有没有",
    "提没提到", "写了没有", "说了没有", "提到过", "提到", "写过", "写的", "写的什么",
    "都写了什么", "写了什么", "说了什么", "讲了什么", "讲的是什么", "说的什么", "是什么",
    "哪些", "多少", "什么", "内容", "具体", "主要", "的", "了", "吗", "呢", "呀", "啊",
    "？", "?", "。", "，", ",", "：", ":", "《", "》",
)


def topic_of(question: str) -> str:
    """从问句里抠出"用户到底在问什么"（只用于"没有找到关于 X"那句）。

    抠不出来（剥完空了 / 还是太长）就**原样用整句** —— 宁可让那句话啰嗦，也不瞎猜一个词。
    """
    topic = (question or "").strip()
    for _round in range(6):                      # 反复剥，直到剥不动（长词表在前，剥一次就少一截）
        before = topic
        for word in _TOPIC_STRIP:
            topic = topic.replace(word, "")
        topic = topic.strip()
        if topic == before:
            break
    topic = re.sub(r"\s+", " ", topic).strip()
    if not topic or len(topic) > 30:
        return (question or "").strip()
    return topic


# ════════════════════════════════════════════════════════════════════════
# ⑥ 四种出口的组装
# ════════════════════════════════════════════════════════════════════════
#: 概览回答最多列几节 / 最多讲几份资料（问题有多大，回答就做到多大）
MAX_OVERVIEW_SECTIONS = 8
MAX_OVERVIEW_DOCUMENTS = 3
#: "没找到"时最多列几份资料、每份列几个标题
MAX_CATALOG_DOCUMENTS = 5
MAX_CATALOG_TITLES = 8

_UNIT_BY_KIND = {
    doc_index.POSITION_PAGE: "页",
    doc_index.POSITION_SECTION: "节",
    doc_index.POSITION_PARAGRAPH: "段",
}


def unit_of(document: doc_index.Document) -> str:
    kinds = {chunk.position_kind for chunk in document.chunks}
    if len(kinds) == 1:
        return _UNIT_BY_KIND[next(iter(kinds))]
    return "节"                                    # 混着来的（少见）：按"节"说，不编更细的


def citation_of(chunk: doc_index.Chunk) -> str:
    """出处文字：`《文件名》第 N 节 · 节标题`（**全项目唯一的一处拼法**）。"""
    position = chunk.position_label
    if chunk.position_title:
        return f"《{chunk.filename}》{position} · {chunk.position_title}"
    return f"《{chunk.filename}》{position}"


def listed_positions(document: doc_index.Document, limit: int) -> list[tuple[str, str, str, int]]:
    """`[(位置标识, 位置文字, 节标题, 这个位置的编号终点)]`，按正文顺序取前 `limit` 个。

    为什么要带上"终点"：位置文字可能是一段区间（`第 1~11 段`）。只说"列了 8 条"，
    用户没法知道这 8 条盖到第几段 —— 也就没法知道还剩多少（实测：那份 PDF 列 8 条盖到
    第 91 段，"还有多少"必须算 115-91=24，不能算成 115-8=107）。
    """
    out: list[tuple[str, str, str, int]] = []
    for key, label, title in document.positions()[:limit]:
        chunk = document.first_chunk_of(key)
        end = (chunk.position_end or chunk.position_number) if chunk else 0
        out.append((key, label, title, end))
    return out


def _catalog(corpus: doc_index.Corpus, names: int = MAX_CATALOG_DOCUMENTS) -> str:
    """资料清单（"没有找到"时说明资料里实际有什么 —— **只报结构，不报内容猜测**）。"""
    lines: list[str] = []
    for document in corpus.documents[:names]:
        listed = listed_positions(document, MAX_CATALOG_TITLES)
        unit = unit_of(document)
        head = f"· 《{document.filename}》—— 共 {document.position_count} {unit}"
        if not listed:
            lines.append(head)
            continue
        tail = " / ".join(title or label for _key, label, title, _end in listed)
        # 列到一半就断掉、又不说还剩下什么，用户会以为这份资料只有这些位置
        hidden = document.position_count - listed[-1][3]
        if hidden > 0:
            tail += f" / …（还有 {hidden} {unit}没有列出来）"
        lines.append(f"{head}：{tail}")
    return "\n".join(lines) if lines else "· （这次没有读到可检索的资料）"


def _empty_pages_note(corpus: doc_index.Corpus) -> str:
    """有的资料里有"提不出文字的页" → 说清楚（B6）。没有就返回空串。"""
    for document in corpus.documents:
        if document.empty_pages:
            return EMPTY_PAGES_NOTE.format(filename=document.filename, count=document.empty_pages)
    return ""


def overview_answer(corpus: doc_index.Corpus) -> DocAnswer:
    """概览：**每一节各挑一句原文** + 每节各自带出处。

    为什么不拿"最像的那一段"回答："这个资料讲了什么"要的是全貌，
    检索排名挑出来的是"最像问题的那一段"，两者不是一回事。
    """
    blocks: list[str] = []
    findings: list[dict[str, Any]] = []
    for document in corpus.documents[:MAX_OVERVIEW_DOCUMENTS]:
        listed = listed_positions(document, MAX_OVERVIEW_SECTIONS)
        unit = unit_of(document)
        if len(corpus.documents) == 1:
            head = f"你导入的《{document.filename}》一共 {document.position_count} {unit}，内容大致是这些："
        else:
            head = f"《{document.filename}》（共 {document.position_count} {unit}）："
        lines = [head, ""]
        for index, (key, label, title, _end) in enumerate(listed, start=1):
            chunk = document.first_chunk_of(key)
            if chunk is None:
                continue
            excerpt = best_sentences(chunk.text, set(), limit=1, max_chars=90)
            name = f"{label} · {title}" if title else label
            lines.append(f"{index}. {name}：{excerpt}")
            lines.append(f"   出处：{citation_of(chunk)}")
            findings.append({
                "document_id": document.document_id, "filename": document.filename,
                "position": chunk.position_label, "position_title": chunk.position_title,
                "quote": excerpt,
            })
        hidden = document.position_count - (listed[-1][3] if listed else 0)
        if hidden > 0:
            lines.append(f"（还有 {hidden} {unit}没有列出来。）")
        lines.append("")
        blocks.append("\n".join(lines).strip())
    # 只讲前几份、又不提剩下的，等于让用户以为库里就这么点东西 —— 把没讲的点名说出来
    rest = corpus.documents[MAX_OVERVIEW_DOCUMENTS:]
    if rest:
        names = "、".join(f"《{document.filename}》" for document in rest)
        blocks.append(f"（这次先讲了前 {MAX_OVERVIEW_DOCUMENTS} 份，还有 {len(rest)} 份没有讲：{names}。）")
    text = "\n\n".join(blocks) + "\n\n" + QUOTE_FOOTER
    return DocAnswer(kind=KIND_ANSWER, title=TITLE_ANSWER, text=text, findings=tuple(findings))


def _hit_answer(result: doc_index.SearchResult) -> DocAnswer:
    """命中：每一条都引用原文 + 出处（**引用是选择，不是改写**）。"""
    lines = [f"我在你导入的资料里找到 {len(result.hits)} 处相关内容：", ""]
    findings: list[dict[str, Any]] = []
    for hit in result.hits:
        chunk = hit.chunk
        quote = best_sentences(chunk.text, set(result.query_units))   # 挑与问题最沾边的原句
        lead, body = attribute_of(quote)
        lines.append(f"· {citation_of(chunk)}")
        lines.append(f"  {lead}：「{body}」")
        findings.append({
            "document_id": chunk.document_id, "filename": chunk.filename,
            "position": chunk.position_label, "position_title": chunk.position_title,
            "quote": body,
        })
    lines.append("")
    lines.append(QUOTE_FOOTER)
    return DocAnswer(kind=KIND_ANSWER, title=TITLE_ANSWER, text="\n".join(lines),
                     findings=tuple(findings))


def _not_found_answer(question: str, corpus: doc_index.Corpus, searched: doc_index.Corpus) -> DocAnswer:
    """没有找到：明说 + 说明资料里实际有什么（**正文里不许出现被问那件事的任何数字**）。"""
    text = NOT_FOUND_TEXT.format(topic=topic_of(question), catalog=_catalog(searched))
    extra: list[str] = []
    note = _empty_pages_note(searched)
    if note:
        extra.append(note)
    if _asks_about_image(question):
        extra.append(IMAGE_ASK_NOTE)
    if extra:
        text += "\n" + "\n".join(extra)
    return DocAnswer(kind=KIND_NOT_FOUND, title=TITLE_NOT_FOUND, text=text)


# ════════════════════════════════════════════════════════════════════════
# ⑦ 主入口
# ════════════════════════════════════════════════════════════════════════
#: 导入记录里"这份文件提不出文字"的错误码（`app/importer` 与 `app/engine/docs.py` 的取值）
NO_TEXT_CODES: tuple[str, ...] = ("no_content", "document_no_text")

#: 失败原因 → 人话（认不出来就说"这次没成功"，不回声内部码）
_FAILED_REASONS: dict[str, str] = {
    "no_content": "它没有可提取的文字",
    "document_no_text": "它没有可提取的文字",
    "document_broken": "文件打不开或已损坏",
    "encoding_unknown": "编码认不出来",
    "parse_failed": "解析失败",
    "too_many_slides": "页数超过上限",
    "markdown_too_large": "文件太大",
}


def _named_hint(question: str) -> str:
    """问句里点名的文件（书名号优先，其次带后缀的文件名）；没点名就返回空串。"""
    book = _BOOK_RE.search(question or "")
    if book:
        return book.group(1).strip()
    found = _FILE_NAME_RE.search(question or "")
    return found.group(0).strip() if found else ""


def _recent_import(filename_hint: str) -> dict[str, Any] | None:
    """导入记录里最近一条**这个文件名**的记录（用来解释"为什么它不在可检索的资料里"）。"""
    try:
        with db.readonly() as connection:
            records, _total = store.list_imports(connection, limit=50, offset=0)
    except Exception:                                     # noqa: BLE001 —— 读不出来就不再往下解释
        return None
    needle = filename_hint.strip()
    for record in records:
        name = str(record.get("source_filename") or "")
        if name and (needle in name or name in needle):
            return record
    return None


def _missing_file_answer(hint: str, corpus: doc_index.Corpus) -> DocAnswer:
    """点名了一份**库里没有**的文件：如实说没有，并把库里真有的列出来（B4：不许挑近似的顶上）。"""
    record = _recent_import(hint)
    if record is not None and str(record.get("status")) == "failed":
        code = str(record.get("error_code") or "")
        name = str(record.get("source_filename") or hint)
        if code in NO_TEXT_CODES:
            return DocAnswer(kind=KIND_NO_TEXT, title=TITLE_NO_TEXT,
                             text=NO_TEXT_TEXT.format(filename=name))
        return DocAnswer(
            kind=KIND_NOT_FOUND, title=TITLE_NOT_FOUND,
            text=FAILED_IMPORT_TEXT.format(filename=name,
                                           reason=_FAILED_REASONS.get(code, "这次没有成功")),
        )
    names = "、".join(f"《{document.filename}》" for document in corpus.documents[:MAX_CATALOG_DOCUMENTS])
    return DocAnswer(kind=KIND_NOT_FOUND, title=TITLE_NOT_FOUND,
                     text=MISSING_FILE_TEXT.format(hint=hint, names=names or "（这次没有读到资料）"))


def _empty_answer() -> DocAnswer:
    """库里一份资料都没有（**真的空库**）—— 明说没有可检索的东西，并告诉用户怎么导。"""
    from app.ai import system_info                       # 局部 import：只为了拿"能导什么格式"

    return DocAnswer(
        kind=KIND_EMPTY, title=TITLE_EMPTY,
        text=EMPTY_TEXT.format(formats=system_info.format_labels()),
    )


def answer_question(question: str, *, corpus: doc_index.Corpus | None = None,
                    fallback: bool = False) -> DocAnswer | None:
    """资料类问题 → `DocAnswer`；**不是**资料类问题 → None（由别的分支回答）。

    纯代码：不调模型、不写记录、不改任何状态（记录由 `app.ai.service` 组装）。

    `fallback=True`（★ FR-010-D）表示调用方已经判过"这是漏网的那一类"（点名了资料、
    却没有用"问内容"那套说法，且库里有资料）—— 那就**越过判据②**照检索流程走：
    命中就给带出处的回答，没命中就照 `NOT_FOUND_TEXT` 说没找到。
    它不是"更宽的资料问答"，而是同一个流程的另一个入口 —— 检索、出处、四种出口一个字没改。
    """
    if not fallback and not is_document_question(question):
        return None

    corpus = corpus if corpus is not None else doc_index.load_corpus()
    hint = _named_hint(question)

    if not corpus.documents:
        if corpus.read_failed:
            # ★ 读不出来 ≠ 没有导入过（与 A3 对导入记录同一条规矩）：把读失败说成"你没有资料"
            #   就是一句假话 —— 而且它会让用户白白重新导入一遍。
            return DocAnswer(kind=KIND_NOT_FOUND, title=TITLE_NOT_FOUND, text=READ_FAILED_TEXT)
        if hint:
            return _missing_file_answer(hint, corpus)
        return _empty_answer()

    document = corpus.find_document(hint) if hint else None
    if hint and document is None:
        return _missing_file_answer(hint, corpus)

    # 点名了一份资料 → **只在这份资料里找**（从别的资料里引一段来回答就是答非所问）
    searched = doc_index.scoped(corpus, document) if document is not None else corpus

    if _is_overview(question):
        return overview_answer(searched)

    # ★ 检索用的是**剥掉脚手架的那个词**（「资料里有没有写买量成本」→「买量成本」）：
    #   脚手架词（资料 / 有没有 / 提到）会把覆盖率的分母撑大，让本该命中的句子判成没命中。
    #   为什么要剥、剥了之后两道判据各自怎么算，写在 `app/ai/doc_index.py` 开头 §④。
    result = doc_index.search(topic_of(question), corpus=searched)
    if result.hits:
        return _hit_answer(result)

    answer = _not_found_answer(question, corpus, searched)
    if searched.notes:
        answer = DocAnswer(kind=answer.kind, title=answer.title,
                           text=answer.text + "\n" + "\n".join(searched.notes),
                           findings=answer.findings)
    return answer


__all__ = [
    "BANNED_WORDS",
    "DocAnswer",
    "KIND_ANSWER",
    "KIND_EMPTY",
    "KIND_NOT_FOUND",
    "KIND_NO_TEXT",
    "attribute_of",
    "answer_question",
    "best_sentences",
    "citation_of",
    "has_content_ask",
    "has_documents",
    "is_document_fallback",
    "is_document_question",
    "listed_positions",
    "overview_answer",
    "points_at_document",
    "sentences_of",
    "topic_of",
    "unit_of",
]
