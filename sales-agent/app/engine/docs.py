"""docs.py · Word / PDF 文档的**真实文本提取** + **规则化结构化摘要**（TASK-003 第一阶段）。

════════════════════════════════════════════════════════════════════════
【本模块干什么 / 不干什么】
════════════════════════════════════════════════════════════════════════
干什么（两个纯函数，都是"读文件 → 出结构"，不碰 HTTP、不碰落盘）：
    extract()    把 .docx / .pdf 读成文本（段落 / 页为最小块），并给出结构信息
    summarize()  从文本里做**抽取式**摘要 + 关键词 + 关键事实（数字/日期/百分比）

**不干什么（Gate 已定，第一阶段明确不做）**：
    · 不上 RAG、不上向量库、不做 embedding、不做检索问答（留给后续 TASK）
    · 不调用任何 LLM：本模块没有任何网络请求、没有任何模型调用，纯本地规则
    · 不做 OCR：扫描件（图片页）提不出文字时**如实报出来**，不猜、不编

════════════════════════════════════════════════════════════════════════
【为什么摘要必须"抽取式"而不是"生成式"】
════════════════════════════════════════════════════════════════════════
本项目的铁律是"**数字一律由程序算，LLM 不算数字**"。在没有 LLM 的第一阶段，
"摘要"只有一种诚实做法：**原句摘录**（extractive）—— 摘要里的每一句都能在原文里
一字不差地找到，`key_facts` 里的每个值都是正则从原文里抠出来的**字面量**，
连上下文（context）都是原文切片。这样任何人拿到摘要都能回原文核对，不存在"编"的可能。

所以本模块的输出里带着 `method` 与 `note` 字段，明说"这是词频/正则抽取，不是理解"。

════════════════════════════════════════════════════════════════════════
【文本长度 > 0 才算成功（指令原文）】
════════════════════════════════════════════════════════════════════════
`extract()` 对"提不出文本"的文件**抛错**而不是返回空串：
`DocumentError(code="document_no_text")`。调用方（api 层）据此回 422，
让它跟"上传成功"明确区分开 —— 上传一个扫描 PDF 却被告知"成功"，是最坏的一种欺骗。

依赖：python-docx（docx）、pypdf（pdf）—— 都是成熟库，本模块不自己解析 zip/PDF 结构。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

# 本阶段支持的文档后缀（新路径专用；老表格上传仍是 api.py 的 ALLOWED_UPLOAD_SUFFIXES）
DOC_SUFFIXES = (".docx", ".pdf")

SUMMARY_METHOD = "rule_based_v1"

# 摘要/关键词/关键事实的默认上限（都是"最多给这么多"，不是凑数）
DEFAULT_MAX_SENTENCES = 5
DEFAULT_MAX_KEYWORDS = 15
DEFAULT_MAX_FACTS = 20


class DocumentError(RuntimeError):
    """文档提取失败（带机器可读 code，供 api 层转成统一错误体）。

    code 取值：
        document_unsupported  后缀不支持（只收 .docx/.pdf）
        document_broken       文件损坏 / 不是有效 docx/pdf
        document_no_text      能打开但**提不出任何文本**（扫描件、空文档）→ 不算成功
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# ① 文本提取
# ════════════════════════════════════════════════════════════════════════
# 中文 Word 里标题样式名可能是 "Heading 1"，也可能是本地化的 "标题 1"
_HEADING_STYLE_PREFIXES = ("heading", "标题")

# 连续空白压成一个空格（PDF 提取出来的行内空档经常是多个空格）
_WHITESPACE_RUN = re.compile(r"[ \t　]{2,}")


def _clean(line: str) -> str:
    return _WHITESPACE_RUN.sub(" ", line.replace("\xa0", " ")).strip()


def _is_heading_style(style_name: str | None) -> bool:
    name = (style_name or "").strip().lower()
    return any(name.startswith(prefix) for prefix in _HEADING_STYLE_PREFIXES)


