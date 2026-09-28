"""parsers.py · FR-003C：**一个入口，六种格式，统一 Dataset 抽象**。

════════════════════════════════════════════════════════════════════════
【入口与分流】
════════════════════════════════════════════════════════════════════════
    parse_file(path, suffix) → ParsedFile

    .xlsx / .xlsm / .csv  → 表格（**多 Sheet = 一个 dataset container 里的多个 table**，
                            不拆成多个独立数据源 —— 评审 §8⑥）
    .pptx                 → 正文 → document；表格 → dataset（**要过资格判定**）
    .md / .markdown       → 正文 → document；标准表格 → dataset
    .docx / .pdf          → document（复用既有 `app/engine/docs.py`，那个文件一个字没改）

**双落**：同一个文件允许同时产生 document + dataset（评审 #3 裁决：允许，且应该允许）。
两个落点共享 source_file_id，由 pipeline.py 负责关链。

════════════════════════════════════════════════════════════════════════
【安全：先验身，再解析（评审 §8⑨）】
════════════════════════════════════════════════════════════════════════
`filename.endswith(".pptx")` **不作为判据**。真正的闸门是 `verify_file()`：

    扩展名 → 文件签名/结构（ZIP 里的身份证文件 / PDF 魔数）→ ZIP 安全检查 → 解压大小限制

一个把 .zip 改名成 .pptx 的文件在这里被拒（找不到 `ppt/presentation.xml`），
一个 200 层嵌套、解压后 10GB 的 ZIP 炸弹也在这里被拒（条目数 / 总解压量 / 压缩比三闸）。

════════════════════════════════════════════════════════════════════════
【★ PPT 表格资格判定（评审 §8⑤，这条最容易做错）】
════════════════════════════════════════════════════════════════════════
**看到 PPT 有表格就自动当销售数据是错的** —— PPT 里的表格可能是项目计划、人员名单、
会议议程。所以 PPTX 的表格先进入候选，但必须过 `_table_qualifies()`：

    ① 形状可用：≥ 2 列、≥ 2 数据行、表头非空且不重复
    ② **字段能被现有识别逻辑认出来**：`registry.guess_mapping()` 认出至少一个语义字段，
       或者命中 `regions.classify_column()` 的地区维度

过不了 ② 的表格**只作为文档内容**（它的文字仍然在 document 里，信息不丢），
不强行生成一个假的"数据集"。

────────────────────────────────────────────────────────────────────────
【Markdown 表格：规范解析，不许"正则随便扫"（评审 §8④）】
────────────────────────────────────────────────────────────────────────
认的形态（标准 Markdown 表格）：

    | 地区 | 销售额 |
    |---|---:|
    | 华东 | 100 |

逐条落地的规则：
    · 表头行 —— 必须是以 `|` 开头/结尾的行
    · 分隔行 —— **表头下一行必须是分隔行**（`|:--|--:|` 这类）；没有它就**不是表格**，
      当作普通正文（这是"非标准 Markdown 表格"的判定）
    · 列数一致性 —— 某行多出/少了单元格：补齐或截断，并**记一条 note**（不静默改数据）
    · 空表 —— 只有表头 + 分隔行、没有数据行 → 不成表（0 行的"数据集"没有意义）
    · 多张表 —— 各自成一张 table（md_table_1 / md_table_2…），**一个文件仍是一个 dataset**
    · 表格前后正文 —— 全文仍进 document，但**表格块会从正文里摘掉**（正文就是正文，
      表格已经物化成数据集了，不必在文档里再糊一遍管道符）
"""

from __future__ import annotations

