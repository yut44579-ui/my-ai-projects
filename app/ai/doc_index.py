"""doc_index.py · 外部资料的**分块 + 检索**（FR-010-B）。

════════════════════════════════════════════════════════════════════════
【这个模块干什么 / 不干什么】
════════════════════════════════════════════════════════════════════════
干什么（**纯代码，没有任何模型调用**）：
    ① 把一份资料的正文切成**片段**（chunk），每段带上**位置元数据**；
    ② 用**轻量 BM25** 给片段打分，回答"用户这句话在资料里的哪一段"；
    ③ 索引落盘缓存，**不许每次提问重解析全文**。

不干什么：
    · 不用向量 / embedding / 新数据库 / 新依赖（评审已定：本期不引 jieba、不引向量库）；
    · 不重写解析：正文一律取自 `app.repositories.unified_documents.get_text()`
      （FR-003 的既有解析产物），本模块只看**已经存下来的文本**；
    · 不读销售数据（本模块跟 `tools` / `engine` 一点关系都没有，见 §五的结构性隔离）。

════════════════════════════════════════════════════════════════════════
【① 怎么切：按章节 / 段落，位置元数据跟着一起走】
════════════════════════════════════════════════════════════════════════
标题识别（三档，都是**从正文里看出来的**，不是猜的）：

    Markdown / PPT 标题行   `#`/`##`/… 开头（PPTX 的正文里本来就有 `## 第 N 页`）
    编号标题行              `一、` / `1.2` / `第 3 章` 这类且**行很短**（≤ 40 字）
    都没有                  → 位置回退成「第 N 段」

位置回退规则（★ 派单 §二 B2 原话：**不许编页码**）：

    · 正文里**有** `## 第 N 页` 这类页标记（PPTX 产物）→ 出处写「第 N 页」；
    · 没有页标记（PDF/Word/Markdown 的存库正文里**没有**分页信息）→ 写「第 N 节」；
    · 连标题都没有 → 写「第 N 段」。
    ★ 绝不"按字数估算一个页码" —— 拿不到就说拿不到。PDF 的存库正文是各页拼起来的
      （`engine/docs.py::extract_pdf` 的行为），页边界在存下来的那一刻就没了，
      从这段文本里推页码只能是编。所以宁可用「第 N 节」，也不用假页码。

一段正文怎么分组：同一节里的连续段落攒到 CHUNK_CHARS 字左右为一个片段
（**不跨节**合并，否则一节的内容会被拼到另一节的出处下面）。

════════════════════════════════════════════════════════════════════════
【② 中文怎么处理：字符 2-gram + 英文按词（评审已定：本期不引 jieba）】
════════════════════════════════════════════════════════════════════════
中文没有空格，不分词的检索有两种做法：引一个分词库，或者用**字符 n-gram**。
评审已经定了本期**不引 jieba**，所以走 n-gram：连续汉字段落切成**相邻两字**为一个词
（「买量成本」→ 买量 / 量成 / 成本）。两字足够表达中文里绝大多数词，又不需要词典；
英文与数字按 `[a-z0-9]+` 整词切分，两边混在一张倒排表里（字符集不重叠，不会互相干扰）。
单个汉字的片段（如标题「序」）保留它自己，否则那一个字永远检索不到。

════════════════════════════════════════════════════════════════════════
【③ 怎么打分：BM25（纯 Python，约 20 行）】
════════════════════════════════════════════════════════════════════════
BM25 只做三件事：词频（tf）越高越相关；词越稀有（idf）越值钱；片段越长越要打折（长度归一）。
它不需要训练、不需要模型、没有随机性 —— 同样的问题永远给同样的排序，**可复现、可单测**。

════════════════════════════════════════════════════════════════════════
【④ 什么算"命中"：覆盖率 + 稀有词（两道都成立才算）】
════════════════════════════════════════════════════════════════════════
光看分数会出事。「资料里有没有写买量成本？」这句话里的「成本」如果资料里**别处**出现过，
BM25 会给一个不低的分数，于是系统就会拿着讲别的事的那一段回答"买量成本"—— 那是**假命中**，
比"没找到"糟得多（派单 §二 B1：检索不到必须说没找到，**禁止补充生成**）。

所以命中要两道同时成立（都是纯代码判据，写在 `_is_hit` 里）：

    ① **覆盖率**：问句里的中文词（2-gram）有多少真的出现在这一段里，门槛 `MIN_BIGRAM_COVERAGE`；
       英文/数字按整词算，门槛 `MIN_WORD_COVERAGE`（词没有"相邻两字"那种部分对应关系）。
    ② **至少有一个"稀有词"命中**：查询词里有一个在全部片段中出现次数 ≤ 5%（至少 2 次）——
       用来挡住"资料 / 什么 / 的"这类到处都是的词把覆盖率凑上去。

★ 为什么用 2-gram 覆盖率而不是"单字覆盖率"：单字太松 —— 「买量成本」和一段只写了"成本"的
  文字，单字覆盖率也有 1/2，照样会被判成命中。2-gram 要求"相邻两字一起出现"，
  「买量/量成/成本」三个里只中一个（1/3）就是没中。三组真实句子的量法写在
  `MIN_BIGRAM_COVERAGE` 旁边，门槛 0.4 就是照它们定的。

★ 检索用的问句先经 `doc_qa.topic_of` 剥掉脚手架（"资料里有没有写…"→"买量成本"）：
  脚手架词（资料 / 有没有 / 提到）在覆盖率里是分母上的噪音 —— 不剥掉的话，
  一句语法完整的话会把覆盖率稀释到谁都命中不了。

════════════════════════════════════════════════════════════════════════
【⑤ 索引放在哪：惰性建 + 落盘缓存（不许每次重解析全文）】
════════════════════════════════════════════════════════════════════════
第一次检索时建，落在 `state/doc_index/<document_id>.json`（走 `json_store.state_dir()`，
所以测试隔离 `SRA_STATE_DIR` 时它也跟着隔离，**跑测试不会碰真实 state/**）。
缓存里带着一个**指纹**：`document_id | 文件名 | 字数 | 入库时间`。读缓存前先比指纹，
对不上就重建（重新导入/文档变了都会让指纹变）——
宁可多切一次，也不拿一份过期索引去回答。

缓存**随时可以删**（删了下次重建），它只是"别每次重解析"的加速件，不是真相来源。
真相永远在 `unified_documents` 那边。

★ 为什么指纹不直接哈希全文：读全文正是我们要省掉的那一步（旧管道的文档还要按原文重新提取）。
指纹里那四个字段任一变化都意味着"这不是原来那份文档"，而字数正是文档内容变化的直接后果。
代价如实写在这里：字数与入库时间都没变、正文却被原地改过的文档，缓存不会自动失效。
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.repositories import json_store, unified_documents

# ════════════════════════════════════════════════════════════════════════
# ① 冻结参数（都有理由，别随手改）
# ════════════════════════════════════════════════════════════════════════
#: 一个片段的字数上限。太小则一段话被切碎、回答里引半句；太大则一段里混着好几件事，
#: 引出来读者看不出到底哪句对应问题。300 字上下 ≈ 中文两三段话。
CHUNK_CHARS = 320

#: 一次回答最多引用几个片段（派单 §二 B2："N 要小，够答就行"）。
MAX_CHUNKS = 6

#: 同一份资料最多引用几个片段 —— 防止一份长文档把回答占满，另一份命中的显示不出来。
MAX_PER_DOCUMENT = 3

#: 命中判据①：问句里的**中文词（2-gram）**在片段里的覆盖率下限。
#: 0.4 这个数是拿两组真实句子量出来的（见模块开头 §④ 的两句对照），不是拍的：
#:     「市场计划」 在写着"市场规划 / 计划在明年…"的那一段上覆盖 2/3 ≈ 0.67 → 命中
#:     「明年能增长」在写着"预计明年销售增长"的那一段上覆盖 2/4 = 0.50     → 命中
#:     「买量成本」 在只碰得上"成本"两个字的那一段上覆盖 1/3 ≈ 0.33       → 不命中
#: 取 0.4 让这三条各自都留出余量（0.67 / 0.50 在上，0.33 在下）。
MIN_BIGRAM_COVERAGE = 0.4

#: 英文/数字按**词**算覆盖率：词与词之间没有"相邻两字"那种部分对应关系，
#: 一个词要么在要么不在，所以这里用常见的过半线。
MIN_WORD_COVERAGE = 0.5

#: 命中判据②：稀有词的判定线 —— 在全部片段里出现次数不超过这个比例（至少 2 次）。
RARE_DF_RATIO = 0.05
RARE_DF_MIN = 2

#: BM25 的两个标准参数（k1 控词频饱和、b 控长度归一）。不调参：默认值在短文档上就够用，
#: 调参需要一份标注集，本项目没有 —— 与其"凭感觉调一个"，不如如实用标准值。
BM25_K1 = 1.2
BM25_B = 0.75

#: 位置元数据的三种形态（出处文字直接用它，见模块开头 §①）
POSITION_PAGE = "page"
POSITION_SECTION = "section"
POSITION_PARAGRAPH = "paragraph"

#: 缓存目录名（挂在 state 目录下，所以测试隔离跟着走）
INDEX_DIRNAME = "doc_index"

#: 标题行的字数上限：超过这个长度的"编号行"是正文里的列表项，不是标题。
MAX_HEADING_CHARS = 40

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
# 汉字的三个 Unicode 区（基本区 U+4E00-9FFF / 扩展A U+3400-4DBF / 兼容区 U+F900-FAFF），
# 写成转义码位：这种「范围写错一个码位」的 bug 靠眼睛看不出来，写成码位至少能逐段核对。
_CJK_RE = re.compile("[㐀-䶿一-鿿豈-﫿]+")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s*(\S.*)$")
_PAGE_MARK_RE = re.compile(r"^第\s*(\d+)\s*页$")
_JSON_HEADING_RE = re.compile(
    r"^(?:第\s*[一二三四五六七八九十百零\d]+\s*[章节条部分]"
    r"|\d+(?:\.\d+){0,3}[、.．)）]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|Chapter\s+\d+)"
)


# ════════════════════════════════════════════════════════════════════════
# ② 分词（中文 2-gram + 英文按词）
# ════════════════════════════════════════════════════════════════════════
def tokenize(text: str) -> tuple[str, ...]:
    """一段文字 → 检索词序列（**唯一**的分词实现，建索引与查问题共用）。

    中文：连续汉字段切成相邻两字（「买量成本」→ 买量/量成/成本）；只有一个汉字的段保留本身。
    英文/数字：小写后整词（`BM25` → bm25）。
    """
    raw = text or ""
    tokens: list[str] = [word for word in _WORD_RE.findall(raw.lower())]
    for run in _CJK_RE.findall(raw):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(tokens)


def content_units(text: str) -> set[str]:
    """去重后的"内容字"——命中判据①的分子分母都用它（汉字逐字 + 英文词）。

    为什么这里用**单字**而不是 2-gram：覆盖率回答的是"这段话里到底有没有提到我问的那些字"。
    2-gram 太严：问句「资料说明年能增长多少」与原文「资料里预计明年销售增长 50%」
    共享的字很多（资料/明年/增长），共享的 2-gram 却很少（中间隔着"预计""销售"）——
    用 2-gram 算覆盖率会把该命中的判成没命中。而"假命中"由判据②（稀有词）负责挡。
    """
    raw = text or ""
    units: set[str] = set(_WORD_RE.findall(raw.lower()))
    for run in _CJK_RE.findall(raw):
        units.update(run)
    return units


# ════════════════════════════════════════════════════════════════════════
# ③ 数据形状
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Chunk:
    """资料的一段正文 + 它的位置元数据（出处就是靠后者说出来的）。"""

    document_id: str
    filename: str
    position_kind: str          # page / section / paragraph
    position_number: int        # 页码 / 节号 / 起始段号
    position_end: int           # 段落回退时用（「第 5~7 段」）；其余情况与本段相同
    position_title: str         # 节标题（没有就是空串）
    text: str
    tf: dict[str, int] = field(default_factory=dict)      # 词 → 次数（建索引时算好）

    @property
    def length(self) -> int:
        """片段长度 = 词数（BM25 的长度归一用它，不用字数）。"""
        return sum(self.tf.values())

    @property
    def position_label(self) -> str:
        """给用户看的出处文字（**唯一的**一处拼法）。"""
        if self.position_kind == POSITION_PAGE:
            return f"第 {self.position_number} 页"
        if self.position_kind == POSITION_SECTION:
            return f"第 {self.position_number} 节"
        if self.position_end and self.position_end != self.position_number:
            return f"第 {self.position_number}~{self.position_end} 段"
        return f"第 {self.position_number} 段"

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_kind": self.position_kind,
            "position_number": self.position_number,
            "position_end": self.position_end,
            "position_title": self.position_title,
            "text": self.text,
            "tf": self.tf,
        }

    @classmethod
    def from_dict(cls, document_id: str, filename: str, payload: dict[str, Any]) -> "Chunk":
        return cls(
            document_id=document_id,
            filename=filename,
            position_kind=str(payload.get("position_kind") or POSITION_PARAGRAPH),
            position_number=int(payload.get("position_number") or 0),
            position_end=int(payload.get("position_end") or 0),
            position_title=str(payload.get("position_title") or ""),
            text=str(payload.get("text") or ""),
            tf={str(key): int(value) for key, value in (payload.get("tf") or {}).items()},
        )


@dataclass(frozen=True)
class Document:
    """一份资料的检索视图（元信息 + 它的片段们，按正文顺序）。"""

    document_id: str
    filename: str
    title: str
    char_count: int
    chunks: tuple[Chunk, ...]
    #: 这份资料里**提不出文字**的页数（PDF 的扫描页）。它不参与检索，但回答"没找到"时
    #: 必须能把这件事说出来（派单 §二 B6：不做 OCR，但必须明说读不了图）——
    #: 不说的话，用户会以为"资料里真的没有"，而事实是"那几页我们读不出来"。
    empty_pages: int = 0

    @property
    def position_count(self) -> int:
        """这份资料一共有几个"位置"（节 / 页 / 段）—— 概览回答要说"一共 N 节"。"""
        return len({(chunk.position_kind, chunk.position_number) for chunk in self.chunks})

    def positions(self) -> list[tuple[str, str, str]]:
        """`[(位置标识, 出处文字, 节标题)]`，按正文顺序（概览回答按它列）。"""
        seen: set[tuple[str, int]] = set()
        out: list[tuple[str, str, str]] = []
        for chunk in self.chunks:
            key = (chunk.position_kind, chunk.position_number)
            if key in seen:
                continue
            seen.add(key)
            out.append((f"{chunk.position_kind}:{chunk.position_number}", chunk.position_label,
                        chunk.position_title))
        return out

    def first_chunk_of(self, position_key: str) -> Chunk | None:
        """某个位置的第一段（概览回答里"每一节挑一句原文"就用它）。"""
        for chunk in self.chunks:
            if f"{chunk.position_kind}:{chunk.position_number}" == position_key:
                return chunk
        return None


@dataclass(frozen=True)
class Corpus:
    """全部资料的检索语料（一次检索读一份，不在模块里存全局状态）。"""

    documents: tuple[Document, ...]
    chunks: tuple[Chunk, ...]
    df: dict[str, int]
    average_length: float
    #: "某一份资料的正文这次没读到"之类的如实说明（回答"没找到"时要能说出来）。
    #: ★ 只说**影响检索**的事。列表层的降级说明（"有 N 份历史文档与新导入的是同一个文件"）
    #:   不往这里塞 —— 那是给「文档资料」列表看的，摆在一条资料问答的答案里只会让人误解。
    notes: tuple[str, ...] = ()
    #: 整个资料库这次读不出来（不是"空库"）—— 调用方**必须**把它与"没有导入过"分开说。
    read_failed: bool = False

    @property
    def has_any_text(self) -> bool:
        return bool(self.chunks)

    def rare_df_limit(self) -> int:
        """稀有词的出现次数上限（见模块开头 §④）。"""
        return max(RARE_DF_MIN, int(round(len(self.chunks) * RARE_DF_RATIO)))

    def find_document(self, hint: str) -> Document | None:
        """按**文件名**找一份资料（派单 §二 B4：文件名只用于定位文件，不构成事实）。

        匹配方式：文件名（含后缀）或去掉后缀的名字，与提示串互相包含。
        找不到就返回 None —— 由调用方如实说"库里没有这份资料"，**不许挑一份近似的顶上**。
        """
        needle = (hint or "").strip()
        if not needle:
            return None
        for document in self.documents:
            stem = Path(document.filename).stem
            if needle in (document.filename, stem) or document.filename in needle or stem in needle:
                return document
        return None


@dataclass(frozen=True)
class Hit:
    """一个命中片段（回答里引用的就是它，出处一起带上）。"""

    chunk: Chunk
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.chunk.document_id,
            "filename": self.chunk.filename,
            "position": self.chunk.position_label,
            "position_title": self.chunk.position_title,
            "score": round(self.score, 4),
            "text": self.chunk.text,
        }


@dataclass(frozen=True)
class SearchResult:
    """一次检索的完整结果（命中片段 + 用过的语料 —— 没命中时还要用它说明"资料里有什么"）。"""

    hits: tuple[Hit, ...]
    corpus: Corpus
    query_units: frozenset[str] = frozenset()
    rare_df_limit: int = 0


# ════════════════════════════════════════════════════════════════════════
# ④ 分块
# ════════════════════════════════════════════════════════════════════════
def _heading_of(line: str) -> tuple[str, int, str] | None:
    """这一行是不是标题？是 → `(位置类型, 编号, 标题文字)`；不是 → None。

    编号对 `page` 是**页码本身**（PPTX 的 `## 第 3 页`），对 `section` 先给 0（后面按顺序编）。
    """
    match = _MD_HEADING_RE.match(line)
    if match:
        title = match.group(2).strip()
        page = _PAGE_MARK_RE.match(title)
        if page:
            return POSITION_PAGE, int(page.group(1)), ""
        return POSITION_SECTION, 0, title
    if len(line) <= MAX_HEADING_CHARS and _JSON_HEADING_RE.match(line):
        return POSITION_SECTION, 0, line
    return None


def _group_paragraphs(paragraphs: list[str]) -> list[tuple[int, int, str]]:
    """同一节里的段落 → `[(起段号, 止段号, 正文)]`（攒到 CHUNK_CHARS 字左右一段）。

    单段就超长的（例如整篇是一段）按 CHUNK_CHARS **硬切**：切点宁可难看，也不能让一段
    撑成几千字 —— 那样 BM25 的长度归一和回答里的引用都会失真。
    """
    groups: list[tuple[int, int, str]] = []
    buffer: list[str] = []
    start = 0                             # 本组第一段是第几段
    last = 0                              # 本组最后一段是第几段
    for index, paragraph in enumerate(paragraphs, start=1):
        pieces = [paragraph[offset:offset + CHUNK_CHARS]
                  for offset in range(0, len(paragraph), CHUNK_CHARS)] or [paragraph]
        for piece in pieces:
            if buffer and len("".join(buffer)) + len(piece) > CHUNK_CHARS:
                groups.append((start, last, "\n".join(buffer)))
                buffer = []
            if not buffer:
                start = index
            buffer.append(piece)
            last = index
    if buffer:
        groups.append((start, last, "\n".join(buffer)))
    return groups


def chunk_document(document_id: str, filename: str, text: str) -> list[Chunk]:
    """一份正文 → 片段列表（**纯函数**，不读盘不落盘）。

    位置规则见模块开头 §①：有页标记按页、有标题按节、都没有按段。
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    # ── 先按标题行切"节"，每节记下它的位置类型与标题 ────────────────────
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        if not line:
            continue
        heading = _heading_of(line)
        if heading is not None:
            kind, number, title = heading
            current = {"kind": kind, "number": number, "title": title, "paragraphs": []}
            sections.append(current)
            continue
        if current is None:
            current = {"kind": POSITION_SECTION, "number": 0, "title": "", "paragraphs": []}
            sections.append(current)
        current["paragraphs"].append(line)

    sections = [section for section in sections if section["paragraphs"]]
    if not sections:
        return []

    # ── 整篇一个标题都没有 → 位置回退成「第 N 段」 ───────────────────────
    if len(sections) == 1 and sections[0]["kind"] == POSITION_SECTION and not sections[0]["title"]:
        chunks: list[Chunk] = []
        for start, end, body in _group_paragraphs(sections[0]["paragraphs"]):
            chunks.append(_make_chunk(document_id, filename, POSITION_PARAGRAPH, start, end, "", body))
        return chunks

    # ── 有标题：节号按出现顺序编（页标记用页码本身，不重编）──────────────
    chunks = []
    section_number = 0
    for section in sections:
        if section["kind"] == POSITION_SECTION:
            section_number += 1
            number = section_number
        else:
            number = int(section["number"])
        for start, end, body in _group_paragraphs(section["paragraphs"]):
            chunks.append(
                _make_chunk(document_id, filename, section["kind"], number, number,
                            str(section["title"]), body)
            )
    return chunks