def extract_docx(path: Path) -> dict:
    """读 docx：段落 + 表格文字；标题样式收集成大纲。"""
    import docx  # 局部 import：只有真的处理 docx 时才需要这个依赖

    try:
        document = docx.Document(str(path))
    except Exception as exc:                       # noqa: BLE001 —— docx 的异常类型很多（zip/XML 都算）
        raise DocumentError("document_broken", f"这个 .docx 打不开（{type(exc).__name__}）：{exc}") from exc

    lines: list[str] = []
    outline: list[str] = []
    for paragraph in document.paragraphs:
        text = _clean(paragraph.text or "")
        if not text:
            continue
        lines.append(text)
        if _is_heading_style(getattr(paragraph.style, "name", None)):
            outline.append(text)

    # 表格：一行一行拍平成 "单元格 | 单元格"（文档里的数据表常常只在表格里）
    table_rows = 0
    for table in document.tables:
        for row in table.rows:
            cells = [_clean(cell.text or "") for cell in row.cells]
            joined = " | ".join(cell for cell in cells if cell)
            if joined:
                lines.append(joined)
                table_rows += 1

    title = ""
    try:
        title = _clean(document.core_properties.title or "")
    except Exception:                              # noqa: BLE001 —— 属性区坏了不该让提取失败
        title = ""
    if not title:
        # 副标题优先级：文档属性里的 title → 第一个标题样式段 → 第一段
        title = outline[0] if outline else (lines[0] if lines else "")

    return {
        "text": "\n".join(lines),
        "blocks": len(lines),
        "block_unit": "paragraph",
        "title": title,
        "outline": outline[:50],
        "warnings": [],
        "extra": {"tables": len(document.tables), "table_rows": table_rows},
    }


def _clean_page(page_text: str) -> str:
    """一页的逐行清理（原 extract_pdf 里的那两行，抽出来给主路径和兜底路径共用）。"""
    return "\n".join(part for part in (_clean(line) for line in page_text.splitlines()) if part)


# ── FR-013 兜底解码 ─────────────────────────────────────────────────────
# 有些中文 PDF（PPT 导出）用 /Type0 + /Encoding /UniGB-UTF16-H 字体，**不带 ToUnicode 表**。
# pypdf 的 page.extract_text() 走自己的 cmap 解码时会把 2 字节 UTF-16 的字节对齐搞坏
# （高位字节丢成 NUL），正文变成 "\x00A\x00I\x00 …" 这副样子。
# 兜底做法：用 pypdf 自己的 ContentStream 取出**原始字符串字节**，按 UTF-16BE 解。
#
# ★ 两道闸门（两道都过才替换，缺一不可）——这是"正常 PDF 一个字不变"的保证：
#   闸门① _looks_broken  只有结果**确实可疑**才去试兜底（正常 PDF 连试都不试）
#   闸门② _is_better_text 兜底结果必须**确实更干净**才被采用（误进兜底也换不掉原文）
_FALLBACK_WARNING = "该 PDF 字体为 Type0/UniGB-UTF16-H 且无 ToUnicode 表，已用 UTF-16 兜底解码"

# 闸门①的阈值：NUL 是"UTF-16 高位字节被丢掉"的指纹，一条就够定案
_BROKEN_CTRL_RATIO = 0.02        # 替换符 + 控制字符（不含 \n \t \r）占比超过这个数 → 可疑
_BROKEN_MIN_CHARS = 60           # 短于这个长度的页不走"中文占比"那条判据（短标题页容易误伤）
_BROKEN_NON_ASCII_RATIO = 0.30   # 非 ASCII 占比够高……
_BROKEN_CJK_RATIO = 0.10         # ……但中文占比极低 → 是乱码（正常中文 PDF 恰好相反）

# 闸门②的阈值：两边都没有脏字符时，要求中文"成倍变多"才算更好（乱码页解码后中文是成倍出现的）
_CJK_GAIN_FACTOR = 2.0
_CJK_GAIN_ABS = 20

_CTRL_KEEP = "\n\t\r"            # 这三种控制字符是正常排版换行，不算"脏"
_STRIP_FOR_COUNT = re.compile(r"\s+")

