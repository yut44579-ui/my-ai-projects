"""models.py · FR-003A：统一 Import / Source 模型的**常量与形状定义**（一处定义，别处引用）。

════════════════════════════════════════════════════════════════════════
【这一层解决什么问题】
════════════════════════════════════════════════════════════════════════
在 FR-003 之前，项目里有**两条互不知道对方存在的输入管道**：

    /api/datasets/inspect + /import   只收 .xlsx/.xlsm/.csv → 登记成 dataset（**引用**原文件）
    /api/documents                    只收 .docx/.pdf       → 提取出 document

于是"这个数字来自哪个文件"这个问题**答不出来**：dataset 记自己的哈希，document 记自己的哈希，
同一个 .pptx 里的正文与表格各落一方，两边没有共同的来源标识。

FR-003A 增加一层**三层来源链路**（评审 #10 点名）：

    source_file  ── 一份**文件**（一个 sha256 就是一份文件，与它被解析出什么无关）
        │
        ├── import ── 一次**导入动作**（哪个文件、哪版解析器、成功还是失败、落了什么）
        │       ├── dataset_id     （结构化数据，可为空）
        │       └── document_id    （正文资料，可为空）
        └── …

★ 双落（同一个 .pptx 既有正文又有表格）时，document 与 dataset **共享同一个 source_file_id**
  —— 这是评审 #3 的硬要求，也是 UI 能说清"销售分析.pptx → 已导入 1 个文档资料 + 2 个数据集"
  的唯一依据（而不是笼统一句"导入成功"）。

════════════════════════════════════════════════════════════════════════
【本文件里放什么、不放什么】
════════════════════════════════════════════════════════════════════════
放：① 解析器版本号（幂等依据的一半）；② 后缀 → 格式 → 落成什么 的**唯一映射表**；
    ③ 安全限额（单文件/行数/PPT 页数/Markdown 字符数/库容量/ZIP 安全检查参数）；
    ④ 机器可读的错误码与人话（`ImporterError`）。
不放：任何 IO、任何 SQL、任何 pandas —— 那些在 db.py / store.py / parsers.py 里。
**它是全项目关于"导入"这件事的唯一定义处**（限额改一个数、格式加一种，只动这一个文件）。
"""

from __future__ import annotations

from typing import Any

# ════════════════════════════════════════════════════════════════════════
# ① 解析器版本 —— 导入幂等依据的另一半
# ════════════════════════════════════════════════════════════════════════
# 幂等规则（评审 §8①，施工指令 FR-003B 原文）：
#     相同 sha256 + 相同 parser_version → 幂等，不重复入库（返回既有 import_id）
#     相同 sha256 + parser_version 变了 → 允许重解析，产生**新的 import revision**
#
# ★ 所以：**解析逻辑一改，这个数字必须 +1**。否则老文件会被"幂等"掉，
#   用户拿着新期望去查旧结果，而系统坚称"这个文件我导过了" —— 那是无声的错。
PARSER_VERSION = "1"

# ════════════════════════════════════════════════════════════════════════
# ② 后缀 → 格式 → 落成什么（**唯一映射表**）
# ════════════════════════════════════════════════════════════════════════
# source_type：给人和审计看的格式名（进 imports.source_type 列）
FMT_EXCEL = "excel"
FMT_CSV = "csv"
FMT_MARKDOWN = "markdown"
FMT_PPTX = "pptx"
FMT_DOCX = "docx"
FMT_PDF = "pdf"

# 落点角色：这份格式"有没有可能"产出正文资料 / 结构化数据集
#   ★ 注意措辞是"有没有可能"：pptx/markdown **必须有内容才落**（见 parsers.py 的资格判定），
#     不是看到后缀就登记一条空记录。
ROLE_DOCUMENT = "document"
ROLE_DATASET = "dataset"