import csv as _csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.engine import docs as engine_docs
from app.importer.models import (
    FMT_CSV,
    FMT_DOCX,
    FMT_EXCEL,
    FMT_MARKDOWN,
    FMT_PDF,
    FMT_PPTX,
    ImporterError,
    MAX_DOCUMENT_CHARS,
    MAX_FILE_BYTES,
    MAX_MARKDOWN_CHARS,
    MAX_PPTX_SLIDES,
    MAX_TABLE_ROWS,
    MAX_TABLES_PER_FILE,
    OLE_MAGIC,
    PDF_MAGIC,
    SOURCE_TYPES,
    ZIP_ALLOWED_MEMBER_SUFFIXES,
    ZIP_MAGIC,
    ZIP_MAGIC_ALT,
    ZIP_MAX_COMPRESSION_RATIO,
    ZIP_MAX_ENTRIES,
    ZIP_MAX_UNCOMPRESSED_BYTES,
    ZIP_ROOTS,
    ZIP_SIGNATURES,
    source_type_of,
)

#: 表格的稳定标识前缀（md_table_1 / pptx_table_2 / sheet_1）
TABLE_MD = "md_table"
TABLE_PPTX = "pptx_table"
TABLE_SHEET = "sheet"

#: CSV 编码尝试顺序（与 `registry.CSV_ENCODINGS` 同一套，含中文 GBK）
CSV_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gbk", "latin-1")


# ════════════════════════════════════════════════════════════════════════
# 解析结果（唯一的数据形状 —— 六个解析器都往这里收敛）
# ════════════════════════════════════════════════════════════════════════
@dataclass
class ParsedTable:
    """一张表：稳定标识 + 列 + 行（值尚未规范化，规范化在 normalize.py）。"""

    name: str                       # 稳定标识：Sheet 名 / md_table_1 / pptx_table_2
    display_name: str               # 给人看的名字（"工作表：Sheet1" / "第 3 张幻灯片的表格"）
    columns: list[str]
    rows: list[list[Any]]
    source_ref: str = ""            # 出处（幻灯片序号 / 工作表名 / 第几张 Markdown 表）
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedFile:
    """一份文件的解析结果（正文 + 若干表 + 说明）。"""

    source_type: str
    document_text: str = ""
    document_title: str = ""
    document_meta: dict[str, Any] = field(default_factory=dict)
    tables: list[ParsedTable] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_document(self) -> bool:
        return bool(self.document_text.strip())

    @property
    def has_tables(self) -> bool:
        return bool(self.tables)


# ════════════════════════════════════════════════════════════════════════
# ① 安全闸门：验身（扩展名 + 签名 + ZIP 结构）
# ════════════════════════════════════════════════════════════════════════
def _read_head(path: Path, size: int = 8) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


def _looks_encrypted_ole(head: bytes) -> bool:
    """被密码保护的 OOXML 会变成 OLE 复合文档（`D0 CF 11 E0`）—— 直接拒，不猜密码。"""
    return head.startswith(OLE_MAGIC)


def _member_suffix(name: str) -> str:
    """压缩包成员的扩展名（含"点开头"的名字，如 `_rels/.rels`）。

    ★ 为什么不能用 `Path(name).suffix`：Python 把**点开头的名字**当作纯文件名，
      `.rels` 的 suffix 是空串 —— OOXML 包里 `_rels/.rels` 是标准成员，
      用 Path.suffix 判会把每一份正常的 Office 文件都判成"成员类型不合法"。
    """
    base = name.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot >= 0 and len(base) > 1 else ""


