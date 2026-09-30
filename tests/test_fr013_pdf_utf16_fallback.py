"""tests/test_fr013_pdf_utf16_fallback.py · FR-013 验收测试（PDF 乱码兜底解码）。

════════════════════════════════════════════════════════════════════════
【本文件测什么】
════════════════════════════════════════════════════════════════════════
缺陷：`data/original/f1307c5d61d4_AI游戏制作知识库.pdf`（12 页，PPT 导出）的字体是
`/Type0` + `/Encoding /UniGB-UTF16-H` 且**没有 ToUnicode 表**，pypdf 的
`page.extract_text()` 把 2 字节 UTF-16 的字节对齐搞坏 → 3295 字符里有 511 个 NUL，
正文形如 `'\\x00A\\x00I\\x00 n8b…'`。

修法（`app/engine/docs.py::extract_pdf` 里的一条**兜底路径**）：先用 pypdf 的
`ContentStream` 取原始字符串字节，再按 UTF-16BE 解；**两道闸门**都过才替换。

  ① `_looks_broken`  结果可疑才去试兜底      —— 正常 PDF 连试都不试
  ② `_is_better_text` 兜底确实更干净才采用    —— 误进兜底也换不掉正常结果

本文件按这两道闸门分四组：
  A. 真文件端到端（那份坏 PDF：NUL 归零、正文是正常中文、warning 如实写）
  B. 回归红线（另一份正常 PDF：**逐字节一致**；正常 PDF 不进兜底路径）
  C. 闸门单测（① 可疑判定 / ② 更干净判定 / 兜底解码器本身）
  D. 边界（扫描件仍报"没有可提取文本"；返回契约字段一个不少）

**不测**：不测 OCR（本阶段不做，扫描件如实报 document_no_text）；
        不测 LLM（本模块没有任何模型调用）。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from app.engine import docs  # noqa: E402

# ════════════════════════════════════════════════════════════════════════
# 真文件 + 改前基线（这两个哈希是**改代码之前**实测出来的，不许改）
# ════════════════════════════════════════════════════════════════════════
ORIGINAL_DIR = PROJECT_ROOT / "data" / "original"
BROKEN_PDF = ORIGINAL_DIR / "f1307c5d61d4_AI游戏制作知识库.pdf"
NORMAL_PDF = ORIGINAL_DIR / "f4f833db56ce_雾 墟海.pdf"

#: 坏 PDF 改前的文本哈希（511 个 NUL 的那一版）—— 修完之后必须**不再是**它
BROKEN_PDF_TEXT_SHA_BEFORE = "b4ff69a2be43a56da0dc304b87753de6437b5a87fc4e167da3817d9e247157e3"

#: 正常 PDF 的文本哈希 / 整个返回值的哈希 —— 这两条是 FR-013 的**回归红线**：
#: 兜底路径无论怎么改，这份正常 PDF 的 extract() 结果必须逐字节不变。
NORMAL_PDF_TEXT_SHA = "a3b433a34a1f611c8b5025908b879ef864fc3b935175ca596bc3482fa27a2f95"
NORMAL_PDF_JSON_SHA = "d658a39fe2e357713ff08585427a958ff47e3c3ccd80cf9bfea7475e2eb0748d"

#: 兜底生效时必须出现的那句话（指令原文，不许改写）
FALLBACK_NOTE = "该 PDF 字体为 Type0/UniGB-UTF16-H 且无 ToUnicode 表，已用 UTF-16 兜底解码"

#: 第 1 页正文的开头（实测，机器核对用）
PAGE1_HEAD = "本手册系统整理了 AI 在游戏开发中的应用知识"

requires_real_files = pytest.mark.skipif(
    not BROKEN_PDF.is_file() or not NORMAL_PDF.is_file(),
    reason="data/original/ 下的两份真 PDF 不在（本测试要的是真文件，不拿假字节代替）",
)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ════════════════════════════════════════════════════════════════════════
# A. 真文件端到端：那份坏 PDF 修好了
# ════════════════════════════════════════════════════════════════════════
@requires_real_files
def test_坏PDF走完整extract后NUL归零():
    """核心验收：3295 字符 / 511 个 NUL → NUL 必须为 0，替换符也必须为 0。"""
    result = docs.extract(BROKEN_PDF)
    text = result["text"]

    assert text.count("\x00") == 0, "兜底之后不许再有 NUL"
    assert text.count("�") == 0, "兜底之后不许有替换符"
    assert result["chars"] > 0
    assert text_sha256(text) != BROKEN_PDF_TEXT_SHA_BEFORE, "结果跟改前一样 = 兜底根本没生效"


@requires_real_files
def test_坏PDF前120字是正常中文():
    """交付证据里贴的那 120 字：必须是正常中文，不是 `\\x00A\\x00I` 那副样子。"""
    head = docs.extract(BROKEN_PDF)["text"][:120]

    assert PAGE1_HEAD in head
    assert "\x00" not in head
    # 中文字符要占相当比例（乱码版这里几乎全是 ASCII+NUL）
    assert sum(1 for char in head if "一" <= char <= "鿿") >= 30


@requires_real_files
def test_坏PDF正文含多页要点():
    """不只是第 1 页：兜底要覆盖整份（实测 1/2/4/6/7/8/9/10/11/12 页都靠它救回来）。"""
    text = docs.extract(BROKEN_PDF)["text"]
    for expected in ("一、AI 在游戏中的核心应用", "二、核心技术详解", "三、AI 游戏开发流程",
                     "四、常用工具与框架", "五、经典案例", "六、学习路径与面试要点"):
        assert expected in text, f"少了这一段：{expected}"


@requires_real_files
def test_坏PDF标题是首页的首行而不是整页():
    """首页首行就是标题「AI 游戏制作知识库」—— 兜底不能把整页拍成一行。"""
    assert docs.extract(BROKEN_PDF)["title"] == "AI 游戏制作知识库"


@requires_real_files
def test_坏PDF保住了行结构与大纲():
    """PDF 自己的定位算符（Tm/Td…）就是它的换行依据，兜底要照着断行 ——
    一行没有的话，`chunk_document` 按行切片的粒度就没了。"""
    result = docs.extract(BROKEN_PDF)

    assert len(result["text"].splitlines()) >= 40
    for heading in ("一、AI 在游戏中的核心应用", "二、核心技术详解", "六、学习路径与面试要点"):
        assert heading in result["outline"]


@requires_real_files
def test_坏PDF的warnings如实说明用了兜底():
    """用了兜底就要写出来 —— 不许静默替换。"""
    warnings = docs.extract(BROKEN_PDF)["warnings"]
    assert any(FALLBACK_NOTE in warning for warning in warnings)
    assert any("UTF-16 兜底解码" in warning for warning in warnings)


@requires_real_files
def test_坏PDF的扫描页仍然如实报没文本():
    """第 3、5 页是图片页：兜底救不回来（没有文字字节），必须照旧进 empty_pages。"""
    result = docs.extract(BROKEN_PDF)
    assert result["extra"]["empty_pages"] == [3, 5]
    assert any("没有可提取文本" in warning for warning in result["warnings"])


# ════════════════════════════════════════════════════════════════════════
# B. 回归红线：正常 PDF 逐字节不变
# ════════════════════════════════════════════════════════════════════════
@requires_real_files
def test_正常PDF正文逐字节不变():
    """改前实测的哈希 —— 兜底路径必须对正常 PDF 零影响。"""
    result = docs.extract(NORMAL_PDF)

    assert text_sha256(result["text"]) == NORMAL_PDF_TEXT_SHA
    assert result["text"].count("\x00") == 0
    assert result["chars"] == 859


@requires_real_files
def test_正常PDF整个返回值逐字节不变():
    """连 warnings / extra / outline 一起比 —— 少一个字段或改一句文案都算回归。"""
    result = docs.extract(NORMAL_PDF)

    assert json_sha256(result) == NORMAL_PDF_JSON_SHA
    assert not any(FALLBACK_NOTE in warning for warning in result["warnings"])


@requires_real_files
def test_正常PDF根本不进兜底路径(monkeypatch):
    """闸门①的直接证明：正常 PDF 上，取原始字节的函数**一次都不会被调用**。"""
    calls: list[str] = []
    original = docs._raw_text_chunks

    def counting(page, reader):
        calls.append("called")
        return original(page, reader)

    monkeypatch.setattr(docs, "_raw_text_chunks", counting)
    docs.extract(NORMAL_PDF)

    assert calls == [], "正常 PDF 不该触发兜底（闸门①失守）"


@requires_real_files
def test_坏PDF确实走了兜底路径(monkeypatch):
    """反向证明：同一套计数，坏 PDF 上必须被调用到（否则上面那条等于没测）。"""
    calls: list[str] = []
    original = docs._raw_text_chunks

    def counting(page, reader):
        calls.append("called")
        return original(page, reader)

    monkeypatch.setattr(docs, "_raw_text_chunks", counting)
    docs.extract(BROKEN_PDF)

    assert len(calls) >= 10, "坏 PDF 的文字页都该试一遍兜底"


# ════════════════════════════════════════════════════════════════════════
# C. 两道闸门的单测（不依赖真文件，直接喂纯函数）
# ════════════════════════════════════════════════════════════════════════
def test_闸门一_正常中文不判可疑():
    normal = "本手册系统整理了 AI 在游戏开发中的应用知识，涵盖 NPC 智能、路径寻路、决策系统。"
    assert docs._looks_broken(normal) is False


def test_闸门一_正常英文不判可疑():
    """正常英文 PDF：长度够长、非 ASCII 几乎为零，不许被第 3 条判据误伤。"""
    english = "Quarterly sales report for the United Kingdom market. " * 8
    assert docs._looks_broken(english) is False


def test_闸门一_含NUL判可疑():
    assert docs._looks_broken("\x00A\x00I\x00 n8b") is True


def test_闸门一_替换符多判可疑():
    assert docs._looks_broken("正常的开头" + "�" * 20) is True


def test_闸门一_非ASCII多但中文极少判可疑():
    """latin-1 式的乱码：没有 NUL 也没有替换符，靠第 3 条判据抓。"""
    mojibake = "æˆ‘ä»¬çš„æ•°æ®åˆ†æž" * 8
    assert docs._looks_broken(mojibake) is True


def test_闸门一_空串不判可疑():
    assert docs._looks_broken("") is False


def test_闸门二_正常结果永远不会被替换():
    """★ 最关键的一条：正常页的脏字符是 0，谁都不可能严格少于 0 —— 换不掉。"""
    normal = "东方幻想开放世界《墟海》品牌视觉全案，深海雾境浮空古风岛屿。"
    for candidate in (
        "⎋ᷘẟㅿ≠Ȼ↟壅析⬀晡濺瀀濴掭Ȼ㜆ㅿ㴷ㄎ↟",   # 那份正常 PDF 的兜底解码结果（实测就是这样的乱码）
        "一、AI 在游戏中的核心应用",
        "",
        "   ",
    ):
        assert docs._is_better_text(normal, candidate) is False


def test_闸门二_更干净就采纳():
    broken = "\x00A\x00I\x00 n8b\x0fR6O"
    fixed = "本手册系统整理了 AI 在游戏开发中的应用知识"
    assert docs._is_better_text(broken, fixed) is True


def test_闸门二_兜底候选为空不采纳():
    assert docs._is_better_text("\x00A\x00I", "") is False


def test_闸门二_两边都干净时不许把中文换少():
    """都没有脏字符时，只有中文成倍变多才算更好（防止拿"干净但更短"的东西顶掉原文）。"""
    assert docs._is_better_text("中文内容若干字", "中文") is False


def test_垃圾字符计数_正常正文为0():
    assert docs._junk_count("1. NPC 智能（非玩家角色）：通过 AI 让 NPC") == 0
    assert docs._junk_count("《墟海》视觉全案 —— 深海雾境·浮空岛屿 100%") == 0


def test_垃圾字符计数_乱码不为0():
    assert docs._junk_count("Nİ䄀䤀 ⡗㡮རⵎ葶㡨썟鑞⡵") > 0
    assert docs._junk_count("v—N;mAe¹hH0\x02") > 0


# ════════════════════════════════════════════════════════════════════════
# D. 边界：扫描件 + 返回契约
# ════════════════════════════════════════════════════════════════════════
def test_扫描件仍然报没有可提取文本(tmp_path):
    """本阶段不做 OCR：一页文字都没有的 PDF 必须抛 document_no_text，不许假装成功。"""
    from test_documents import build_minimal_pdf

    path = tmp_path / "scanned.pdf"
    path.write_bytes(build_minimal_pdf(["", ""], draw_text=False))

    with pytest.raises(docs.DocumentError) as excinfo:
        docs.extract(path)
    assert excinfo.value.code == "document_no_text"


def test_正常小PDF解出来的就是写进去的那些字(tmp_path):
    """兜底不许动正常 PDF 一个字节 —— 手工造的最小 PDF 上直接核对原文。"""
    from test_documents import build_minimal_pdf

    path = tmp_path / "plain.pdf"
    path.write_bytes(build_minimal_pdf(["Quarterly sales report", "Second page line"]))

    result = docs.extract(path)
    assert result["text"] == "Quarterly sales report\nSecond page line"
    assert result["warnings"] == []
    assert not any(FALLBACK_NOTE in warning for warning in result["warnings"])


@requires_real_files
def test_返回契约字段一个不少():
    """FR-013 明令：extract() 的返回字段不许少、不许改名。"""
    for path in (BROKEN_PDF, NORMAL_PDF):
        result = docs.extract(path)
        assert set(result) == {
            "text", "blocks", "block_unit", "title", "outline",
            "warnings", "extra", "chars", "suffix", "filename",
        }
        assert result["block_unit"] == "page"
        assert isinstance(result["outline"], list)
        assert isinstance(result["warnings"], list)