def _make_chunk(
    document_id: str, filename: str, kind: str, number: int, end: int, title: str, body: str
) -> Chunk:
    counts: dict[str, int] = defaultdict(int)
    for token in tokenize(f"{title}\n{body}"):
        counts[token] += 1
    return Chunk(
        document_id=document_id,
        filename=filename,
        position_kind=kind,
        position_number=number,
        position_end=end,
        position_title=title,
        text=body,
        tf=dict(counts),
    )


# ════════════════════════════════════════════════════════════════════════
# ⑤ 索引缓存（惰性建 + 落盘）
# ════════════════════════════════════════════════════════════════════════
def index_dir() -> Path:
    """缓存目录（每次现读环境变量，测试改了 `SRA_STATE_DIR` 立刻生效）。"""
    return json_store.state_dir() / INDEX_DIRNAME


def _cache_file(document_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", document_id or "document")
    return index_dir() / f"{safe}.json"


def _fingerprint(record: dict[str, Any]) -> str:
    """缓存指纹（见模块开头 §⑤）：文档变了它就变。"""
    return "|".join((
        str(record.get("document_id") or ""),
        str(record.get("filename") or ""),
        str(record.get("char_count") or 0),
        str(record.get("created_at") or ""),
    ))


def _read_cache(record: dict[str, Any]) -> list[Chunk] | None:
    path = _cache_file(str(record.get("document_id")))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                          # 没有缓存 / 缓存坏了 —— 都是"重建"，不是错误
    if str(payload.get("fingerprint") or "") != _fingerprint(record):
        return None
    chunks = [
        Chunk.from_dict(str(record.get("document_id")), str(record.get("filename")),
                        item)
        for item in payload.get("chunks") or []
    ]
    return chunks or None


def _write_cache(record: dict[str, Any], chunks: list[Chunk]) -> None:
    """写缓存：临时文件 + 原子替换（与 json_store 同一套做法）。

    写不进去（没权限 / 磁盘满）**不影响正确性** —— 下次重建一遍而已，绝不因此让检索失败。
    """
    path = _cache_file(str(record.get("document_id")))
    payload = {
        "schema": 1,
        "fingerprint": _fingerprint(record),
        "document_id": record.get("document_id"),
        "filename": record.get("filename"),
        "chunks": [chunk.to_dict() for chunk in chunks],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def clear_cache() -> None:
    """清掉索引缓存（排障与测试用：删了下次自动重建，不影响任何数据）。"""
    directory = index_dir()
    if not directory.is_dir():
        return
    for item in directory.glob("*.json"):
        try:
            item.unlink()
        except OSError:
            continue


def chunks_of(record: dict[str, Any], text: str) -> list[Chunk]:
    """一份文档的片段：先用缓存，指纹对不上再切一遍并落盘。"""
    cached = _read_cache(record)
    if cached is not None:
        return cached
    chunks = chunk_document(str(record.get("document_id")), str(record.get("filename")), text)
    _write_cache(record, chunks)
    return chunks


# ════════════════════════════════════════════════════════════════════════
# ⑥ 读语料（走既有仓储层，**不另写一套取数逻辑**）
# ════════════════════════════════════════════════════════════════════════
def load_corpus(limit: int = unified_documents.MAX_UNIFIED_DOCUMENTS) -> Corpus:
    """把已导入的资料读成检索语料（元信息走统一文档层，正文走它的 `get_text`）。

    读不出来**如实记进 `notes`**，不让整条链路失败（与统一文档层"一个来源坏了不影响另一个"
    同一条规矩）：一份历史文档的原文没了 → 那一份这次不参与检索，其余照常。
    """
    notes: list[str] = []
    try:
        page, _total, _list_notes = unified_documents.list_documents(limit=limit, offset=0)
    except Exception:                                     # noqa: BLE001 —— 读不出来就是读不出来
        return Corpus(documents=(), chunks=(), df={}, average_length=0.0, read_failed=True)

    documents: list[Document] = []
    chunks: list[Chunk] = []
    for record in page:
        if not int(record.get("char_count") or 0):
            continue                                      # 没有正文的那一条（失败/空文档）不参与检索
        try:
            text = unified_documents.get_text(str(record.get("document_id")))
        except unified_documents.DocumentTextUnavailable:
            notes.append(f"《{record.get('filename')}》的原文已经不在本机了，这份资料这次没有参与检索。")
            continue
        except Exception as exc:                          # noqa: BLE001
            notes.append(f"《{record.get('filename')}》的正文这次读不出来（{type(exc).__name__}）。")
            continue
        own = chunks_of(record, text)
        if not own:
            continue
        documents.append(Document(
            document_id=str(record.get("document_id")),
            filename=str(record.get("filename") or ""),
            title=str(record.get("title") or ""),
            char_count=int(record.get("char_count") or 0),
            chunks=tuple(own),
            empty_pages=len((record.get("extra") or {}).get("empty_pages") or []),
        ))
        chunks.extend(own)

    df: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        for token in chunk.tf:
            df[token] += 1
    average_length = (sum(chunk.length for chunk in chunks) / len(chunks)) if chunks else 0.0
    return Corpus(
        documents=tuple(documents),
        chunks=tuple(chunks),
        df=dict(df),
        average_length=average_length,
        notes=tuple(notes),
    )


# ════════════════════════════════════════════════════════════════════════
# ⑦ 检索
# ════════════════════════════════════════════════════════════════════════
def scoped(corpus: Corpus, document: Document) -> Corpus:
    """把语料**缩小到一份资料**（问句点名了《X》时用）。

    为什么要缩：问「《A》里有没有写 X」时，从 B 里检索出一段来回答就是**答非所问**
    （哪怕那段内容确实相关）。词频统计（df / 平均长度）也跟着这份资料的片段重算 ——
    不重算的话，"稀有词"的判定线会按整个库的规模算，一份小资料的命中反而更容易被漏掉。
    """
    chunks = tuple(document.chunks)
    df: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        for token in chunk.tf:
            df[token] += 1
    average_length = (sum(chunk.length for chunk in chunks) / len(chunks)) if chunks else 0.0
    return Corpus(
        documents=(document,),
        chunks=chunks,
        df=dict(df),
        average_length=average_length,
        notes=corpus.notes,
    )


def _bm25_scores(query_tokens: Iterable[str], corpus: Corpus) -> dict[int, float]:
    """问句 → `{片段下标: 分数}`（只留分数 > 0 的）。"""
    total = len(corpus.chunks)
    if not total or corpus.average_length <= 0:
        return {}
    scores: dict[int, float] = defaultdict(float)
    for term in set(query_tokens):
        frequency = corpus.df.get(term, 0)
        if not frequency:
            continue
        idf = math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
        for index, chunk in enumerate(corpus.chunks):
            count = chunk.tf.get(term, 0)
            if not count:
                continue
            denominator = count + BM25_K1 * (
                1 - BM25_B + BM25_B * chunk.length / corpus.average_length
            )
            scores[index] += idf * count * (BM25_K1 + 1) / denominator
    return {index: score for index, score in scores.items() if score > 0}


def _is_cjk_token(token: str) -> bool:
    """这个检索词是不是中文词（2-gram 或单字）—— 中文与英文用**两套**覆盖率（见 §④）。"""
    return bool(token) and ord(token[0]) > 0x2FFF


def _is_hit(query_tokens: tuple[str, ...], chunk: Chunk, corpus: Corpus) -> bool:
    """命中判据（两道同时成立，见模块开头 §④）。"""
    distinct = set(query_tokens)
    if not distinct:
        return False

    # ① 覆盖率：中文按 2-gram、英文按词，各看各的（两套都在才算过）
    for is_cjk, floor in ((True, MIN_BIGRAM_COVERAGE), (False, MIN_WORD_COVERAGE)):
        group = [token for token in distinct if _is_cjk_token(token) == is_cjk]
        if not group:
            continue
        matched = sum(1 for token in group if token in chunk.tf)
        if matched / len(group) < floor:
            return False

    # ② 稀有词：至少一个"有信息量"的词真的对上了（挡住"资料/什么/的"把覆盖率凑上去）
    limit = corpus.rare_df_limit()
    return any(
        corpus.df.get(token, 0) and corpus.df[token] <= limit and token in chunk.tf
        for token in distinct
    )


def search(
    question: str,
    *,
    max_chunks: int = MAX_CHUNKS,
    max_per_document: int = MAX_PER_DOCUMENT,
    corpus: Corpus | None = None,
) -> SearchResult:
    """一句话 → 命中的片段（按分数排序，同一份资料最多 `max_per_document` 段）。

    传进来的应该是**检索用的话**（`doc_qa.topic_of` 剥掉脚手架之后的那个词），
    不是用户原句 —— 理由见模块开头 §④ 最后一段。

    **没有任何副作用**：不写记录、不调模型、不改任何状态。语料可以外部传进来（测试用）。
    """
    corpus = corpus if corpus is not None else load_corpus()
    query_tokens = tokenize(question)
    query_units = content_units(question)
    empty = SearchResult(hits=(), corpus=corpus, query_units=frozenset(query_units),
                         rare_df_limit=corpus.rare_df_limit())
    if not query_tokens or not corpus.has_any_text:
        return empty

    scores = _bm25_scores(query_tokens, corpus)
    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),          # 分数相同时按正文顺序 —— 确定性
    )
    hits: list[Hit] = []
    per_document: dict[str, int] = defaultdict(int)
    for index, score in ranked:
        chunk = corpus.chunks[index]
        if per_document[chunk.document_id] >= max_per_document:
            continue
        if not _is_hit(query_tokens, chunk, corpus):
            continue
        hits.append(Hit(chunk=chunk, score=score))
        per_document[chunk.document_id] += 1
        if len(hits) >= max_chunks:
            break
    return SearchResult(hits=tuple(hits), corpus=corpus, query_units=frozenset(query_units),
                        rare_df_limit=corpus.rare_df_limit())


__all__ = [
    "BM25_B",
    "BM25_K1",
    "CHUNK_CHARS",
    "INDEX_DIRNAME",
    "MAX_CHUNKS",
    "MAX_PER_DOCUMENT",
    "MIN_BIGRAM_COVERAGE",
    "MIN_WORD_COVERAGE",
    "POSITION_PAGE",
    "POSITION_PARAGRAPH",
    "POSITION_SECTION",
    "RARE_DF_MIN",
    "RARE_DF_RATIO",
    "Chunk",
    "Corpus",
    "Document",
    "Hit",
    "SearchResult",
    "chunk_document",
    "chunks_of",
    "clear_cache",
    "content_units",
    "index_dir",
    "load_corpus",
    "scoped",
    "search",
    "tokenize",
]