# 内容流里"换了行/新起一段"的定位算符（BT 起一个文本块，其余四个改文本基线）
_LINE_OPS = (b"Tm", b"Td", b"TD", b"T*", b"BT")


def _cjk_count(text: str) -> int:
    """中文（基本区）字符数 —— 判"解码对不对"最直接的指标。"""
    return sum(1 for char in text if "一" <= char <= "鿿")


# "正常正文该有的字符"白名单（ASCII 可见字符 / 中文 / 中英标点 / 全角符号 / 常见排版符号）。
# 白名单**故意收得紧**：解错的 UTF-16 会散落到西里尔、谚文、注音、CJK 扩展 A 等一堆
# 互不相干的区段里，那些全在名单外 —— 这正是 `_junk_count` 能认出乱码的原因。
_SANE_RANGES = (
    (0x20, 0x7E), (0xA5, 0xA5), (0xB0, 0xB0), (0xB7, 0xB7), (0xD7, 0xD7),
    (0x2010, 0x2027), (0x2030, 0x205E), (0x2190, 0x2199),
    (0x3000, 0x303F), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF),
)


def _junk_count(text: str) -> int:
    """数"不像正文"的字符（白名单之外的全算）—— 比较两段解码谁更像正文用。

    只用来**相对比较**（同一段字节的两种解法谁更干净），不设绝对阈值：
    解对了的一段 junk 为 0，解错的一段必然散落一堆冷门区段字符，比出来的结果很稳。
    """
    return sum(
        1 for char in text
        if char not in "\n\t\r" and not any(low <= ord(char) <= high for low, high in _SANE_RANGES)
    )



def _looks_broken(text: str) -> bool:
    """闸门①：这段提取结果是不是**可疑乱码**。

    三条判据（命中任意一条即可疑），全部只在"真像乱码"时才成立：
        1. 含 NUL                       —— UTF-16 高位字节被丢掉的指纹（本缺陷的实测指纹）
        2. 替换符/控制字符占比超阈值      —— 解码失败的直接证据
        3. 非 ASCII 一大堆但中文几乎没有  —— latin-1 式的乱码（第 1、2 条都抓不到它）
    正常 PDF（含正常中文 PDF）三条都不命中：没有 NUL、没有替换符、中文占比高。
    """
    if "\x00" in text:
        return True

    compact = _STRIP_FOR_COUNT.sub("", text)
    total = len(compact)
    if total == 0:
        return False

    dirty = sum(
        1 for char in compact
        if char == "�" or (char not in _CTRL_KEEP and unicodedata.category(char) == "Cc")
    )
    if dirty / total > _BROKEN_CTRL_RATIO:
        return True

    if total >= _BROKEN_MIN_CHARS:
        non_ascii = sum(1 for char in compact if ord(char) > 127)
        if non_ascii / total > _BROKEN_NON_ASCII_RATIO and _cjk_count(compact) / non_ascii < _BROKEN_CJK_RATIO:
            return True
    return False


