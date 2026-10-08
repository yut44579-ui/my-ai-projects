"""document_title.py · 文档标题的可信化（FR-009-A3）。

════════════════════════════════════════════════════════════════════════
【要解决什么（用户实测的真缺陷，不是设想）】
════════════════════════════════════════════════════════════════════════
导入「AI游戏制作知识库.pdf」之后，库里的标题是：

    '\\x00A\\x00I\\x00 n8b\\x0fR6O\\wå\\x8bÆ^\\x93'

字符之间夹着 `\\x00` —— 这是 **UTF-16 的字节被当成单字节编码（latin-1 / cp1252）读**
的典型指纹：把它按单字节还原回字节，再按 UTF-16-BE 解码，得到的就是「AI 游戏制作知识库」。
文件名本身是好的，坏掉的只有从文件里提出来、又原样存进库的那个标题。

════════════════════════════════════════════════════════════════════════
【判据是「质量校验」，不是「猜编码万能探测器」（评审裁决原话）】
════════════════════════════════════════════════════════════════════════
核心不是"哪个编码排第一"，而是：

    解码成功 ≠ 解码结果正确。

`'\\x00A\\x00I'` 用 latin-1 解码永远不会失败 —— 它"成功"了，但结果是垃圾。
所以本模块的做法是：**每一步都要过质量校验，第一个通过的才采用**：

    原始标题
        ↓ 过质量校验 且 过「原样采信的前置判据」（见下）
      采用原样
        ↓ 不过
    有 UTF-16 指纹？ → UTF-16 解码（BOM / 空字节奇偶位判 BE·LE）
        ↓ 不过
    UTF-8 解码（单字节装的 UTF-8 字节）
        ↓ 不过
    ★ 原始文件名（去扩展名）—— 宁可给一个"确实是这份文件的名字"，
      也绝不把一个乱码字符串当成标题显示给用户

（顺序与评审给的一致；Latin-1 那一步在这里等于"原样"，所以它没有单独一层 ——
 把同一个字符串试两遍不会得到第二个结果。）

★ 「原样采信的前置判据」是评审那条顺序之外**唯一**加的一道闸门，理由是它直接服务于
  评审的第 5 步（"不可信就用原始文件名"），而不是在猜编码：

      这串字能不能用**单字节编码**（cp1252）原样表示？
          · 不能 → 里面出现了单字节误读**不可能**产生出来的字符（汉字、真正的 Unicode
            符号）→ 它本来就是好的，原样采信；
          · 能，而且是**纯 ASCII 可打印** → 普通英文标题，原样采信；
          · 能，但不是纯 ASCII → 它**有可能**是"字节被当单字节读"的产物，
            必须由上面某一步**解码证实**，否则退回文件名。

  为什么必须加这道闸门（实测出来的反例）：纯中文的 UTF-16 标题
  （如「季度总结报告」= 5E74 5EA6 603B 7ED3 62A5 544A）**一个空字节都没有**，
  按空字节奇偶位找指纹是找不到的；它被单字节读出来后仍然"全部可打印"，
  于是能骗过质量校验，显示成 `'[c^ƒ‘;~Ób¥TJ'` 这种"看着像字、其实全是错的"标题。
  这种情况下正确答案就是评审第 5 步的**文件名兜底**。

★ **不引 charset 检测库**（评审：不值得为一个 PDF metadata bug 建新依赖）——
  本模块只用标准库（cp1252 是标准库自带的编码），没有 import 任何第三方包。

════════════════════════════════════════════════════════════════════════
【为什么不放在 app/engine/docs.py 里】
════════════════════════════════════════════════════════════════════════
engine 层那批文件是冻结契约（FR-009-A 边界：`app/engine/**` 一个字不许改），
而且**两条文档管道都要用这个修复**：

    app/importer/parsers.py     FR-003 新管道（写 SQLite documents）
    app/api_documents.py        TASK-003 旧管道（写 state/documents.json）

所以它是一个**独立的、无依赖的**纯函数模块，两边各调一次，行为完全一致 ——
不复制两份实现（复制两份迟早只修一处）。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

#: 标题长度上限：超过这个长度几乎可以肯定不是标题（是正文被误当标题），按"不可信"处理。
MAX_TITLE_CHARS = 200

#: 各种"垃圾字符"的容忍比例。标题很短，所以这里给的是**比例**而不是绝对个数：
#: 一个 12 字的标题里出现 1 个替换符 = 8.3% > 2%，判不可信；100 字的标题里出现 1 个 ≈ 1%，放过。
MAX_BAD_RATIO = 0.02

#: 可打印字符的最低比例（CJK / 拉丁字母 / 数字 / 标点都算可打印）
MIN_PRINTABLE_RATIO = 0.90

#: UTF-16 指纹只看前多少个字符：误读的 UTF-16 头几个字符就能看出来，不必扫全串
UTF16_PROBE_CHARS = 64

#: 允许出现在标题里的"控制字符"（换行/制表算空白，不算乱码）
_ALLOWED_CONTROLS = frozenset("\t\n\r")

#: 单字节视角用的编码名（只为文档里写清楚"是哪一套"；实际用的是下面那张表）。
#: 为什么不是单一 codec：0x80–0x9F 这一段**两套解码都会出现**，谁也覆盖不了谁 ——
#:     · latin-1 把 0x8B 解成 U+008B（C1 控制符）→ cp1252 编不回去（那是未定义位）
#:     · cp1252  把 0x83 解成 U+0192（ƒ）      → latin-1 编不回去（超出 0xFF）
#: 所以"字节被当单字节读"的字符串要还原，得**两套都认**（见下面的 `single_byte_view`）。
SINGLE_BYTE_CODEC = "cp1252"

#: 字节顺序标记（写成 chr()，免得源码里藏一个看不见的字符）
BOM = chr(0xFEFF)


def _cp1252_byte_of() -> dict[str, int]:
    """{cp1252 字符: 对应的字节}（从标准库 codec 现生成，不手抄那张表）。

    只收**超出 0xFF 的**字符：0x00–0xFF 的字符按 latin-1 直接取码位即可（下面函数里那一条），
    这张表补的是 0x80–0x9F 那一段被 cp1252 解成 ƒ ‘ ’ … € 之类的符号。
    """
    table: dict[str, int] = {}
    for byte in range(0x100):
        try:
            char = bytes([byte]).decode(SINGLE_BYTE_CODEC)
        except UnicodeDecodeError:                    # 0x81/0x8D/0x8F/0x90/0x9D 在 cp1252 里未定义
            continue
        if ord(char) > 0xFF:
            table.setdefault(char, byte)
    return table


_CP1252_BYTE_OF: dict[str, int] = _cp1252_byte_of()


# ════════════════════════════════════════════════════════════════════════
# ① 质量校验（本模块的核心判据）
# ════════════════════════════════════════════════════════════════════════
def is_trustworthy_title(candidate: str) -> bool:
    """这段文字能不能当标题用（评审点名的四条判据，逐条落在这里）。

    ① 不应大量出现 `\\x00`（UTF-16 字节当单字节读的指纹）
    ② 不应大量出现 Unicode 替换符 U+FFFD（解码失败留下的疤）
    ③ 不应出现明显控制字符（除制表/换行）
    ④ 长度合理（1 ~ MAX_TITLE_CHARS）且可打印字符占比达标

    **NUL 单独从严**：标题是从文件里"提出来的一行字"，正常标题里出现 NUL 只有一种解释
    —— 编码读错了。所以 NUL 是零容忍，不是"比例达标就行"（评审的"不应大量出现"是通用判据，
    这里是它在标题上的严格化，理由写在上一句）。
    """
    if not candidate:
        return False
    if len(candidate) > MAX_TITLE_CHARS:
        return False
    if "\x00" in candidate:
        return False

    total = len(candidate)
    replacement = candidate.count("�")
    controls = sum(
        1 for ch in candidate if unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROLS
    )
    printable = sum(1 for ch in candidate if ch.isprintable() or ch in _ALLOWED_CONTROLS)
    if printable / total < MIN_PRINTABLE_RATIO:
        return False
    if (replacement + controls) / total > MAX_BAD_RATIO:
        return False
    # 至少得有一个"真的看得见"的字符：全是空白不算标题
    return any(ch.strip() for ch in candidate)


# ════════════════════════════════════════════════════════════════════════
# ② 单字节视角：这串字有没有可能是"字节被当单字节读"的产物
# ════════════════════════════════════════════════════════════════════════
def single_byte_view(value: str) -> bytes | None:
    """把"只可能是单字节读出来的"字符串还原成原本的字节；不是这种串就返回 None。

    逐字符还原，两套单字节解码都认（见 `SINGLE_BYTE_CODEC` 上面那段说明）：
        · 码位 < 0x100        → 就是那个字节（latin-1 那条路）
        · 码位 ≥ 0x100 但在 cp1252 表里 → 取它对应的字节（cp1252 那条路）

    返回 None 的意思是：这串字里出现了**单字节误读不可能产生**的字符（汉字、真正的
    Unicode 符号）→ 它本来就是一份好的文本，不需要也不应该被"修"。
    """
    out = bytearray()
    for char in value:
        code = ord(char)
        if code < 0x100:
            out.append(code)
            continue
        byte = _CP1252_BYTE_OF.get(char)
        if byte is None:
            return None
        out.append(byte)
    return bytes(out)


def _is_plain_ascii(value: str) -> bool:
    """整串都是可打印 ASCII（或制表/换行）—— 普通英文标题就是这种。

    为什么它可以直接采信：单字节误读会把 ≥0x80 的字节带进来，纯 ASCII 说明这串字
    里没有一个可疑字节，没有什么可"修"的。
    """
    return all(ch in _ALLOWED_CONTROLS or 0x20 <= ord(ch) < 0x7F for ch in value)


def looks_like_utf16(value: str) -> bool:
    """这段文字有没有明显的 UTF-16 误读特征（评审要求的第一步判断）。"""
    return bool(_utf16_endianness(value))


def _utf16_endianness(value: str) -> tuple[str, ...]:
    """按指纹判断该按哪种 UTF-16 解，返回要试的顺序（不确定就两种都试）。

    指纹只看**字节排布**，不看内容：
        · 有 BOM（'\\ufeff' 或字节 FE FF / FF FE）                     → 按 BOM 走
        · 空字节**只出现在偶数位**（'\\x00A\\x00I…'）                    → UTF-16-BE
        · 空字节**只出现在奇数位**（'A\\x00I\\x00…'）                    → UTF-16-LE
        · 空字节两种奇偶位都有 / 一个都没有                            → 不是（或不只是）UTF-16

    ★ 判据是"**奇偶位一致**"，不是"空字节占比高"：中文的 UTF-16 里两个字节都不是 0
      （'游' = 6E 38），所以一份中文标题里空字节可能只占 3/32 —— 占比门槛会把它漏掉，
      而"所有空字节都落在同一奇偶位"这条在单字节误读的 UTF-16 上是**必然成立**的。
    """
    if value.startswith(BOM):
        return ("utf-16",)
    data = single_byte_view(value)
    if data is None or len(data) < 2:
        return ()
    if data[:2] in (b"\xfe\xff", b"\xff\xfe"):
        return ("utf-16",)

    head = value[:UTF16_PROBE_CHARS]
    nul_positions = [index for index, ch in enumerate(head) if ch == "\x00"]
    if not nul_positions:
        return ()                                    # 一个空字节都没有 → 没有 UTF-16 排布指纹
    even_nulls = sum(1 for index in nul_positions if index % 2 == 0)
    odd_nulls = len(nul_positions) - even_nulls
    if even_nulls and odd_nulls:
        # 空字节**两种奇偶位都出现** → 这是普通文本里零散混进的 NUL，不是 UTF-16 的排布
        return ()
    if even_nulls:
        return ("utf-16-be", "utf-16-le")            # 空字节只落在**偶数位** → 高字节在前（BE）
    return ("utf-16-le", "utf-16-be")                # 空字节只落在**奇数位** → 低字节在前（LE）


# ════════════════════════════════════════════════════════════════════════
# ③ 主入口
# ════════════════════════════════════════════════════════════════════════
def _decode_attempts(value: str) -> list[str]:
    """按可疑程度依次解码（UTF-16 → UTF-8）；每一步的结果仍要过质量校验，别在这里判。"""
    candidates: list[str] = []
    data = single_byte_view(value)
    if data is None:
        return candidates                            # 不是单字节误读的产物，没有可解码的东西
    for encoding in _utf16_endianness(value):
        try:
            candidates.append(data.decode(encoding))
        except (UnicodeDecodeError, LookupError):    # 半个字（奇数长度）解不出来就换下一种
            continue
    if data.strip():
        try:
            candidates.append(data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return candidates


def _raw_is_acceptable(value: str) -> bool:
    """原文能不能**不走解码**直接用（见模块顶部"原样采信的前置判据"）。"""
    if single_byte_view(value) is None:
        return True                                  # 单字节误读产生不出这些字符 → 本来就是好的
    return _is_plain_ascii(value)


def repair_title(raw: str | None, filename: str | None = None) -> str:
    """把"从文件里提出来的标题"修成可信的标题；修不出来就退回**原始文件名（去扩展名）**。

    这不是"猜编码"：每一步的产物都要过 `is_trustworthy_title`，
    第一个通过的才是结果 —— 全部不通过时返回文件名 stem，绝不放乱码出去。

    （`filename` 不传时返回空串：调用方拿不到文件名就等于没有兜底可用，
    宁可给空串，也不返回那个不可信的原文。）
    """
    fallback = Path(str(filename or "").strip().replace("\\", "/")).stem.strip()
    text = str(raw or "").strip()
    if not text:
        return fallback

    if is_trustworthy_title(text) and _raw_is_acceptable(text):
        return text
    for candidate in _decode_attempts(text):
        cleaned = candidate.strip()
        if is_trustworthy_title(cleaned):
            return cleaned
    return fallback


__all__ = [
    "MAX_TITLE_CHARS",
    "MAX_BAD_RATIO",
    "MIN_PRINTABLE_RATIO",
    "SINGLE_BYTE_CODEC",
    "UTF16_PROBE_CHARS",
    "is_trustworthy_title",
    "looks_like_utf16",
    "repair_title",
    "single_byte_view",
]
