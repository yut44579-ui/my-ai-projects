"""连接器基类。

★ 设计原则：核心逻辑**不依赖任何具体知识源**（见 docs/决策记录.md）。

    知识源各家的 API 天差地别（飞书/语雀/Confluence/共享盘/自研系统），
    但它们对上层只需要提供一个东西：**把文档变成统一的 NormalizedDoc**。

    连接器只做三件事：
      ① 列出源里有哪些文档（含变更时间）
      ② 取出一篇文档的纯文本
      ③ 报告哪些文档被删了

    ★ 第 ③ 件最容易漏，但漏了就会出事：删掉的文档会永远留在索引里
      继续被回答（见 D8）。所以基类把它定为必须实现。

★ 为什么要有"文件夹连接器"这个看起来最土的东西：
    不管客户用什么系统，只要能导出成文件放进一个目录，就能接上。
    这保证了**永远有方案**，不会被某个不开放的 SaaS 卡死。
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedDoc:
    """统一文档格式。所有连接器的输出都是它。"""

    doc_id: str            # 源内稳定 ID（增量同步靠它）
    title: str
    content: str
    source_ref: str = ""   # 源里的路径 / URL / 文档 ID（给用户看出处）
    updated_at: str | None = None
    permission: str = "external"   # external=可以对客户说 / internal=只能说给内部
    meta: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:32]


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"新增 {self.added} · 更新 {self.updated} · 删除 {self.deleted} "
            f"· 未变 {self.unchanged}"
            + (f" · 跳过 {len(self.skipped)}" if self.skipped else "")
        )


class Connector(ABC):
    """所有知识源连接器的接口。"""

    name: str = "base"

    @abstractmethod
    def list_docs(self) -> list[str]:
        """列出源里**当前**存在的 doc_id。用于识别被删的文档。"""

    @abstractmethod
    def fetch(self, doc_id: str) -> NormalizedDoc | None:
        """取一篇文档。返回 None 表示取不到（不抛异常，便于跳过单篇坏文档）。"""

    def describe(self) -> str:
        return f"{self.name}"