def _raw_text_chunks(page: Any, reader: Any) -> str:
    """取出这一页所有"文本显示"算符里的字符串（**绕过 pypdf 的字体 cmap 解码**）。

    覆盖四个算符：Tj / ' / " 各取一个字符串，TJ 取数组里的每一段。
    两种字符串形态都要接 —— 同一份 PDF 里两种会**混着出现**（实测这份就是），
    它们其实是同一批 UTF-16 字节的两种写法：

        · 十六进制串 <…>  → pypdf 原样给 bytes：**连续的 bytes 攒在一起**再整段按 UTF-16BE 解
          （必须整段解：逐段解会破坏 2 字节码元的对齐 —— 那正是本缺陷的成因）
        · 字面串 (…)      → pypdf 自己先解过一道，解对了也解错了（实测两种都有）：
          它留着 `original_bytes` 这个原始字节，所以按这份字节重新解一遍，
          再用 `_junk_count` **逐段比一下谁更像正文**，谁干净用谁（一样干净就保持 pypdf 的解）
    """
    from pypdf.generic import ContentStream

    contents = page.get_contents()
    if contents is None:
        return ""

    pieces: list[str] = []
    pending = bytearray()                       # 攒连续的 bytes；遇到字面串或走到头再整段解
    for operands, operator in ContentStream(contents, reader).operations:
        if operator in _LINE_OPS:
            # 定位算符 = 这一页自己认定的"换了一行/新起一段"。只有攒到的字节是**偶数个**时才断行：
            #   断行绝不能以切坏 2 字节码元为代价（宁可少一个换行，也不能把字解错）。
            if pending and len(pending) % 2 == 0:
                pieces.append(_decode_utf16(bytes(pending)))
                pending.clear()
            if pieces and not pending and pieces[-1] != "\n":
                pieces.append("\n")
            continue
        if operator in (b"Tj", b"'", b'"'):
            candidates = list(operands)
        elif operator == b"TJ":
            candidates = [item for operand in operands if isinstance(operand, (list, tuple)) for item in operand]
        else:
            continue
        for candidate in candidates:
            if isinstance(candidate, (bytes, bytearray)):
                pending += candidate
                continue
            if not isinstance(candidate, str):
                continue
            if pending:                         # 先结算攒着的字节，顺序才不乱
                pieces.append(_decode_utf16(bytes(pending)))
                pending.clear()
            original = getattr(candidate, "original_bytes", b"") or b""
            decoded = _decode_utf16(bytes(original)) if original else ""
            pieces.append(decoded if _junk_count(decoded) < _junk_count(candidate) else candidate)
    if pending:
        pieces.append(_decode_utf16(bytes(pending)))
    return "".join(pieces)


def _decode_utf16(raw: bytes) -> str:
    """原始字节按 UTF-16BE 解（解不动就留替换符 —— 那会让它在闸门②里落选，不会被当成好结果）。"""
    return raw.decode("utf-16-be", errors="replace")


def _utf16_fallback(page: Any, reader: Any) -> str:
    """这一页按上面的办法重解一遍（拿不到就返回空串，交给闸门②判）。"""
    try:
        return _raw_text_chunks(page, reader)
    except Exception:                              # noqa: BLE001 —— 兜底自己坏了不该让整份文档失败
        return ""


def _is_better_text(original: str, candidate: str) -> bool:
    """闸门②：兜底结果是否**明显更干净**（只有它更好才准替换）。

    主判据：脏字符（NUL + 替换符）**严格变少**。
    ★ 这一条就是"正常 PDF 不受影响"的硬保证：正常页的脏字符数是 0，
      **不可能有谁严格少于 0** —— 所以正常页哪怕被闸门①误判、白跑一趟兜底，
      也一定原样保留（下面那条中文成倍变多的补充判据也要求候选没有脏字符）。
    """
    if not candidate.strip():
        return False

    dirty_before = original.count("\x00") + original.count("�")
    dirty_after = candidate.count("\x00") + candidate.count("�")
    cjk_before = _cjk_count(original)
    cjk_after = _cjk_count(candidate)

    if dirty_after < dirty_before:
        return cjk_after >= cjk_before          # 更干净了，但中文不许变少
    if dirty_before == 0 and dirty_after == 0:
        # 两边都没有脏字符（latin-1 式的乱码页）→ 要求中文成倍变多，且绝对量说得过去
        return cjk_after >= _CJK_GAIN_ABS and cjk_after >= _CJK_GAIN_FACTOR * cjk_before
    return False


