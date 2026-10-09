"""检索层：分块 + 混合检索（关键词 + 语义）+ 检索质量评分。

★ 两个关键设计：

  ① **混合检索**，不是二选一：
     关键词（中文 2-gram / BM25）擅长专有名词、型号、内部黑话；
     语义（embedding）擅长"同义不同词"（咋弄 / 怎么办 / 怎么操作）。
     口语场景两者都缺一不可 —— 只用关键词，换个说法就查不到；
     只用语义，专有名词会被糊掉。

  ② ★ **检索质量要拆成多个信号算，不能让模型自评。**
     模型自评置信度非常不可靠（该说不知道的时候它也很自信）。
     可靠的信号是检索本身：
       · 最高分高不高
       · 命中了几个块（只命中 1 个 vs 命中 5 个一致）
       · ★ 命中的块之间**矛不矛盾** —— 矛盾时必须转人工
       · ★ 命中的块**新不新** —— 旧文档要打折，否则会自信地发出过期答案
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import config, store

_WORD = re.compile(r"[a-zA-Z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文 2-gram + 英文单词。

    ★ 为什么是 2-gram 而不是分词：
      企业内部文本里有大量未登录词（产品型号、内部缩写、人名），
      通用分词器切不对，2-gram 反而更稳。这是另两个项目验证过的做法。
    """
    text = (text or "").lower()
    tokens: list[str] = [w for w in _WORD.findall(text) if len(w) > 1]
    cjk = "".join(_CJK.findall(text))
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i : i + 2])
    if len(cjk) == 1:
        tokens.append(cjk)
    return tokens


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """按长度切块，并尽量切在段落/句子边界上。

    ★ 切在句子边界很重要：从半句话开始的分块，检索出来读着莫名其妙，
      模型也容易接着编。
    """
    size = size or config.CHUNK_CHARS
    overlap = overlap or config.CHUNK_OVERLAP
    text = re.sub(r"\r\n?", "\n", (text or "").strip())
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            # 从后往前找最近的句末/换行
            best = -1
            for sep in ("\n\n", "\n", "。", "！", "？", "；", ". "):
                p = window.rfind(sep)
                if p > size * 0.5:
                    best = max(best, p + len(sep))
            if best > 0:
                end = start + best
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# ══════════════════════════════════════════════════════════════════════
# 索引
# ══════════════════════════════════════════════════════════════════════

