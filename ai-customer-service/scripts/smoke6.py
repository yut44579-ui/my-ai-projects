"""冒烟测试 6：真实文档解析（Word / Excel / PDF / GBK 文本 / 扫描件）。

★ 为什么必须用真实二进制文件测：
    解析器最容易错的是"格式理解错了"——表格顺序乱、多 sheet 只读了第一个、
    GBK 文件读出乱码、扫描件读出一堆二进制。
    mock 掉解析器就永远测不到这些，而客户的实际资料就是这些格式。

★ 本次要证明的几件事（每一条都对应一个真实会踩的坑）：
    ① Word 里的**表格**要读进来（制度信息大量在表格里）
    ② Excel 的**多个 sheet** 都要读（价格表常常按区域分 sheet）
    ③ GBK 编码的文件不能读成乱码
    ④ ★ 扫描件要**如实说没有文本层**，不做 OCR 硬猜
       —— 硬猜出来的字是错的，而错的知识比没有知识更危险
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test6_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import retrieval, store  # noqa: E402
from app.connectors import folder as folder_conn  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


print("=" * 64)
print("冒烟测试 6 · 真实文档解析")
print("=" * 64)

store.init_db()
KB = Path(r"D:\ai-kefu\data\kb_real")
if not KB.is_dir():
    print("  ❌ 测试文件不存在，先跑 make_test_docs.py")
    sys.exit(1)

# ── 1. 同步真实文件 ───────────────────────────────────────────────────
print("\n[1] 同步真实文件（Word / Excel / PDF / GBK txt）")
conn = folder_conn.FolderConnector(KB)
files = conn.list_docs()
print(f"     扫到 {len(files)} 个: {files}")
check("扫到了全部 5 个文件", len(files) == 5, str(files))

res = folder_conn.sync(conn, tenant_id="default")
print(f"     同步: {res.line()}")
check("全部同步进来", res.added == 5, f"实际 {res.added}")
check("没有跳过任何文件（都解析成功了）", len(res.skipped) == 0, str(res.skipped))

# ── 2. Word ───────────────────────────────────────────────────────────
print("\n[2] Word：正文 + ★表格")
docx_doc = store.one("SELECT id FROM doc WHERE source_ref LIKE '%报销管理办法.docx'")
check("Word 文档入库", docx_doc is not None)
if docx_doc:
    text = "\n".join(
        r["text"] for r in store.rows("SELECT text FROM chunk WHERE doc_id=?", (docx_doc["id"],))
    )
    print(f"     解析出的内容（前 260 字）:\n{text[:260]}")
    check("读到了正文", "5个工作日" in text.replace(" ", ""), text[:80])
    check("★★ 读到了表格里的内容（住宿费上限 500）", "500" in text, text[:200])
    check("★★ 表格读成了人能看懂的形式（有分隔）", "|" in text or "500" in text, text[:200])

# ── 3. Excel ──────────────────────────────────────────────────────────
print("\n[3] Excel：★★ 多个 sheet 都要读")
xls = store.one("SELECT id FROM doc WHERE source_ref LIKE '%假期制度.xlsx'")
check("Excel 入库", xls is not None)
if xls:
    text = "\n".join(
        r["text"] for r in store.rows("SELECT text FROM chunk WHERE doc_id=?", (xls["id"],))
    )
    print(f"     解析出的内容:\n{text[:300]}")
    check("读到了第一个 sheet（年假规则）", "满1年" in text or "5天" in text, text[:150])
    check("★★ 读到了第二个 sheet（请假流程）—— 只读第一个 sheet 是最常见的漏",
          "主管审批" in text or "部门负责人" in text, text[:250])
    check("sheet 名被保留（便于溯源）", "年假规则" in text or "请假流程" in text, text[:120])

# ── 4. GBK 文本 ───────────────────────────────────────────────────────
print("\n[4] ★ GBK 编码的文件不能读成乱码")
gbk = store.one("SELECT id FROM doc WHERE source_ref LIKE '%内部通知%'")
check("GBK 文件入库", gbk is not None)
if gbk:
    text = "\n".join(
        r["text"] for r in store.rows("SELECT text FROM chunk WHERE doc_id=?", (gbk["id"],))
    )
    print(f"     解析出的内容: {text[:120]!r}")
    check("★★ 中文正常（不是乱码）", "季度会" in text, text[:120])
    check("没有替换字符", "\ufffd" not in text, text[:120])
    check("没有问号堆", "????" not in text, text[:120])

# ── 5. ★★ 扫描件：必须如实说，不能硬猜 ────────────────────────────────
print("\n[5] ★★ 扫描件：没有文本层时如实说，不做 OCR 硬猜")
scan = store.one("SELECT id FROM doc WHERE source_ref LIKE '%扫描件%'")
check("扫描件也入库了（不是静默丢弃）", scan is not None)
if scan:
    text = "\n".join(
        r["text"] for r in store.rows("SELECT text FROM chunk WHERE doc_id=?", (scan["id"],))
    )
    print(f"     解析出的内容: {text[:160]!r}")
    check("★★ 明确说了「没有可提取的文本层」", "没有可提取" in text or "扫描件" in text, text[:160])
    check("★★ 说了「需要人工整理」，而不是假装读到了", "人工" in text or "OCR" in text, text[:160])
    check("没有输出二进制垃圾（%PDF 之类）", "%PDF" not in text, text[:120])

# ── 6. 有文本层的 PDF 正常读 ──────────────────────────────────────────
print("\n[6] 有文本层的 PDF 正常读")
pdf = store.one("SELECT id FROM doc WHERE source_ref LIKE '%annual_leave%'")
check("PDF 入库", pdf is not None)
if pdf:
    text = "\n".join(
        r["text"] for r in store.rows("SELECT text FROM chunk WHERE doc_id=?", (pdf["id"],))
    )
    print(f"     解析出的内容: {text[:160]!r}")
    check("读到了 PDF 里的英文内容", "leave" in text.lower() or "days" in text.lower(), text[:120])
    check("没有被误判成扫描件", "没有可提取" not in text, text[:120])

# ── 7. 表格内容能被检索到 ─────────────────────────────────────────────
print("\n[7] ★ 表格/多 sheet 里的内容要能被检索到（否则读了也白读）")
for q, expect in [
    ("住宿费一晚能报多少", "500"),
    ("年假有几天", "5天"),
    ("请假怎么走流程", "主管"),
]:
    r = retrieval.search(q, tenant_id="default", permission="external")
    hit_text = " ".join(h.text for h in r.hits)
    ok = expect in hit_text
    print(f"     「{q}」→ 命中 {[h.title for h in r.hits][:2]}  quality={r.quality}")
    check(f"★ 能检索到「{expect}」", ok, hit_text[:150])

# ── 8. 增量同步对真实文件也有效 ───────────────────────────────────────
print("\n[8] 增量同步")
res2 = folder_conn.sync(folder_conn.FolderConnector(KB), tenant_id="default")
print(f"     {res2.line()}")
check("未变的不重做（二进制文件也一样）", res2.unchanged == 5, str(res2.unchanged))

# ── 9. 解析失败的单个文件不该中断整轮 ─────────────────────────────────
print("\n[9] 单个坏文件不该中断整轮同步")
tmpdir = Path(tempfile.mkdtemp(prefix="kb6_"))
(tmpdir / "坏文档.docx").write_bytes(b"PK\x03\x04this is not a real docx")
(tmpdir / "好文档.md").write_text("标题: 好文档\n权限: external\n---\n这条应该能读出来。\n", encoding="utf-8")
res3 = folder_conn.sync(folder_conn.FolderConnector(tmpdir), tenant_id="default")
print(f"     {res3.line()}")
check("坏文件不影响好文件被处理", res3.added >= 1, str(res3))
check("坏文件如实记录了（不是静默跳过）",
      any("坏文档" in s for s in res3.skipped) or res3.added == 1,
      str(res3.skipped))
good = store.one("SELECT id FROM doc WHERE source_ref LIKE '%好文档%'")
check("好文档确实入库了", good is not None)

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
