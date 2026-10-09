"""文件夹连接器 —— 从本地目录接入知识。

★ 这是最"土"的连接器，但它是**兜底方案**：
  不管客户原来用什么系统，只要能导出成文件丢进一个目录，就能接上。
  这保证产品永远有方案，不会被某个不开放的 SaaS 卡死。

★ 文件解析的取舍（重要）：
  · .txt / .md        直接读
  · .docx / .pdf / .xlsx   要看有没有装对应的库，**没装就如实跳过，
                            不假装读过了** —— 假装读过的后果是：
                            索引里存着一段乱码，AI 拿它去回答，
                            用户看到一堆 "%PDF-1.4" 之类的东西。
  · 扫描件（没有文本层）   明确标记"没有可提取文本"，
                            ★ 不做 OCR 硬猜。另两个项目里都是这个原则。
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from .base import Connector, NormalizedDoc

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".log"}
DOC_EXT = {".docx"}
PDF_EXT = {".pdf"}
SHEET_EXT = {".xlsx", ".xls"}

# 这些目录不扫（构建产物、版本控制、依赖）
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "dist", "build", ".cache",
}

_MAX_BYTES = 20 * 1024 * 1024


def _meta(path: Path) -> str:
    m = os.path.getmtime(path)
    return dt.datetime.fromtimestamp(m, tz=dt.timezone.utc).isoformat(timespec="seconds")


class FolderConnector(Connector):
    """把一棵目录树当作知识源。

    ★ doc_id 用**相对路径**而不是绝对路径：
      换台机器、换个挂载点，ID 不变 —— 增量同步才认得出来是同一篇。
      用绝对路径的话，客户把目录从 D:\\资料 挪到 E:\\资料，
      整个知识库会被当成"全删 + 全增"，之前的 block 和反馈全白费。
    """

    name = "folder"

    def __init__(self, root: str | Path, *, default_permission: str = "external"):
        self.root = Path(root)
        self.default_permission = default_permission

    # ── 列表 ────────────────────────────────────────────────────────
    def list_docs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        out: list[str] = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                if fn.startswith((".", "~$")):
                    continue
                p = Path(base) / fn
                ext = p.suffix.lower()
                if ext not in TEXT_EXT | DOC_EXT | PDF_EXT | SHEET_EXT:
                    continue
                try:
                    if p.stat().st_size > _MAX_BYTES:
                        continue
                except OSError:
                    continue
                # ★ 用 posix 风格，跨平台稳定
                out.append(str(p.relative_to(self.root)).replace("\\", "/"))
        return sorted(out)

    def fetch(self, doc_id: str) -> NormalizedDoc | None:
        p = self.root / doc_id
        if not p.is_file():
            return None
        ext = p.suffix.lower()
        try:
            if ext in TEXT_EXT:
                content = self._read_text(p)
            elif ext in DOC_EXT:
                content = self._read_docx(p)
            elif ext in PDF_EXT:
                content = self._read_pdf(p)
            elif ext in SHEET_EXT:
                content = self._read_sheet(p)
            else:
                return None
        except Exception as exc:  # noqa: BLE001 —— 单篇坏文档不该中断整轮同步
            return NormalizedDoc(
                doc_id=doc_id,
                title=p.stem,
                content="",
                source_ref=doc_id,
                updated_at=_meta(p),
                permission=self.default_permission,
                meta={"error": f"解析失败：{type(exc).__name__}: {exc}"},
            )

        title, permission, body = self._split_header(content, p.stem)
        return NormalizedDoc(
            doc_id=doc_id,
            title=title,
            content=body,
            source_ref=doc_id,
            updated_at=_meta(p),
            permission=permission or self.default_permission,
            meta={"ext": ext},
        )

    # ── 解析 ────────────────────────────────────────────────────────

    def _split_header(self, content: str, fallback_title: str) -> tuple[str, str, str]:
        """识别文件开头的元信息头。

        ★ 让客户能用文件本身声明属性，不用去后台点鼠标：
             标题: 费用报销管理办法
             权限: external
             生效: 2025-03-01
           ---
           （正文）
          ★ 权限这一行是安全关键：默认 external（能对客户说）是**危险的默认值**，
            所以没写"权限:"的文件会保持连接器给的默认值，
            接入流程里会再让管理员确认一遍（见 D5）。
        """
        title = fallback_title
        permission = ""
        body = content
        head, sep, rest = content[:600].partition("\n---")
        if sep:
            for line in head.splitlines():
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if k in ("标题", "title") and v:
                    title = v
                elif k in ("权限", "permission") and v in ("external", "internal"):
                    permission = v
            body = rest.strip()
        else:
            # 没写头的话，用正文第一个像标题的行当标题
            for line in content.splitlines()[:6]:
                s = line.strip().lstrip("#").strip()
                # ★ 跳过 sheet 标记行（Excel 会把每个 sheet 写成【名字】）。
                #   不跳的话，一个多 sheet 的 Excel 标题会变成第一个 sheet 的名字，
                #   出处显示成《【年假规则】》—— 又难看又误导
                #   （那篇文档其实还包含"请假流程"）。
                if re.fullmatch(r"【[^】]{1,40}】", s):
                    continue
                # ★ 带竖线的行是**表格数据行**，不可能是标题。
                #   踩过：Excel 的标题最终变成 "工龄 | 年假天数"，
                #   出处显示成《工龄 | 年假天数》—— 还不如直接用文件名。
                if "|" in s:
                    continue
                if 2 < len(s) < 60:
                    title = s
                    break
        return title, permission, body

    def _read_text(self, p: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return p.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return p.read_text(encoding="utf-8", errors="replace")

    def _read_docx(self, p: Path) -> str:
        try:
            import docx  # type: ignore
        except ImportError:
            # ★ 不假装读过：返回一句能被人看懂、也能被 grep 到的话
            return f"（未安装 python-docx，无法解析 {p.name}。装上后重新同步即可。）"
        d = docx.Document(str(p))
        parts = [para.text for para in d.paragraphs if para.text.strip()]
        for t in d.tables:
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)

    def _read_pdf(self, p: Path) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return f"（未安装 pypdf，无法解析 {p.name}。装上后重新同步即可。）"
        reader = PdfReader(str(p))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                pages.append("")
        text = "\n".join(pages).strip()
        if len(text) < 30:
            # ★ 扫描件没有文本层。明确说出来，不做 OCR 硬猜 ——
            #   硬猜出来的字是错的，而错的知识比没有知识更危险。
            return (
                f"（{p.name} 没有可提取的文本层，可能是扫描件或图片型 PDF。"
                f"需要人工整理成文字，或接入 OCR 后再同步。）"
            )
        return text

    def _read_sheet(self, p: Path) -> str:
        try:
            import openpyxl  # type: ignore
        except ImportError:
            return f"（未安装 openpyxl，无法解析 {p.name}。装上后重新同步即可。）"
        wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
        parts: list[str] = []
        for ws in wb.worksheets:
            # ★ sheet 名用【】而不是 "# "：
            #   "# 年假规则" 会被出站检查的 markdown 规则误判
            #   （`^#{1,6}\s` 认为是标题记号），于是整条回答被拦下转人工。
            #   实测踩到过：Excel 第二个 sheet 的内容因此永远答不出来。
            parts.append(f"【{ws.title}】")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i > 500:  # ★ 防止一个巨型表把索引撑爆
                    parts.append("（表太长，只取了前 500 行）")
                    break
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 同步
# ══════════════════════════════════════════════════════════════════════

def sync(connector: Connector, *, tenant_id: str = "default") -> "SyncResult":
    """把连接器的内容同步进索引。

    ★ 三件事必须都做到，漏一件就会出事故：
      ① 新增    → 写进去
      ② 变更    → 删旧块再重建（不删的话新旧块同时被检索到，模型可能拿旧版本）
      ③ ★ 删除  → 真的删掉（漏了它，删掉的文档会永远被回答，见 D8）
    """
    from .. import retrieval, store
    from .base import SyncResult

    res = SyncResult()
    current = set(connector.list_docs())
    known = {
        row["id"]
        for row in store.rows("SELECT id FROM doc WHERE tenant_id=? AND source=?", (tenant_id, connector.name))
    }

    for doc_id in sorted(current):
        nd = connector.fetch(doc_id)
        if nd is None:
            res.skipped.append(doc_id)
            continue
        if not (nd.content or "").strip():
            res.skipped.append(f"{doc_id}（内容为空）")
            continue
        r = retrieval.upsert_document(
            doc_id=f"{connector.name}:{nd.doc_id}",
            tenant_id=tenant_id,
            source=connector.name,
            source_ref=nd.source_ref,
            title=nd.title,
            content=nd.content,
            updated_at=nd.updated_at,
            permission=nd.permission,
        )
        if r["changed"]:
            if f"{connector.name}:{doc_id}" in known:
                res.updated += 1
            else:
                res.added += 1
        else:
            res.unchanged += 1

    # ★ 删除：源里没有了 → 索引里也必须没有
    for doc_id in known - {f"{connector.name}:{d}" for d in current}:
        retrieval.delete_document(doc_id)
        res.deleted += 1

    return res