def check_zip_safety(path: Path) -> dict[str, Any]:
    """ZIP 安全检查（PPTX/DOCX/XLSX 共用）：条目数 / 解压总量 / 压缩比 / 成员路径。

    返回一份"签"（条目数、解压总量），供调用方记进导入说明里。
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ImporterError("parse_failed", f"这个文件不是有效的压缩包（可能已损坏）：{exc}") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > ZIP_MAX_ENTRIES:
            raise ImporterError(
                "zip_unsafe",
                f"压缩包里有 {len(infos)} 个成员，超过 {ZIP_MAX_ENTRIES} 个上限 —— 拒收。",
            )
        total_uncompressed = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ImporterError("zip_unsafe", f"压缩包成员路径不合法（疑似路径穿越）：{name}")
            if info.is_dir():
                continue
            suffix = _member_suffix(name)
            if suffix not in ZIP_ALLOWED_MEMBER_SUFFIXES:
                raise ImporterError(
                    "zip_unsafe", f"压缩包里出现了不该有的成员类型：{name}"
                )
            total_uncompressed += int(info.file_size)
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > ZIP_MAX_COMPRESSION_RATIO and info.file_size > 1024 * 1024:
                    raise ImporterError(
                        "zip_bomb",
                        f"压缩包成员 {name} 的压缩比 {ratio:.0f}:1 异常（疑似 ZIP 炸弹）—— 拒收。",
                    )
            if total_uncompressed > ZIP_MAX_UNCOMPRESSED_BYTES:
                raise ImporterError(
                    "zip_bomb",
                    f"压缩包解压后超过 {ZIP_MAX_UNCOMPRESSED_BYTES // 1048576}MB 上限 —— 拒收。",
                )
        names = [info.filename.replace("\\", "/") for info in infos]
    return {"entries": len(infos), "uncompressed_bytes": total_uncompressed, "names": names}


def verify_file(path: Path, suffix: str) -> dict[str, Any]:
    """**解析之前的第一道闸门**：扩展名 → 文件签名/结构 → ZIP 安全检查。

    为什么不能只看扩展名（评审 §8⑨ 原文）：PPTX 本质是 OOXML ZIP 包，"支持 PPTX"
    等于"支持解析任意 ZIP"。只看后缀的话，一个 200 层嵌套的炸弹会被当成演示文稿送进解析器。
    """
    suffix = str(suffix).lower()
    spec = SOURCE_TYPES.get(suffix)
    if spec is None:
        raise ImporterError(
            "unsupported_type",
            f"不支持的文件类型：{suffix or '(无后缀)'}。"
            f"支持的是 {sorted(SOURCE_TYPES)}（Excel/CSV/Markdown/PPT/Word/PDF）。",
        )
    source_type = str(spec["source_type"])

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ImporterError("parse_failed", f"读不到这个文件：{exc}") from exc
    if size <= 0:
        raise ImporterError("parse_failed", "这是个空文件（0 字节），没有可导入的内容。")
    if size > MAX_FILE_BYTES:
        raise ImporterError(
            "file_too_large",
            f"文件 {size / 1048576:.1f}MB 超过 {MAX_FILE_BYTES // 1048576}MB 上限 —— 请先拆分或抽样。",
        )

    head = _read_head(path, 8)
    if _looks_encrypted_ole(head):
        raise ImporterError(
            "encrypted_archive",
            "这个 Office 文件带密码保护 —— 请先去掉密码再导入（系统不去猜密码）。",
        )

    if source_type == FMT_PDF:
        if not head.startswith(PDF_MAGIC):
            raise ImporterError(
                "signature_mismatch",
                "后缀是 .pdf，但文件内容不是 PDF（魔数不是 %PDF-）—— 拒收。",
            )
        return {"source_type": source_type, "signature": "pdf"}

    if source_type in (FMT_EXCEL, FMT_PPTX, FMT_DOCX):
        if not (head.startswith(ZIP_MAGIC) or head.startswith(ZIP_MAGIC_ALT)):
            raise ImporterError(
                "signature_mismatch",
                f"后缀是 {suffix}，但文件内容不是 Office 文档（不是 ZIP 包）—— 拒收。",
            )
        report = check_zip_safety(path)
        for signature in ZIP_SIGNATURES[source_type]:
            if signature in report["names"]:
                return {**report, "source_type": source_type, "signature": signature}
        raise ImporterError(
            "signature_mismatch",
            f"这个文件虽然是个压缩包，但里面没有 {ZIP_SIGNATURES[source_type]} —— "
            f"它不是一个真正的 {source_type} 文件。拒收（只改扩展名不算数）。",
        )

    # CSV / Markdown：文本格式，验"是不是二进制乱码"
    with open(path, "rb") as handle:
        sample = handle.read(8192)
    if b"\x00" in sample:
        raise ImporterError(
            "signature_mismatch",
            f"后缀是 {suffix}，但文件内容含二进制空字节 —— 它不是文本文件。拒收。",
        )
    return {"source_type": source_type, "signature": "text"}


# ════════════════════════════════════════════════════════════════════════
# ② 表格通用工具（四种来源共用）
# ════════════════════════════════════════════════════════════════════════
def _clean_columns(raw: Iterable[Any]) -> list[str]:
    """表头整理：空单元格给个占位名（"列N"），不丢列。"""
    columns: list[str] = []
    for index, cell in enumerate(raw):
        text = "" if cell is None else str(cell).strip()
        if isinstance(cell, float) and cell != cell:
            text = ""
        columns.append(text or f"列{index + 1}")
    return columns


def _rows_of(values: Any) -> list[list[Any]]:
    """把二维容器整理成"行的列表"（None 原样留着，规范化那一层再处理）。"""
    rows: list[list[Any]] = []
    for row in values:
        cells = list(row)
        if all(cell is None or (isinstance(cell, float) and cell != cell) for cell in cells):
            continue                       # 整行空 → 丢掉（Excel 尾部常见）
        rows.append(cells)
    return rows


def _check_table_limits(table: ParsedTable) -> ParsedTable:
    """行数闸门：超了**明确拒绝**，绝不悄悄截断（截断出来的"报表"比没有更危险）。"""
    if len(table.rows) > MAX_TABLE_ROWS:
        raise ImporterError(
            "too_many_rows",
            f"「{table.display_name}」有 {len(table.rows):,} 行，超过单表 {MAX_TABLE_ROWS:,} 行上限 —— "
            f"请先在源文件里筛选或拆分后再导入。",
        )
    return table


def _check_table_count(tables: list[ParsedTable]) -> None:
    if len(tables) > MAX_TABLES_PER_FILE:
        raise ImporterError(
            "too_many_tables",
            f"这个文件里有 {len(tables)} 张表，超过 {MAX_TABLES_PER_FILE} 张上限 —— 请拆分后再导入。",
        )


# ════════════════════════════════════════════════════════════════════════
# ③ Excel / CSV
# ════════════════════════════════════════════════════════════════════════
def _parse_excel(path: Path) -> ParsedFile:
    """**多 Sheet → 一个 dataset 里的多张表**（评审 §8⑥：不拆成多个独立数据源）。

    ★ `pd.ExcelFile` 一定要用 `with` 关掉：它持有文件的打开句柄，不关的话在 Windows 上
      这份文件删不掉 / 改名失败 —— 而导入成功后我们**要**把输入文件消费掉（已物化的语义），
      句柄没收就会变成"导入说成功了，文件却还在原地"，与 FR-003B 的承诺不符。
    """
    import pandas as pd

    parsed = ParsedFile(source_type=FMT_EXCEL)
    with pd.ExcelFile(path, engine="openpyxl") as excel:
        return _read_excel_sheets(excel, parsed)


def _read_excel_sheets(excel: Any, parsed: ParsedFile) -> ParsedFile:
    """逐个工作表读成表（**ExcelFile 必须用完就关** —— 见下）。"""
    sheet_names = [str(name) for name in excel.sheet_names]
    parsed.notes.append(f"工作簿共 {len(sheet_names)} 个工作表：{'、'.join(sheet_names)}")
    for ordinal, sheet in enumerate(sheet_names, start=1):
        frame = excel.parse(sheet, header=0)
        if frame.shape[1] == 0:
            parsed.notes.append(f"工作表「{sheet}」是空的，跳过。")
            continue
        columns = _clean_columns(frame.columns)
        rows = _rows_of(frame.itertuples(index=False, name=None))
        if not rows:
            parsed.notes.append(f"工作表「{sheet}」没有数据行，跳过。")
            continue
        parsed.tables.append(_check_table_limits(ParsedTable(
            name=f"{TABLE_SHEET}{ordinal}",
            display_name=f"工作表「{sheet}」",
            columns=columns,
            rows=rows,
            source_ref=f"工作表 {sheet}",
        )))
    _check_table_count(parsed.tables)
    return parsed


def _parse_csv(path: Path) -> ParsedFile:
    """CSV：按 UTF-8 → GBK → Latin-1 依次尝试，并**说明实际用的是哪种编码**。"""
    parsed = ParsedFile(source_type=FMT_CSV)
    last_error: Exception | None = None
    text: str | None = None
    used_encoding = ""
    for encoding in CSV_ENCODINGS:
        try:
            text = path.read_text(encoding=encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except OSError as exc:
            raise ImporterError("parse_failed", f"读不到这个 CSV：{exc}") from exc
    if text is None:
        raise ImporterError(
            "encoding_unknown",
            f"这个 CSV 的编码认不出来（试过 {list(CSV_ENCODINGS)}）。请另存为 UTF-8 后重试。"
            f"原始错误：{last_error}",
        )
    parsed.notes.append(f"CSV 按 {used_encoding} 读取")

    reader = _csv.reader(io.StringIO(text))
    raw_rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not raw_rows:
        raise ImporterError("no_content", "这个 CSV 里没有任何数据行。")
    columns = _clean_columns(raw_rows[0])
    rows = _rows_of(raw_rows[1:])
    if not rows:
        raise ImporterError("no_content", "这个 CSV 只有表头，没有数据行。")
    parsed.tables.append(_check_table_limits(ParsedTable(
        name="sheet1", display_name="CSV 表格", columns=columns, rows=rows, source_ref="CSV 文件",
    )))
    return parsed


# ════════════════════════════════════════════════════════════════════════
# ④ Markdown（正文 + 标准表格）
# ════════════════════════════════════════════════════════════════════════
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_DELIMITER = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _split_md_row(line: str) -> list[str]:
    """一行 Markdown 表格 → 单元格文本（去掉首尾的管道符，含反斜杠转义的 | 先还原）。"""
    text = line.strip()
    text = text[1:] if text.startswith("|") else text
    text = text[:-1] if text.endswith("|") else text
    cells: list[str] = []
    current = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            current += "|"
            index += 2
            continue
        if char == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += char
        index += 1
    cells.append(current.strip())
    return cells


def _is_md_row(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def parse_markdown_text(text: str) -> tuple[str, list[ParsedTable], list[str]]:
    """Markdown → `(表格外的正文, 表格列表, 说明)`。

    ★ 表格**必须**有分隔行才算表格（`| 地区 | 销售额 |` 后面那行 `|---|---:|`）。
      没有分隔行的"管道符乱写"只当正文 —— 这是"非标准 Markdown 表格"的判定口径。
    """
    lines = text.splitlines()
    body: list[str] = []
    tables: list[ParsedTable] = []
    notes: list[str] = []
    index = 0
    in_fence = False
    while index < len(lines):
        line = lines[index]
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            body.append(line)
            index += 1
            continue
        # 候选表格：当前行是表头、下一行是分隔行（且都不在代码围栏里）
        if (
            not in_fence
            and _is_md_row(line)
            and index + 1 < len(lines)
            and _is_md_row(lines[index + 1])
            and _MD_DELIMITER.match(lines[index + 1])
        ):
            header = _split_md_row(line)
            data_rows: list[list[str]] = []
            cursor = index + 2
            mismatched = 0
            while cursor < len(lines) and _is_md_row(lines[cursor]):
                cells = _split_md_row(lines[cursor])
                if len(cells) != len(header):
                    mismatched += 1
                if len(cells) < len(header):
                    cells = cells + [""] * (len(header) - len(cells))
                data_rows.append(cells[: len(header)])
                cursor += 1
            if data_rows:
                ordinal = len(tables) + 1
                table_notes: list[str] = []
                if mismatched:
                    table_notes.append(
                        f"有 {mismatched} 行的单元格数与表头不一致（已按表头列数补齐/截断）"
                    )
                tables.append(ParsedTable(
                    name=f"{TABLE_MD}_{ordinal}",
                    display_name=f"第 {ordinal} 张 Markdown 表格",
                    columns=_clean_columns(header),
                    rows=data_rows,
                    source_ref=f"Markdown 第 {index + 1} 行起",
                    notes=table_notes,
                ))
                notes.append(f"第 {ordinal} 张表格：{len(data_rows)} 行 × {len(header)} 列")
            else:
                notes.append(f"第 {len(tables) + 1} 处表格只有表头没有数据行，未成表。")
            index = cursor
            continue
        body.append(line)
        index += 1

    plain = "\n".join(body).strip()
    return plain, tables, notes


def _parse_markdown(path: Path) -> ParsedFile:
    parsed = ParsedFile(source_type=FMT_MARKDOWN)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="gbk")
            parsed.notes.append("Markdown 按 GBK 读取（不是 UTF-8）")
        except UnicodeDecodeError as exc:
            raise ImporterError(
                "encoding_unknown", f"这个 Markdown 的编码认不出来（UTF-8 与 GBK 都失败）：{exc}"
            ) from exc
    if len(text) > MAX_MARKDOWN_CHARS:
        raise ImporterError(
            "markdown_too_large",
            f"Markdown 有 {len(text):,} 字符，超过 {MAX_MARKDOWN_CHARS:,} 上限 —— 请拆分后再导入。",
        )
    plain, tables, notes = parse_markdown_text(text)
    parsed.notes.extend(notes)
    parsed.document_text = plain
    parsed.tables = tables[:MAX_TABLES_PER_FILE]
    _check_table_count(tables)
    for table in parsed.tables:
        _check_table_limits(table)
    return parsed


# ════════════════════════════════════════════════════════════════════════
# ⑤ PPTX（正文 + 表格，表格要过资格判定）
# ════════════════════════════════════════════════════════════════════════
def _parse_pptx(path: Path) -> ParsedFile:
    """python-pptx 解析：正文 → 文档；表格 → 候选（再过资格判定）。"""
    from pptx import Presentation                     # 只在真正要解析 pptx 时才 import

    parsed = ParsedFile(source_type=FMT_PPTX)
    try:
        presentation = Presentation(str(path))
    except Exception as exc:                          # noqa: BLE001 —— 库的异常类型很杂
        raise ImporterError("parse_failed", f"这个 PPT 打不开（可能已损坏）：{exc}") from exc

    slides = list(presentation.slides)
    if len(slides) > MAX_PPTX_SLIDES:
        raise ImporterError(
            "too_many_slides",
            f"这个 PPT 有 {len(slides)} 页，超过 {MAX_PPTX_SLIDES} 页上限 —— 请拆分后再导入。",
        )
    parsed.notes.append(f"演示文稿共 {len(slides)} 页")

    text_blocks: list[str] = []
    skipped_tables: list[str] = []
    for slide_index, slide in enumerate(slides, start=1):
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                content = str(shape.text_frame.text or "").strip()
                if content:
                    slide_texts.append(content)
            if getattr(shape, "has_table", False):
                table = _pptx_table_to_parsed(shape.table, slide_index, len(parsed.tables) + 1)
                if table is None:
                    continue
                qualifies, why = _table_qualifies(table)
                if qualifies:
                    parsed.tables.append(_check_table_limits(table))
                else:
                    skipped_tables.append(f"第 {slide_index} 页的表格（{why}）")
        if slide_texts:
            text_blocks.append(f"## 第 {slide_index} 页\n\n" + "\n\n".join(slide_texts))

    parsed.document_text = "\n\n".join(text_blocks)
    for note in skipped_tables:
        parsed.notes.append(f"{note} 不像业务数据表，只作为文档内容保留（未生成数据集）")
    _check_table_count(parsed.tables)
    return parsed


def _pptx_table_to_parsed(table: Any, slide_index: int, ordinal: int) -> ParsedTable | None:
    """一张 PowerPoint 表格 → ParsedTable（第一行当表头）。空表返回 None。"""
    rows: list[list[Any]] = []
    for row in table.rows:
        cells: list[Any] = []
        for cell in row.cells:
            cells.append(str(cell.text or "").strip())
        rows.append(cells)
    if len(rows) < 2:
        return None                                   # 只有表头（或全空）—— 不成表
    columns = _clean_columns(rows[0])
    data_rows = _rows_of(rows[1:])
    if not data_rows:
        return None
    return ParsedTable(
        name=f"{TABLE_PPTX}_{ordinal}",
        display_name=f"第 {slide_index} 页的表格",
        columns=columns,
        rows=data_rows,
        source_ref=f"第 {slide_index} 页",
    )


def _table_qualifies(table: ParsedTable) -> tuple[bool, str]:
    """★ PPT 表格资格判定（评审 §8⑤）—— 返回 `(是否合格, 不合格的原因)`。

    两道闸门（都要过）：
      ① **形状**：≥2 列、≥2 数据行、表头非空且不重复
      ② **内容像数据**（下面 a/b/c 任一成立）：
           a) `guess_mapping()` 认出 **≥2 个**语义字段
           b) 有 ≥1 个地区维度列 **且** 有 ≥1 个数值列
           c) 有 ≥1 个语义字段 **且** 有 ≥1 个数值列

    ★ 为什么不能只写"认出一个字段就算数"：`guess_mapping` 的候选里有「时间」「日期」
      这类**通用词**，会议议程表的表头正是「时间 / 事项」—— 一个词命中就放行，
      整份议程就会被登记成"销售数据源"。加上"还得有一个数值列（或两个字段）"之后：
        · 会议议程（时间/事项）  → 0 个数值列、1 个字段        → 拒（正是我们要的）
        · 项目计划（任务/负责人/开始时间/工时）→ 0 个字段      → 拒
        · 人员名单（姓名/部门/入职日期/电话）  → 0 个字段      → 拒
        · 真实销售表（省份/销售额、商品/数量…）→ 地区+数值 或 字段+数值 → 放行
      宁可少落一个数据集（信息仍在 document 里，不会丢），也不能造一个假的"数据源"。
    """
    if len(table.columns) < 2:
        return False, "列数少于 2 列"
    if len(table.rows) < 2:
        return False, "数据行少于 2 行"
    header = [str(column).strip() for column in table.columns]
    named = [column for column in header if column and not column.startswith("列")]
    if len(named) < 2:
        return False, "表头基本是空的"
    if len(set(named)) != len(named):
        return False, "表头有重复名字"

    from app.datasets import registry as dataset_registry

    fields = dataset_registry.guess_mapping(header)
    region_hit = _region_hit_in(header, table)
    numeric_hit = _numeric_hit_in(header, table)

    if len(fields) >= 2:
        return True, f"识别出 {len(fields)} 个业务字段"
    if region_hit and numeric_hit:
        return True, "含地区字段与数值列"
    if fields and numeric_hit:
        return True, f"识别出业务字段（{sorted(fields)}）且有数值列"
    return False, "既没有成组的业务字段，也没有「字段 + 数值列」的组合"


def _profiles_of(header: list[str], table: ParsedTable) -> dict[str, dict[str, Any]]:
    """按表头取列画像（判定用；规范化失败就当判不出来，不让它把导入搞崩）。"""
    from app.importer import normalize as normalize_module

    try:
        _names, _rows, profiles = normalize_module.normalize_table(header, table.rows)
    except Exception:                                  # noqa: BLE001 —— 判定失败不该让导入炸
        return {}
    return {str(profile["name"]): dict(profile) for profile in profiles}


def _region_hit_in(header: list[str], table: ParsedTable) -> bool:
    """表头里有没有地区维度候选（值形态也要过 —— 复用同一套判定，不另写一份）。"""
    from app.importer import regions as regions_module

    by_name = _profiles_of(header, table)
    return any(
        regions_module.classify_column(by_name[column]) is not None
        for column in header if column in by_name
    )


def _numeric_hit_in(header: list[str], table: ParsedTable) -> bool:
    """表里有没有**真正的数值列**（整数/小数；日期与文本不算）。"""
    by_name = _profiles_of(header, table)
    return any(
        str(profile.get("inferred_type")) in ("integer", "number")
        for profile in by_name.values()
    )


# ════════════════════════════════════════════════════════════════════════
# ⑥ Word / PDF（复用既有提取能力，不重写）
# ════════════════════════════════════════════════════════════════════════
def _parse_document(path: Path, source_type: str, suffix: str) -> ParsedFile:
    """Word/PDF → 正文（调既有的 `app/engine/docs.py`，那个文件一个字没改）。"""
    parsed = ParsedFile(source_type=source_type)
    try:
        extracted = engine_docs.extract(path, suffix)
    except engine_docs.DocumentError as exc:
        raise ImporterError(exc.code, exc.message) from exc
    parsed.document_text = str(extracted.get("text") or "")
    parsed.document_title = str(extracted.get("title") or "")
    parsed.document_meta = {
        "chars": int(extracted.get("chars") or 0),
        "blocks": int(extracted.get("blocks") or 0),
        "block_unit": extracted.get("block_unit") or "",
        "outline": list(extracted.get("outline") or []),
        "warnings": list(extracted.get("warnings") or []),
        "extra": dict(extracted.get("extra") or {}),
    }
    if not parsed.has_document:
        raise ImporterError(
            "no_content",
            "这份文档提不出任何文字（扫描件或空文档）—— 按约定不算导入成功。",
        )
    return parsed


# ════════════════════════════════════════════════════════════════════════
# ⑦ 统一入口
# ════════════════════════════════════════════════════════════════════════
def parse_file(path: str | Path, suffix: str | None = None, *, verify: bool = True) -> ParsedFile:
    """**唯一入口**：验身 → 按格式分流 → 收敛成同一个 ParsedFile。

    `suffix` 不传就从路径取。调用方（pipeline.py）负责幂等、落库与错误登记；
    这里只负责"把文件读成正文 + 表"，读不出来就抛 `ImporterError`。

    `verify=False` 只给 pipeline.py 用：它在调本函数**之前**已经自己验过一次身，
    那次验身的结果决定了"失败要不要留案底"（见 pipeline.py 的三道顺序），不能白验两遍。
    单独调用本函数时（测试、脚本）保持默认 True —— 验身永远是解析的前置条件。
    """
    target = Path(path)
    actual_suffix = str(suffix if suffix is not None else target.suffix).lower()
    if verify:
        verify_file(target, actual_suffix)             # ★ 先验身，再解析（顺序不许换）
    source_type = source_type_of(actual_suffix)

    if source_type == FMT_EXCEL:
        parsed = _parse_excel(target)
    elif source_type == FMT_CSV:
        parsed = _parse_csv(target)
    elif source_type == FMT_MARKDOWN:
        parsed = _parse_markdown(target)
    elif source_type == FMT_PPTX:
        parsed = _parse_pptx(target)
    elif source_type in (FMT_DOCX, FMT_PDF):
        parsed = _parse_document(target, source_type, actual_suffix)
    else:                                              # pragma: no cover —— verify_file 已经拦过
        raise ImporterError("unsupported_type", f"不支持的文件类型：{actual_suffix}")

    if not parsed.has_document and not parsed.has_tables:
        raise ImporterError(
            "no_content",
            "这份文件里没有可导入的内容（既没有正文，也没有可用的表格）。",
        )
    if len(parsed.document_text) > MAX_DOCUMENT_CHARS:
        parsed.document_text = parsed.document_text[:MAX_DOCUMENT_CHARS]
        parsed.notes.append(f"正文超过 {MAX_DOCUMENT_CHARS:,} 字符，已截断入库")
    return parsed


__all__ = [
    "CSV_ENCODINGS",
    "ParsedFile",
    "ParsedTable",
    "TABLE_MD",
    "TABLE_PPTX",
    "TABLE_SHEET",
    "check_zip_safety",
    "parse_file",
    "parse_markdown_text",
    "verify_file",
]
