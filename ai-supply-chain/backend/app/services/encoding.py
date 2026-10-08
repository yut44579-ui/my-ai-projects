"""中文 CSV 文本编码探测（TASK-001 §四）。

评审点名：中文 CSV 常见 GBK/GB18030 而非 UTF-8，读错就乱码。
★ 按"自己写 UTF-8 → GB18030 尝试"来做，不引 chardet（硬边界：不引新库）。

探测顺序：UTF-8 BOM → UTF-8 → GB18030（GBK 的超集，能解 GBK 也能解 GB2312）。
"""

from __future__ import annotations

import codecs

from app.services.errors import ImportErrorCode, ImportFailure

# 允许对外声明/回传的编码白名单
SUPPORTED_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030")


def _try_decode(raw: bytes, encoding: str) -> str | None:
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def sniff_encoding(raw: bytes) -> str:
    """只探测编码名，不返回文本。失败抛 parse_error（不乱码硬解）。"""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    for candidate in ("utf-8", "gb18030"):
        if _try_decode(raw, candidate) is not None:
            return candidate
    raise ImportFailure(
        ImportErrorCode.PARSE_ERROR,
        "无法识别文件编码：既不是 UTF-8 也不是 GB18030/GBK，请另存为 UTF-8 或 GBK 后重试",
    )


def decode_text(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    """解码字节为文本，返回 (文本, 实际使用的编码名)。

    encoding 为 None 时自动探测；显式传入时按传入的编码解（用户在预览里确认过的编码），
    解不开则报 parse_error，绝不静默回退成乱码。
    """
    if encoding is None:
        encoding = sniff_encoding(raw)

    if encoding not in SUPPORTED_ENCODINGS:
        raise ImportFailure(
            ImportErrorCode.PARSE_ERROR,
            f"不支持的编码声明 {encoding!r}，仅支持 {', '.join(SUPPORTED_ENCODINGS)}",
        )

    text = _try_decode(raw, encoding)
    if text is None:
        raise ImportFailure(
            ImportErrorCode.PARSE_ERROR,
            f"按 {encoding} 解码失败，文件的实际编码与声明不符",
        )
    return text, encoding