def extract_pdf(path: Path) -> dict:
    """读 pdf：逐页 extract_text；提不出字的页如实记进 warnings。

    提取结果"可疑"的页（见 `_looks_broken`）会再走一遍 UTF-16 兜底解码（FR-013）：
    兜底确实更好就采用并写 warning，没更好就保留原结果、同样如实写 warning（绝不静默吞掉）。
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except Exception as exc:                       # noqa: BLE001
        raise DocumentError("document_broken", f"这个 .pdf 打不开（{type(exc).__name__}）：{exc}") from exc

    pages: list[str] = []
    empty_pages: list[int] = []
    broken_pages: list[int] = []
    rescued_pages: list[int] = []                  # 兜底解码成功、正文已换掉的页
    suspect_pages: list[int] = []                  # 可疑且兜底也没救回来的页（正文仍是原样）
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:                          # noqa: BLE001 —— 单页坏了不该毁掉整份文档
            page_text = ""
            broken_pages.append(index)
        cleaned = _clean_page(page_text)

        if _looks_broken(cleaned):
            decoded = _clean_page(_utf16_fallback(page, reader))
            if _is_better_text(cleaned, decoded):
                cleaned = decoded
                rescued_pages.append(index)
            else:
                suspect_pages.append(index)

        if not cleaned:
            empty_pages.append(index)
        pages.append(cleaned)

    warnings: list[str] = []
    if broken_pages:
        warnings.append(f"第 {broken_pages} 页解析报错，内容已跳过（原文件可能有损坏页）")
    if rescued_pages:
        warnings.append(f"第 {rescued_pages} 页{_FALLBACK_WARNING}")
    if suspect_pages:
        warnings.append(
            f"第 {suspect_pages} 页提取出的文字可疑（含 NUL/控制字符或中文占比异常），"
            "UTF-16 兜底解码没能给出更好的结果 —— 这些页的文字可能不可靠，引用前请核对原文"
        )
    if empty_pages:
        warnings.append(
            f"第 {empty_pages} 页没有可提取文本（共 {page_count} 页），"
            "这类页通常是扫描图片 —— 本阶段不做 OCR，这部分内容**没有**被提取"
        )

    text = "\n".join(pages)
    first_line = next((line for line in pages[0].splitlines() if line.strip()), "") if pages else ""
    return {
        "text": text,
        "blocks": page_count,
        "block_unit": "page",
        "title": first_line,
        "outline": _outline_from_text(text),
        "warnings": warnings,
        "extra": {"empty_pages": empty_pages, "broken_pages": broken_pages},
    }


def extract(path: str | Path, suffix: str | None = None) -> dict:
    """把一份文档读成文本 + 结构信息（**文本长度为 0 就抛错**，不算成功）。

    返回（全部来自文件本身，没有一个字段是编的）：
        text/chars       提取出的全文与字符数（chars > 0 是本函数成功的前提）
        blocks/block_unit 块数与单位（docx=段落数，pdf=页数）
        title            标题（docx 取文档属性/标题样式/首段；pdf 取首页首行）
        outline          大纲（docx 的标题样式段落；pdf 按"第X章/1.2.3"这类编号行猜）
        warnings         如实报告的问题（如扫描页提不出字）——不是错误，是"这部分没提到"
        extra            分类型附加信息（表格数 / 空页清单）
    """
    target = Path(path)
    suffix = (suffix or target.suffix or "").lower()
    if suffix not in DOC_SUFFIXES:
        raise DocumentError(
            "document_unsupported",
            f"不支持的文档类型：{suffix or '(无后缀)'}。本阶段只接受 Word/PDF（{list(DOC_SUFFIXES)}）。",
        )
    if not target.is_file():
        raise DocumentError("document_broken", f"文件不存在或不是普通文件：{target}")

    result = extract_docx(target) if suffix == ".docx" else extract_pdf(target)
    text = result["text"]
    chars = len(text.strip())
    if chars == 0:
        detail = "；".join(result["warnings"]) if result["warnings"] else "文件里没有文字内容"
        raise DocumentError(
            "document_no_text",
            f"提不出任何文本（{detail}）。按约定：文本长度为 0 **不算提取成功**。",
        )

    result.update({"chars": chars, "suffix": suffix, "filename": target.name})
    return result


# ════════════════════════════════════════════════════════════════════════
# ② 结构化摘要（抽取式：原句摘录 + 词频关键词 + 正则关键事实）
# ════════════════════════════════════════════════════════════════════════
# 句子切分：中英文标点的句末 + 换行。**不**在数字小数点处切（所以 . 后面要跟空白或结尾）
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|\n+")

# 关键词候选：英文单词/编号（≥3 字符）+ 中文 2 字组（没有分词库，用二元组近似）
_ENGLISH_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")

# 高频虚词/无信息词的二元组黑名单（词频法的噪音主要来自这里）
_STOP_BIGRAMS = {
    "我们", "你们", "他们", "这个", "那个", "这些", "那些", "可以", "需要", "进行", "以及",
    "一个", "没有", "如果", "因为", "所以", "但是", "并且", "而且", "或者", "不是", "就是",
    "通过", "对于", "关于", "由于", "为了", "已经", "正在", "将会", "可能", "应该", "必须",
    "以下", "如下", "上述", "其中", "包括", "其他", "另外", "同时", "此外", "根据", "按照",
    "报告", "数据", "内容", "问题", "情况", "工作", "方面", "部分", "相关", "进行", "目前",
    "什么", "怎么", "这样", "那样", "一些", "有些", "很多", "以上", "之下", "之后", "之前",
}
_ENGLISH_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have",
    "not", "you", "your", "our", "its", "can", "will", "shall", "may", "but", "all", "any",
    "page", "doc", "docx", "pdf",
}

# 关键事实：全部是**字面量正则**（抠出来的值一定在原文里一字不差地存在）
_FACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("money", re.compile(r"[¥￥$]\s?\d[\d,，]*(?:\.\d+)?|\d[\d,，]*(?:\.\d+)?\s*(?:元|万元|亿元|万|亿|美元)")),
    ("percent", re.compile(r"\d+(?:\.\d+)?\s*%")),
    ("date", re.compile(
        r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"
        r"|\d{4}\s*年\s*\d{1,2}\s*月"
        r"|\d{4}\s*[-/]\s*\d{1,2}\b"
    )),
    ("quantity", re.compile(r"\d[\d,，]*\s*(?:个|条|项|份|人|次|台|张|笔|家|行|列|页)")),
)
_FACT_CONTEXT = 18      # 上下文各取 18 字（够看清"这个数字是什么的"）


def split_sentences(text: str) -> list[str]:
    """切句：句末标点/换行处断开，丢掉过短的碎片（<4 字基本是表格里的孤立词）。"""
    return [sentence.strip() for sentence in _SENTENCE_SPLIT.split(text) if len(sentence.strip()) >= 4]


def _iter_keyword_terms(text: str):
    """产出关键词候选（带来源标记，便于解释"这个词是怎么来的"）。"""
    for match in _ENGLISH_WORD.finditer(text):
        word = match.group(0).lower()
        if word not in _ENGLISH_STOPWORDS and not word.isdigit():
            yield word, "word"
    for run in _CJK_RUN.finditer(text):
        chunk = run.group(0)
        # 2 字组滑窗（中文没有空格，二元组是"不引分词库"时最稳的近似）
        for index in range(len(chunk) - 1):
            bigram = chunk[index:index + 2]
            if bigram not in _STOP_BIGRAMS:
                yield bigram, "bigram"


def _keywords(text: str, limit: int) -> list[dict]:
    """词频法关键词：同一词出现 ≥2 次才算（只出现一次的多半是人名/噪音）。"""
    counts: dict[str, int] = {}
    for term, _kind in _iter_keyword_terms(text):
        counts[term] = counts.get(term, 0) + 1
    ranked = sorted(
        ((term, count) for term, count in counts.items() if count >= 2),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    # 去掉"被更高频词完全包含"的二元组（如"销售"已高频时不再单列"售额"）
    kept: list[dict] = []
    for term, count in ranked:
        if any(term in chosen["term"] and term != chosen["term"] for chosen in kept):
            continue
        kept.append({"term": term, "count": count})
        if len(kept) >= limit:
            break
    return kept


def _key_facts(text: str, limit: int) -> list[dict]:
    """正则抓"关键事实"：金额 / 百分比 / 日期 / 数量 —— 值本身是原文的字面量。

    同时给出 ±18 字的**原文上下文**，这样摘要里的数字能直接回原文核对。
    同一 (类型, 值) 只留第一次出现，避免"316,412.16"在文档里出现 10 次就刷屏。

    ⚠️ context 是 `text[start:end]` 的**原样切片**：不做空白压缩、不把换行换成空格。
    为"好看"而改写它，就等于让"可回原文核对"这句话变成假的 ——
    测试里那条 `context in text` 的断言就是为了钉死这一点（第一版就是在这里栽的）。
    """
    facts: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _FACT_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            if (kind, value) in seen:
                continue
            seen.add((kind, value))
            start = max(0, match.start() - _FACT_CONTEXT)
            end = min(len(text), match.end() + _FACT_CONTEXT)
            facts.append({
                "type": kind,
                "value": value,
                "context": text[start:end],
            })
            if len(facts) >= limit:
                return facts
    return facts


def _outline_from_text(text: str) -> list[str]:
    """没有样式信息时（PDF）按编号行猜大纲：第X章/1.2.3/一、二、 开头且不长的行。"""
    pattern = re.compile(
        r"^(?:第\s*[一二三四五六七八九十百零\d]+\s*[章节条部分]"
        r"|\d+(?:\.\d+){0,3}[、.．)）]"
        r"|[一二三四五六七八九十]+[、.．])"
    )
    lines = []
    for raw in text.splitlines():
        line = _clean(raw)
        if 0 < len(line) <= 60 and pattern.match(line):
            lines.append(line)
        if len(lines) >= 50:
            break
    return lines


def summarize(
    text: str,
    *,
    max_sentences: int = DEFAULT_MAX_SENTENCES,
    max_keywords: int = DEFAULT_MAX_KEYWORDS,
    max_facts: int = DEFAULT_MAX_FACTS,
) -> dict:
    """对一段文本做**抽取式**结构化摘要（不生成、不改写、不调用 LLM）。

    摘要怎么选：先算关键词，再按"句子里命中的关键词数量 + 长度是否适中"给每句打分，
    取分数最高的前 N 句，**按原文顺序**拼回去（不重排 = 读起来还是原文的叙述顺序）。
    """
    sentences = split_sentences(text)
    keywords = _keywords(text, max_keywords)
    facts = _key_facts(text, max_facts)
    keyword_set = {item["term"] for item in keywords}

    scored: list[tuple[int, int, str]] = []
    for position, sentence in enumerate(sentences):
        lowered = sentence.lower()
        hits = sum(1 for term in keyword_set if term in lowered)
        # 分数 = 关键词命中数×2 + 含数字奖励1（有数字的句子往往是结论句），再按长度轻微惩罚超长句
        score = hits * 2 + (1 if re.search(r"\d", sentence) else 0)
        if len(sentence) > 120:
            score -= 1
        scored.append((score, position, sentence))

    picked = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_sentences]
    picked_in_order = [sentence for _score, _position, sentence in sorted(picked, key=lambda item: item[1])]

    return {
        "method": SUMMARY_METHOD,
        "note": (
            "规则抽取（句子打分 + 词频关键词 + 正则关键事实），未使用 LLM、未使用向量库；"
            "摘要为**原文原句摘录**，未改写、未生成，可逐句回原文核对。"
        ),
        "chars": len(text),
        "sentence_count": len(sentences),
        "summary": "".join(sentence if sentence[-1] in "。！？!?；;." else sentence + "。" for sentence in picked_in_order),
        "sentences": picked_in_order,
        "keywords": keywords,
        "key_facts": facts,
        "fact_counts": {kind: sum(1 for fact in facts if fact["type"] == kind) for kind, _ in _FACT_PATTERNS},
    }


def summarize_document(extracted: dict, **kwargs: Any) -> dict:
    """在 extract() 的结果上做摘要，并把"摘要针对的是哪份文本"一并带上（可追溯）。"""
    result = summarize(extracted["text"], **kwargs)
    result.update({
        "doc_chars": extracted["chars"],
        "blocks": extracted["blocks"],
        "block_unit": extracted["block_unit"],
        "title": extracted.get("title", ""),
        "outline": extracted.get("outline", []),
        "warnings": extracted.get("warnings", []),
    })
    return result
