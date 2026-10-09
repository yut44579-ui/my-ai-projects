"""网页连接器：把一堆 URL 当知识源。

★ 中小企业最常有的"公开资料"就是**官网和公众号文章**。
  这些东西没人会去整理成文档，但它们回答了客户最常问的问题
  （你们是做什么的、有哪些产品、怎么联系）。

★ 两个实现判断：

  ① **用标准库的 HTMLParser，不引第三方**（BeautifulSoup 之类）。
     理由：私有化部署的机器内存很小，而且我们只需要"把标签去掉、
     留下正文"，不需要完整的 DOM 能力。少一个依赖少一份运维负担。

  ② ★ **抓不到就如实报告，不缓存空内容**。
     第一版如果直接存空字符串，会污染索引（一堆空 chunk），
     而且看起来"接上了"其实什么都没抓到。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from .base import Connector, NormalizedDoc

# 这些标签里的内容**不是正文**，要整块丢掉
_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
# 这些标签结束时补一个换行，避免所有文字连成一坨
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "table", "blockquote", "pre",
}


class _TextExtractor(HTMLParser):
    """把 HTML 抽成纯文本。

    ★ 关键是"块级标签补换行"：
      不补的话，`<p>第一段</p><p>第二段</p>` 会变成"第一段第二段"，
      检索和阅读都会出问题。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title += data
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # 压掉多余空行，但保留段落分隔
        raw = re.sub(r"[ \t\u00a0]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()

    def title(self) -> str:
        return self._title.strip()


def html_to_text(html: str) -> tuple[str, str]:
    """返回 (标题, 正文)。"""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 —— 坏 HTML 不该让整轮同步挂掉
        pass
    return p.title(), p.text()


class UrlConnector(Connector):
    """把一组 URL 当知识源。

    ★ 为什么不内置"自动发现站内链接"：
      那会变成一个爬虫，涉及 robots.txt、频率、被抓站点的感受，
      而且很容易把客户的整个官网（含无关页面）拉进来，稀释检索质量。
      **让客户明确给几个 URL** 更可控，也更符合"不要求他们改变现状"的定位。
    """

    name = "url"

    def __init__(
        self,
        urls: list[str],
        *,
        tenant_id: str = "default",
        default_permission: str = "external",
        timeout: float = 20.0,
    ):
        self.urls = [u.strip() for u in urls if u and u.strip()]
        self.tenant_id = tenant_id
        self.default_permission = default_permission
        self.timeout = timeout

    def list_docs(self) -> list[str]:
        # ★ doc_id 就用 URL。换台机器、换个部署，URL 不变，增量同步认得出来。
        return list(self.urls)

    def fetch(self, doc_id: str) -> NormalizedDoc | None:
        from .http import get as http_get

        try:
            # ★ 走 http.get 而不是 httpx.get：
            #   它会**对内网地址绕过代理**（否则内部地址会被发到外部代理，
            #   既是功能问题也是安全问题，见 connectors/http.py 的说明）
            r = http_get(
                doc_id,
                timeout=self.timeout,
                headers={"User-Agent": "AIKefuBot/0.1 (+knowledge-sync)"},
            )
        except Exception as exc:  # noqa: BLE001
            return NormalizedDoc(
                doc_id=doc_id, title=doc_id, content="", source_ref=doc_id,
                permission=self.default_permission,
                meta={"error": f"抓取失败：{type(exc).__name__}: {exc}"},
            )

        if r.status_code != 200:
            # ★ 如实报告状态码，不假装抓到了
            return NormalizedDoc(
                doc_id=doc_id, title=doc_id, content="", source_ref=doc_id,
                permission=self.default_permission,
                meta={"error": f"HTTP {r.status_code}"},
            )

        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "<html" not in r.text[:500].lower():
            # 不是网页（可能是 PDF 或 JSON），原样当文本
            body = r.text
            title = doc_id.rsplit("/", 1)[-1]
        else:
            title, body = html_to_text(r.text)
            title = title or doc_id.rsplit("/", 1)[-1]

        # 正文开头的"标题:/权限:/---"元信息头和文件连接器保持同一套约定
        if body.startswith("标题:"):
            from .folder import FolderConnector

            fc = FolderConnector.__new__(FolderConnector)
            t2, perm, b2 = FolderConnector._split_header(fc, body, title)
            return NormalizedDoc(
                doc_id=doc_id, title=t2 or title, content=b2, source_ref=doc_id,
                permission=perm or self.default_permission, meta={"content_type": ctype},
            )

        return NormalizedDoc(
            doc_id=doc_id, title=title, content=body, source_ref=doc_id,
            permission=self.default_permission, meta={"content_type": ctype},
        )

    def describe(self) -> str:
        return f"网页（{len(self.urls)} 个）"