SOURCE_TYPES: dict[str, dict[str, Any]] = {
    ".xlsx": {"source_type": FMT_EXCEL, "roles": (ROLE_DATASET,), "label": "Excel 工作簿"},
    ".xlsm": {"source_type": FMT_EXCEL, "roles": (ROLE_DATASET,), "label": "Excel 工作簿（含宏）"},
    ".csv": {"source_type": FMT_CSV, "roles": (ROLE_DATASET,), "label": "CSV 表格"},
    ".md": {"source_type": FMT_MARKDOWN, "roles": (ROLE_DOCUMENT, ROLE_DATASET), "label": "Markdown"},
    ".markdown": {"source_type": FMT_MARKDOWN, "roles": (ROLE_DOCUMENT, ROLE_DATASET), "label": "Markdown"},
    ".pptx": {"source_type": FMT_PPTX, "roles": (ROLE_DOCUMENT, ROLE_DATASET), "label": "PowerPoint"},
    ".docx": {"source_type": FMT_DOCX, "roles": (ROLE_DOCUMENT,), "label": "Word 文档"},
    ".pdf": {"source_type": FMT_PDF, "roles": (ROLE_DOCUMENT,), "label": "PDF 文档"},
}

#: 允许导入的全部后缀（`app/api_imports.py` 只认这张表里的）
ALLOWED_SUFFIXES: tuple[str, ...] = tuple(SOURCE_TYPES)

#: 会产出**结构化数据集**的后缀
DATASET_SUFFIXES: tuple[str, ...] = tuple(
    suffix for suffix, spec in SOURCE_TYPES.items() if ROLE_DATASET in spec["roles"]
)

#: 会产出**正文资料**的后缀
DOCUMENT_SUFFIXES: tuple[str, ...] = tuple(
    suffix for suffix, spec in SOURCE_TYPES.items() if ROLE_DOCUMENT in spec["roles"]
)

#: 扩展名 → 中文格式名（错误话术里用，别让用户读 ".xlsx" 这种字符串）
SUFFIX_LABELS: dict[str, str] = {suffix: spec["label"] for suffix, spec in SOURCE_TYPES.items()}


def roles_of(suffix: str) -> tuple[str, ...]:
    """这个后缀**可能**落成什么（不支持的返回空元组，由调用方报错）。"""
    spec = SOURCE_TYPES.get(str(suffix).lower())
    return tuple(spec["roles"]) if spec else ()


def source_type_of(suffix: str) -> str | None:
    """这个后缀的格式名（不认识返回 None）。"""
    spec = SOURCE_TYPES.get(str(suffix).lower())
    return str(spec["source_type"]) if spec else None


# ════════════════════════════════════════════════════════════════════════
# ③ 安全限额（评审 §8⑧⑨：导入是**文件解析攻击面**，必须有闸门）
# ════════════════════════════════════════════════════════════════════════
MAX_FILE_BYTES = 80 * 1024 * 1024           # 单文件 80MB（与既有 /api/datasets/inspect 同一档）
MAX_TABLE_ROWS = 200_000                    # 单个表的数据行上限（物化入库；超了明确拒绝）
MAX_TABLES_PER_FILE = 50                    # 一个文件最多落多少个表（多 Sheet / 多张 Markdown 表）
MAX_MARKDOWN_CHARS = 2_000_000              # Markdown 正文字符数上限
MAX_DOCUMENT_CHARS = 2_000_000              # 落库正文的字符数上限（PPT/Word/PDF/MD 共用）
MAX_PPTX_SLIDES = 300                       # PPT 页数上限
MAX_DB_BYTES = 512 * 1024 * 1024            # SQLite 库总容量上限（超了明确拒绝，不悄悄写爆磁盘）

# ── ZIP 安全检查（PPTX / DOCX / XLSX 本质都是 OOXML ZIP 包）───────────────
# 只检查扩展名 = 把"支持 PPTX"变成"支持任意 ZIP"。下面这几条一起构成 ZIP 闸门：
ZIP_MAX_ENTRIES = 4_000                     # 条目数上限（炸弹通常靠海量小条目）
ZIP_MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024   # 解压后总大小上限
ZIP_MAX_COMPRESSION_RATIO = 200             # 单条目压缩比上限（>200:1 判为炸弹）
ZIP_ALLOWED_MEMBER_SUFFIXES: tuple[str, ...] = (
    ".xml", ".rels", ".txt", ".bin", ".png", ".jpg", ".jpeg", ".gif",
    ".emf", ".wmf", ".tiff", ".bmp", ".svg", ".mp4", ".wav", ".mp3",
)

#: OOXML 包里的"身份证"文件 —— 有它才认这是那种格式（光看后缀不算）
#: ★ 这一条是防"把 .zip 改名成 .pptx 就当成 PPT 解析"
ZIP_SIGNATURES: dict[str, tuple[str, ...]] = {
    FMT_PPTX: ("ppt/presentation.xml",),
    FMT_DOCX: ("word/document.xml",),
    FMT_EXCEL: ("xl/workbook.xml",),
}

