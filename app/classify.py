"""接入时的内容预扫描 —— 在 AI 说话之前，先告诉管理员"这份资料有问题"。

★★ 这个模块是被一次测试逼出来的。
    测试里跑了"模型不可用时直接给原文"，结果原文里带着「财务部」三个字，
    出站检查把它拦下了（防线起作用了）。

    但顺着往下想：**如果一篇标着"可对客户说"的文档里出现内部部门名，
    那说明这篇文档的标签本身打错了。**

    这就是我之前提过的第 ⑤ 个设计问题：
    「让客户自己圈出'哪些能对客户说'—— 但很多 SME 的知识库就是混着的，
      客户自己都分不清哪个文档里有什么。」

    所以不能让客户从零去圈。要做的是：
      **接入时先自动扫一遍，生成一份"这些文档里可能有敏感内容"的建议清单，
        让客户确认，而不是让客户凭空回忆。**

★ 这里只做**提示**，不自动改标签。
  自动改标签更危险：扫错了会把该公开的藏起来（客户抱怨答不上来），
  或者把不该公开的放出去（安全事故）。宁可多问一次。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import config, store


@dataclass
class Flag:
    kind: str          # identity / money / commitment / personal / legal
    label: str
    hits: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"{self.label}：{'、'.join(self.hits[:6])}"


# ★ 每一条都对应一种"不该让外部客户看到"的东西。
#   注意这些都是**提示级别**，不是拦截 —— 拦截由 policy.check_outbound 做。
PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("identity", "内部部门/职位", re.compile(
        r"(财务|法务|人事|售后|技术|采购|行政|运营)(部|部门|组|团队)|"
        r"(主管|经理|总监|专员|助理)[，,。\s]|工号"
    )),
    ("money", "价格/折扣/账期", re.compile(
        r"(底价|内部价|成本价|折扣|返点|账期|毛利|净利|利润率|[0-9]+\s*折)"
    )),
    ("commitment", "可能被误解为承诺", re.compile(
        r"(一定|保证|百分百|绝对|承诺|包退|包换|终身)"
    )),
    ("personal", "个人信息", re.compile(
        r"1[3-9]\d{9}|[0-9]{15,18}|\b[\w.+-]+@[\w-]+\.[\w.]+\b|身份证"
    )),
    ("legal", "合同/法务", re.compile(
        r"(违约|赔偿|仲裁|诉讼|起诉|保密协议|竞业)"
    )),
]


def scan_text(text: str) -> list[Flag]:
    out: list[Flag] = []
    for kind, label, pat in PATTERNS:
        hits = sorted({m.group(0).strip() for m in pat.finditer(text or "")})
        if hits:
            out.append(Flag(kind=kind, label=label, hits=hits))
    return out


def scan_doc(doc_id: str) -> list[Flag]:
    """扫一篇文档（拿它所有的块拼起来）。"""
    parts = store.rows("SELECT text FROM chunk WHERE doc_id=? ORDER BY seq", (doc_id,))
    return scan_text("\n".join(p["text"] for p in parts))


def audit(tenant_id: str = "default", limit: int = 200) -> dict[str, Any]:
    """扫全部文档，找出"标着可公开、但内容里有敏感东西"的那些。

    ★ 重点看的是 **external 文档里出现内部信息** 这种情况：
      因为 internal 文档本来就不会发给客户，扫出来也只是提醒。
      而 external 文档是**真的会被客户看到**的。
    """
    docs = store.rows(
        "SELECT id, title, source_ref, permission FROM doc WHERE tenant_id=? LIMIT ?",
        (tenant_id, limit),
    )
    risky: list[dict[str, Any]] = []
    clean = 0
    for d in docs:
        flags = scan_doc(d["id"])
        if not flags:
            clean += 1
            continue
        risky.append(
            {
                "doc_id": d["id"],
                "title": d["title"] or d["source_ref"],
                "permission": d["permission"],
                # ★ 只有 external 的才算"高危"：它会真的发给客户
                "severe": d["permission"] == "external",
                "flags": flags,
            }
        )
    risky.sort(key=lambda r: (not r["severe"], r["title"]))
    return {
        "total": len(docs),
        "clean": clean,
        "risky": risky,
        "severe_count": sum(1 for r in risky if r["severe"]),
    }


def advice() -> str:
    """给管理员的一段人话建议。

    ★ 措辞刻意不用"警告/禁止"，用"你看一下"：
      这些是提示不是判决，扫错很正常（比如"经理"可能只是正文里一句闲聊）。
    """
    return (
        "下面这些资料里出现了可能不该对外说的内容。\n"
        "标着「可对客户说」的尤其要看一眼 —— 那些是会真的发给客户的。\n"
        "你可以：把这几个词改成通用说法（比如「财务部」→「我们」）、"
        "或者把这篇的标签改成「仅内部可见」。\n"
        "这只是提示，扫错很正常（比如正文里随口提到一个词），你判断。"
    )