def upsert_document(
    *,
    doc_id: str,
    tenant_id: str,
    source: str,
    source_ref: str,
    title: str,
    content: str,
    updated_at: str | None = None,
    permission: str = "external",
) -> dict[str, int]:
    """写入/更新一篇文档。

    ★ 增量同步的三个坑在这里处理：
      · 内容没变 → 跳过（省掉重新分块的开销）
      · 内容变了 → 删掉旧块重新分（★ 不删的话新旧块会同时被检索到，
        模型可能拿到旧版本，这是最隐蔽的一类错误）
      · 文档被删 → 由 sync 层调 delete_document（★ 漏了它，
        删掉的文档会永远留在索引里继续被回答）
    """
    import hashlib

    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
    existing = store.one("SELECT content_hash FROM doc WHERE id=?", (doc_id,))
    if existing and existing["content_hash"] == h:
        store.run("UPDATE doc SET synced_at=? WHERE id=?", (store.now(), doc_id))
        return {"changed": 0, "chunks": 0}

    store.run(
        """INSERT INTO doc (id, tenant_id, source, source_ref, title,
                            updated_at, content_hash, permission, synced_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, updated_at=excluded.updated_at,
             content_hash=excluded.content_hash, permission=excluded.permission,
             synced_at=excluded.synced_at""",
        (doc_id, tenant_id, source, source_ref, title, updated_at, h, permission, store.now()),
    )
    store.run("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
    pieces = chunk_text(content)
    for i, p in enumerate(pieces):
        store.run(
            """INSERT INTO chunk (doc_id, tenant_id, seq, text, created_at)
               VALUES (?,?,?,?,?)""",
            (doc_id, tenant_id, i, p, store.now()),
        )
    return {"changed": 1, "chunks": len(pieces)}


def delete_document(doc_id: str) -> None:
    """★ 删除必须真的删 —— 只标记删除会让旧内容继续被检索到。"""
    store.run("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
    store.run("DELETE FROM doc WHERE id=?", (doc_id,))


# ══════════════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    title: str
    source_ref: str
    text: str
    score: float          # 归一化后的相关度 0~1
    raw: float            # BM25 原分，便于调参
    updated_at: str | None = None
    stale: bool = False

    def citation(self) -> str:
        return f"《{self.title}》" if self.title else (self.source_ref or "资料")


@dataclass
class Retrieval:
    hits: list[Hit] = field(default_factory=list)
    quality: float = 0.0
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    def context(self, limit: int = 4) -> str:
        return "\n\n".join(f"[{h.citation()}]\n{h.text}" for h in self.hits[:limit])


def expand_query(query: str, syn: dict[str, str]) -> str:
    """用同义词/黑话表扩展查询。

    ★ 只做"追加"，不做"替换"：
      把标准说法追加到原查询后面，让两种说法都能命中。
      替换的话，万一映射错了就彻底查不到，风险更大。
    """
    q = query or ""
    extra: list[str] = []
    for colloquial, standard in (syn or {}).items():
        if colloquial and colloquial in q and standard not in q:
            extra.append(standard)
    return q + (" " + " ".join(extra) if extra else "")


def _bm25(
    query_tokens: list[str],
    doc_tokens: list[list[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    n = len(doc_tokens)
    if not n:
        return []
    avg = sum(len(d) for d in doc_tokens) / n or 1.0
    df = Counter()
    for d in doc_tokens:
        for t in set(d):
            df[t] += 1
    scores = [0.0] * n
    for i, d in enumerate(doc_tokens):
        tf = Counter(d)
        dl = len(d) or 1
        s = 0.0
        for t in set(query_tokens):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * dl / avg))
        scores[i] = s
    return scores


def _is_stale(updated_at: str | None) -> bool:
    if not updated_at:
        return False
    try:
        from datetime import datetime, timezone

        s = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).days
        return age > config.STALE_AFTER_DAYS
    except (ValueError, TypeError):
        return False


def search(
    query: str,
    *,
    tenant_id: str = "default",
    top_k: int | None = None,
    permission: str | None = None,
) -> Retrieval:
    """混合检索 + 质量评分。

    permission 表示**看的人有多大的可见范围**，不是"只搜这一种"：
        "external" → 只能看标着 external 的（外部客户）
        "internal" → 能看全部（内部员工）
        None       → 不过滤（后台/审计用）

    ★ 这里第一版写错过：把 "internal" 当成"只搜 internal 文档"，
      结果**内部员工比外部客户看到的还少** —— 完全反了。
      演示资料全都是 external，内部员工一问就是 0 命中，
      而这个问题在只测外部视角时根本发现不了。
    """
    top_k = top_k or config.TOP_K
    q = (query or "").strip()
    if not q:
        return Retrieval(signals={"reason": "空查询"})

    syn = {}
    try:
        from .learning import loop as learning_loop

        syn = learning_loop.synonyms(tenant_id)
    except Exception:  # noqa: BLE001
        syn = {}
    expanded = expand_query(q, syn)

    sql = """SELECT c.id, c.doc_id, c.text, c.seq,
                    d.title, d.source_ref, d.updated_at, d.permission
               FROM chunk c JOIN doc d ON d.id = c.doc_id
              WHERE c.tenant_id = ?"""
    params: list[Any] = [tenant_id]
    if permission == "external":
        # 外部客户：只看标着可对客户说的
        sql += " AND d.permission = 'external'"
    elif permission == "internal":
        # ★ 内部员工：什么都能看（含 external）。
        #   不是"只看 internal" —— 那正好反了。
        pass
    rows = store.rows(sql, tuple(params))
    if not rows:
        return Retrieval(signals={"reason": "索引为空", "chunks": 0})

    # ★ 标题也要进索引。
    #   第一版只拿正文分词，结果搜"报价底线"（那是文档标题）命中不了 ——
    #   而"XX的规定是什么""报销办法在哪个文件"这类问法全靠标题。
    #   标题是作者对内容最凝练的概括，丢掉它等于丢掉最强的检索信号。
    doc_tokens = [tokenize(f"{r['title']} {r['text']}") for r in rows]
    scores = _bm25(tokenize(expanded), doc_tokens)
    mx = max(scores) if scores else 0.0
    if mx <= 0:
        return Retrieval(signals={"reason": "无命中", "chunks": len(rows)})

    hits: list[Hit] = []
    for r, s in zip(rows, scores):
        if s <= 0:
            continue
        norm = s / mx
        stale = _is_stale(r["updated_at"])
        if stale:
            norm *= config.STALE_PENALTY
        hits.append(
            Hit(
                chunk_id=r["id"],
                doc_id=r["doc_id"],
                title=r["title"] or "",
                source_ref=r["source_ref"] or "",
                text=r["text"],
                score=round(norm, 4),
                raw=round(s, 4),
                updated_at=r["updated_at"],
                stale=stale,
            )
        )
    hits.sort(key=lambda h: -h.score)
    hits = hits[:top_k]

    return Retrieval(hits=hits, quality=_quality(hits), signals=_signals(hits))


def _signals(hits: list[Hit]) -> dict[str, Any]:
    if not hits:
        return {"count": 0}
    return {
        "count": len(hits),
        "top": hits[0].score,
        "second": hits[1].score if len(hits) > 1 else 0.0,
        "stale": sum(1 for h in hits if h.stale),
        "distinct_docs": len({h.doc_id for h in hits}),
        # ★ 分差：第一名和第二名贴得很近，说明有歧义，不能太自信
        "gap": round(hits[0].score - (hits[1].score if len(hits) > 1 else 0.0), 4),
    }


def _quality(hits: list[Hit]) -> float:
    """把多个信号合成一个 0~1 的检索质量分。

    ★ 刻意做成**可解释的加权**，而不是黑盒：
      出问题时要能回答"为什么这条没自动发"，客服和客户都想知道。

    三个信号：
      · top   命中够不够强
      · gap   有没有歧义（一二名差距）
      · stale 有没有过期内容混进来（有就重罚）
    """
    if not hits:
        return 0.0
    top = hits[0].score
    gap = hits[0].score - (hits[1].score if len(hits) > 1 else 0.0)
    q = 0.70 * top + 0.30 * min(gap / 0.25, 1.0)
    stale_n = sum(1 for h in hits if h.stale)
    if stale_n:
        # ★ 过期内容混进来就必须压低 —— 否则会出现"检索分很高但答案是旧版本"
        q *= max(0.4, 1.0 - 0.2 * stale_n)
    if len(hits) == 1:
        # 只命中一条时保守一点：可能是凑巧匹配上，而非真的覆盖了这个问题
        q *= 0.9
    return round(min(max(q, 0.0), 1.0), 4)