#: 各格式可接受的 ZIP 内容根（防止一个 xlsx 被当成 pptx 解析）
ZIP_ROOTS: dict[str, str] = {FMT_PPTX: "ppt/", FMT_DOCX: "word/", FMT_EXCEL: "xl/"}

#: 二进制魔数（非 ZIP 类）
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
ZIP_MAGIC_ALT = (b"PK\x05\x06", b"PK\x07\x08")     # 空包 / 分卷

#: 密钥保护的 ZIP（OOXML 加密后是 OLE 复合文档头）—— 直接拒，不去猜密码
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ════════════════════════════════════════════════════════════════════════
# ④ 错误码（机器可读）与人话（给人看）
# ════════════════════════════════════════════════════════════════════════
#: 状态取值（imports.status）
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"          # 幂等命中：什么都不用做（返回既有 import_id）

#: 地区维度不可用（评审 #5 点名，**后端必须返回这个结构化错误**，不能只靠前端隐藏）
DIMENSION_UNAVAILABLE = "DIMENSION_UNAVAILABLE"


class ImporterError(RuntimeError):
    """导入层可预期的错误（带机器可读 `code` + 给人看的话）。

    为什么不复用 `registry.DatasetError`：那是"数据源登记"这条老管道的错误类型，
    它对上层承诺的 code 集合是冻结的（`api_datasets._error_from` 按它映射状态码）。
    FR-003 是新管道，错误码是**新增**的，混进老类型会让老映射表长出一堆新分支 ——
    所以新开一个同形的类（自带 `code`/`message` 属性，api.py 的异常处理器靠鸭子类型接住）。
    """

    def __init__(self, code: str, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        #: 结构化附加信息（如 DIMENSION_UNAVAILABLE 的 `{"dimension": "region"}`）
        self.extra: dict[str, Any] = dict(extra or {})


def dimension_unavailable(dimension: str, dataset_name: str = "") -> ImporterError:
    """「当前数据源没有这个维度」的**唯一**构造处（评审 #5 的响应体形状）。

    调用方（HTTP 层）会把它翻成：

        {"code": "DIMENSION_UNAVAILABLE", "dimension": "region",
         "message": "当前数据源没有地区字段"}

    ★ 这里**没有任何** fallback 分支 —— 不可能在无地区字段时"顺手用 Country 顶上"。
      评审把这一条单独列成断言（ASSERT 25/26），所以它的实现就是"只报错、不替代"。
    """
    label = {"region": "地区", "province": "省份", "city": "城市"}.get(dimension, dimension)
    where = f"「{dataset_name}」" if dataset_name else "当前数据源"
    return ImporterError(
        DIMENSION_UNAVAILABLE,
        f"{where}没有地区字段 —— 按地区看数据需要数据源本身带地区列（如 大区/省份/城市）。",
        extra={"dimension": dimension, "dimension_label": label, "dataset_name": dataset_name},
    )


__all__ = [
    "ALLOWED_SUFFIXES",
    "DATASET_SUFFIXES",
    "DIMENSION_UNAVAILABLE",
    "DOCUMENT_SUFFIXES",
    "FMT_CSV",
    "FMT_DOCX",
    "FMT_EXCEL",
    "FMT_MARKDOWN",
    "FMT_PDF",
    "FMT_PPTX",
    "ImporterError",
    "MAX_DB_BYTES",
    "MAX_DOCUMENT_CHARS",
    "MAX_FILE_BYTES",
    "MAX_MARKDOWN_CHARS",
    "MAX_PPTX_SLIDES",
    "MAX_TABLE_ROWS",
    "MAX_TABLES_PER_FILE",
    "OLE_MAGIC",
    "PARSER_VERSION",
    "PDF_MAGIC",
    "ROLE_DATASET",
    "ROLE_DOCUMENT",
    "SOURCE_TYPES",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_SUCCESS",
    "SUFFIX_LABELS",
    "ZIP_ALLOWED_MEMBER_SUFFIXES",
    "ZIP_MAGIC",
    "ZIP_MAX_COMPRESSION_RATIO",
    "ZIP_MAX_ENTRIES",
    "ZIP_MAX_UNCOMPRESSED_BYTES",
    "ZIP_ROOTS",
    "ZIP_SIGNATURES",
    "dimension_unavailable",
    "roles_of",
    "source_type_of",
]
