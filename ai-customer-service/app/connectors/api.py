"""通用 API 连接器：接客户自研系统（CRM / ERP / 工单 / 内部知识服务）。

★★ 为什么需要它，以及它为什么必须"定义契约"：

    客户的内部系统千奇百怪，你不可能为每家写一个连接器 ——
    那就变成外包公司了，规模不起来。

    解法是**定义一个最小契约**，让客户自己的 IT 按契约返回 JSON：
        {
          "items": [
            {"id": "...", "title": "...", "content": "...",
             "updated_at": "2025-03-01", "permission": "external"}
          ],
          "next": "可选的下一页游标"
        }

    ★ 契约只有 5 个字段，客户 IT 半天就能对接完。
      这比"支持 20 种系统"实际得多。

★ 另一个判断：**分页必须支持**。
    内部系统动辄几万条记录，一次拉全量会超时/爆内存。
"""

from __future__ import annotations

import json
from typing import Any

from .base import Connector, NormalizedDoc


class ApiConnector(Connector):
    """从一个 HTTP 接口拉文档。

    客户接口需要满足的契约（见模块顶部说明）：
      GET  {base_url}  （可带 ?cursor=xxx 分页）
      headers: 客户自定义（通常放 Authorization）
      →  200 {"items":[{id,title,content,updated_at,permission}], "next": "..."}
    """

    name = "api"

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        items_path: str = "items",
        id_field: str = "id",
        title_field: str = "title",
        content_field: str = "content",
        updated_field: str = "updated_at",
        permission_field: str = "permission",
        next_field: str = "next",
        cursor_param: str = "cursor",
        default_permission: str = "external",
        max_items: int = 5000,
        timeout: float = 30.0,
    ):
        self.base_url = base_url
        self.headers = headers or {}
        self.items_path = items_path
        self.id_field = id_field
        self.title_field = title_field
        self.content_field = content_field
        self.updated_field = updated_field
        self.permission_field = permission_field
        self.next_field = next_field
        self.cursor_param = cursor_param
        self.default_permission = default_permission
        # ★ 上限保护：配置写错（比如接口永远返回 next）时不能无限拉下去
        self.max_items = max_items
        self.timeout = timeout
        self._cache: dict[str, NormalizedDoc] | None = None
        self.last_error = ""

    # ── 拉取 ──────────────────────────────────────────────────────
    def _load(self) -> dict[str, NormalizedDoc]:
        """把远端全部拉下来，按 id 建索引。

        ★ 为什么要一次性拉完再返回：
          连接器的接口是 `list_docs()` + `fetch(id)`，是"先列后取"的形状。
          远端接口是分页的流。所以在 _load 里做一次全量，
          后面 list/fetch 都查内存 —— 简单且不会重复请求。
        """
        if self._cache is not None:
            return self._cache

        from .http import get as http_get

        out: dict[str, NormalizedDoc] = {}
        cursor: str | None = None
        pages = 0
        while len(out) < self.max_items:
            params = {self.cursor_param: cursor} if cursor else {}
            try:
                # ★ 内网接口必须绕过代理，否则内部地址会被发到外部代理
                r = http_get(
                    self.base_url, params=params, headers=self.headers, timeout=self.timeout
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"请求失败：{type(exc).__name__}: {exc}"
                break
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}：{r.text[:160]}"
                break
            try:
                data = r.json()
            except (ValueError, TypeError) as exc:
                self.last_error = f"返回的不是 JSON：{exc}"
                break

            items = data.get(self.items_path) if isinstance(data, dict) else data
            if not isinstance(items, list):
                self.last_error = f"契约不对：期望 {self.items_path} 是数组，实际 {type(items).__name__}"
                break
            if not items:
                break

            for it in items:
                if not isinstance(it, dict):
                    continue
                did = str(it.get(self.id_field) or "").strip()
                content = str(it.get(self.content_field) or "").strip()
                if not did or not content:
                    continue
                out[did] = NormalizedDoc(
                    doc_id=did,
                    title=str(it.get(self.title_field) or did).strip(),
                    content=content,
                    source_ref=f"{self.base_url}#{did}",
                    updated_at=it.get(self.updated_field),
                    permission=str(it.get(self.permission_field) or self.default_permission),
                    meta={"raw_keys": sorted(it.keys())[:12]},
                )

            cursor = data.get(self.next_field) if isinstance(data, dict) else None
            pages += 1
            if not cursor or pages > 200:
                break

        self._cache = out
        return out

    # ── 契约 ──────────────────────────────────────────────────────
    def list_docs(self) -> list[str]:
        return sorted(self._load().keys())

    def fetch(self, doc_id: str) -> NormalizedDoc | None:
        return self._load().get(doc_id)

    def describe(self) -> str:
        n = len(self._load())
        err = f"（{self.last_error}）" if self.last_error else ""
        return f"内部接口（{n} 条）{err}"


def probe(base_url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """接入前的体检：看看客户的接口能不能对上契约。

    ★ 这个函数的价值在于**把"接不上"的原因说清楚**：
      客户 IT 最常犯的错是返回 `{"data": [...]}` 而不是 `{"items": [...]}`。
      直接报"解析失败"他会一头雾水；告诉他"期望 items 是数组，实际是 dict"
      他五分钟就能改好。
    """
    import httpx  # noqa: F401  （保留以便调用方按需覆盖）

    from .http import get as http_get

    result: dict[str, Any] = {"ok": False, "url": base_url}
    try:
        # ★ 同样绕过内网代理
        r = http_get(base_url, headers=headers or {}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"连不上：{type(exc).__name__}: {exc}"
        return result

    result["status"] = r.status_code
    if r.status_code != 200:
        result["error"] = f"HTTP {r.status_code}（期望 200）：{r.text[:200]}"
        return result

    try:
        data = r.json()
    except (ValueError, TypeError) as exc:
        result["error"] = f"返回的不是 JSON：{exc}（前 200 字：{r.text[:200]}）"
        return result

    result["top_level_keys"] = sorted(data.keys())[:20] if isinstance(data, dict) else "(数组)"
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        result["error"] = (
            "契约不对：期望顶层有 items 数组。"
            f"实际顶层是 {type(data).__name__}，键为 {result.get('top_level_keys')}。"
            "如果你的数据在 data 字段下，请改成 items，或用 items_path 参数指定。"
        )
        return result

    result["count"] = len(items)
    if items and isinstance(items[0], dict):
        result["fields"] = sorted(items[0].keys())
        missing = [f for f in ("id", "title", "content") if f not in items[0]]
        if missing:
            result["error"] = (
                f"缺少必需字段：{missing}。"
                "契约要求每条至少有 id / title / content。"
            )
            return result
    result["ok"] = True
    return result
