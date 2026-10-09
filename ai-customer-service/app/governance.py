"""治理层：急停开关 + 影子模式 + 后果预览。

★★ 这三样是"客户敢不敢开自动"的决定因素，不是锦上添花。

    客户的真实心理：
      "万一它说错话了怎么办？"  → 需要**急停开关**（出事时那个能按的按钮）
      "我怎么知道它答得对不对？" → 需要**影子模式**（先只算不发，拿数据说话）
      "调了会怎样？"            → 需要**后果预览**（调旋钮时看到数据）

    没有这三样，客户只敢一直用"全部转人工" ——
    那这套系统就等于没上。

★ 一个关键的实现判断：**后果预览不需要调模型。**
    因为每次问答的检索质量分已经存在 qa_log 里了。
    拿历史质量分按新门槛重放一遍，就能算覆盖率变化 ——
    零成本、瞬时、而且是**这家公司自己的历史数据**，不是估算。
    这条让"调旋钮看后果"从"很贵"变成"免费"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import policy, store

# ══════════════════════════════════════════════════════════════════════
# 一、急停开关
# ══════════════════════════════════════════════════════════════════════

# ★ 平台强制的底线之一（见决策记录 D5）：
#   **开关必须存在，客户不能关掉这个功能本身。**
#   但"要不要按"是客户的权力 —— 我们只保证他随时能按、且按了立刻生效。
STOP_KEY = "emergency_stop"


def is_stopped(tenant_id: str = "default") -> bool:
    row = store.one(
        "SELECT value_json FROM platform_policy WHERE key=?",
        (f"{STOP_KEY}:{tenant_id}",),
    )
    return bool(store.jload((row or {}).get("value_json"), False))


def set_stop(
    tenant_id: str = "default",
    *,
    on: bool,
    by: str = "unknown",
    reason: str = "",
) -> dict[str, Any]:
    """按/松急停。★ 每次都要留痕：谁、什么时候、为什么。

    ★ 为什么必须留痕：急停是"出事时"才会按的按钮。
      事后复盘时，"谁在什么时候按的、当时以为什么"是**唯一能还原现场的东西**。
      没有它，一次事故会变成互相猜。
    """
    store.run(
        """INSERT INTO platform_policy (key, value_json, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                          updated_at=excluded.updated_at""",
        (f"{STOP_KEY}:{tenant_id}", store.jdump(bool(on)), store.now()),
    )
    store.run(
        """INSERT INTO stop_log (tenant_id, action, by_who, reason, created_at)
           VALUES (?,?,?,?,?)""",
        (tenant_id, "stop" if on else "resume", by, reason, store.now()),
    )
    return {
        "stopped": bool(on),
        "message": (
            "已急停：所有自动发送立即停止，全部转为待人工处理。"
            if on
            else "已恢复：自动发送重新生效（门槛仍按当前档位）。"
        ),
    }


def stop_history(tenant_id: str = "default", limit: int = 20) -> list[dict[str, Any]]:
    return store.rows(
        """SELECT action, by_who, reason, created_at FROM stop_log
            WHERE tenant_id=? ORDER BY id DESC LIMIT ?""",
        (tenant_id, limit),
    )


# ══════════════════════════════════════════════════════════════════════
# 二、影子模式
# ══════════════════════════════════════════════════════════════════════

SHADOW_KEY = "shadow_mode"


def is_shadow(tenant_id: str = "default") -> bool:
    row = store.one(
        "SELECT value_json FROM platform_policy WHERE key=?",
        (f"{SHADOW_KEY}:{tenant_id}",),
    )
    return bool(store.jload((row or {}).get("value_json"), False))


def set_shadow(tenant_id: str = "default", *, on: bool) -> dict[str, Any]:
    """开/关影子模式。

    ★ 影子模式是**让客户敢开自动的唯一路径**：
      先跑两周，AI 只算不发，人工照常回。
      然后拿"AI 会怎么答"和"人实际怎么答"比 ——
      如果 80% 一致，客户自己就会说"你开吧"。
      这比任何演示都有说服力，因为用的是他自己的数据。
    """
    store.run(
        """INSERT INTO platform_policy (key, value_json, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                          updated_at=excluded.updated_at""",
        (f"{SHADOW_KEY}:{tenant_id}", store.jdump(bool(on)), store.now()),
    )
    return {
        "shadow": bool(on),
        "message": (
            "已开影子模式：AI 照常算，但一条都不会发出去，人工照常回。"
            "跑一段时间后看下面的对照数据。"
            if on
            else "已关影子模式：恢复正常的自动/起草判定。"
        ),
    }


@dataclass
class ShadowReport:
    total: int = 0                 # 影子期间记录了多少条
    with_human: int = 0            # 其中人工给出了实际回答的
    fact_match: int = 0            # AI 的事实能被人工作答覆盖的
    would_auto: int = 0            # 按当前门槛，AI 本来会直接发出去的
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """如果开自动，有多大比例不用人管。"""
        return self.would_auto / self.total if self.total else 0.0

    @property
    def match_rate(self) -> float:
        """在有对照的样本里，AI 的事实和人工答案对上的比例。

        ★ 这是影子模式**唯一的产出指标**，也是给客户看的那句话：
          "你看，AI 和你们客服 78% 是一致的。"
        """
        return self.fact_match / self.with_human if self.with_human else 0.0

    def line(self) -> str:
        if not self.total:
            return "影子模式还没有数据。开一段时间再看。"
        if not self.with_human:
            return (
                f"影子期间记录了 {self.total} 条，但还没有人工回答可以对照。"
                f"（AI 本来会直接发出去的有 {self.would_auto} 条，"
                f"占 {self.coverage:.0%}）"
            )
        return (
            f"影子期间 {self.total} 条，其中 {self.with_human} 条有人工回答可对照；"
            f"AI 的事实和人工答案对上的有 {self.fact_match} 条（{self.match_rate:.0%}）。"
            f"按当前门槛，AI 本来会直接发出 {self.would_auto} 条（{self.coverage:.0%}）。"
        )


def _facts(text: str) -> list[str]:
    """从一段话里抽出"具体事实"（数字、时间、天数…）。

    ★ 用事实重合度而不是文本相似度来衡量"AI 答得对不对"：
      人工发出去的话往往和 AI 草稿措辞差很远（他改成了自己的说法），
      但**事实应该是一样的**。用文本相似度会得出"几乎都不一致"的错误结论。
      （这和评测集里 must_contain 的抽取逻辑是同一个判断。）
    """
    from .learning.eval import extract_facts

    return extract_facts(text)


def shadow_report(tenant_id: str = "default", days: int = 30) -> ShadowReport:
    """影子模式对照报告。

    ★ 怎么判断"AI 答对了"：看人工最终发出去的那句话里，
      **有没有覆盖 AI 答案里的具体事实**。
      覆盖了 → 说明 AI 的事实没错（哪怕措辞不同）
      没覆盖 → 要么 AI 答漏了，要么答错了
    """
    rep = ShadowReport()
    rows = store.rows(
        """SELECT id, question_raw, answer_text, quality, decision,
                  would_be_decision, edited_to, outcome
             FROM qa_log
            WHERE tenant_id=?
              AND created_at >= datetime('now', ?)
              AND decision IN ('shadow','draft','auto')
            ORDER BY id DESC LIMIT 300""",
        (tenant_id, f"-{int(days)} days"),
    )
    from .learning.eval import extract_facts

    for r in rows:
        rep.total += 1
        # ★ 用落库的 would_be_decision，而不是拿 quality 重新推。
        #   重推会漏掉意图命中（报价/投诉）和出站检查拦下的那些，
        #   把"本来会直接发"算多 —— 而客户正是拿这个数决定要不要开自动的。
        wb = r["would_be_decision"] or r["decision"]
        if wb == "auto":
            rep.would_auto += 1
        # 人工最终的版本：优先用 edited_to，没有就用它自己发的（accepted）
        human = r["edited_to"] or (r["answer_text"] if r["outcome"] == "accepted" else None)
        if not human:
            continue
        rep.with_human += 1
        ai_facts = extract_facts(r["answer_text"] or "")
        if not ai_facts:
            continue
        hit = [f for f in ai_facts if f in human]
        if hit:
            rep.fact_match += 1
        if len(rep.examples) < 8:
            rep.examples.append(
                {
                    "question": r["question_raw"],
                    "ai": (r["answer_text"] or "")[:150],
                    "human": human[:150],
                    "matched": hit,
                    "missed": [f for f in ai_facts if f not in human],
                }
            )
    return rep


def _tier(tenant_id: str) -> str:
    row = store.one("SELECT tier FROM tenant_config WHERE tenant_id=?", (tenant_id,))
    return (row or {}).get("tier") or "standard"


# ══════════════════════════════════════════════════════════════════════
# 三、后果预览
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Preview:
    total: int = 0
    auto: int = 0
    draft: int = 0
    escalate: int = 0
    tier: str = "standard"
    auto_threshold: float = 0.0
    draft_threshold: float = 0.0

    @property
    def coverage(self) -> float:
        return self.auto / self.total if self.total else 0.0

    @property
    def human_load(self) -> int:
        """还需要人处理的量。★ 客户最关心的其实是这个 ——
        他只有 2 个客服，覆盖率再高，人处理不过来也白搭。"""
        return self.draft + self.escalate

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "auto": self.auto,
            "draft": self.draft,
            "escalate": self.escalate,
            "coverage": round(self.coverage, 4),
            "human_load": self.human_load,
            "tier": self.tier,
            "auto_threshold": self.auto_threshold,
            "draft_threshold": self.draft_threshold,
        }


def preview(
    tenant_id: str = "default",
    *,
    tier: str | None = None,
    auto_threshold: float | None = None,
    draft_threshold: float | None = None,
    days: int = 30,
) -> Preview:
    """★ 拿历史数据重放，算"如果换成这套门槛会怎样"。

    ★★ 零成本：**不调模型**。
      质量分已经存在 qa_log 里了，重放只是把历史质量分按新门槛重新分一次类。
      所以界面上可以做到"拖滑块实时看数字"。

    ★ 为什么这很关键：客户不敢调门槛，是因为**看不到后果**。
      "自动发送覆盖率 65% → 58%，但每天转人工从 23 条涨到 41 条 ——
       你只有 2 个客服，够吗？"
      有了这句话，他才知道该往哪调。
    """
    t = tier or _tier(tenant_id)
    auto_t, draft_t = policy.tier_thresholds(t)
    if auto_threshold is not None:
        auto_t = auto_threshold
    if draft_threshold is not None:
        draft_t = draft_threshold

    p = Preview(tier=t, auto_threshold=auto_t, draft_threshold=draft_t)
    rows = store.rows(
        """SELECT quality, intent, decision FROM qa_log
            WHERE tenant_id=? AND created_at >= datetime('now', ?)
              AND quality IS NOT NULL""",
        (tenant_id, f"-{int(days)} days"),
    )
    for r in rows:
        p.total += 1
        # ★ 意图转人工优先于分数 —— 和线上判定顺序保持一致。
        #   不一致的话，预览出来的数字和实际行为对不上，这个功能就骗人了。
        if r["intent"]:
            p.escalate += 1
        elif (r["quality"] or 0) >= auto_t:
            p.auto += 1
        elif (r["quality"] or 0) >= draft_t:
            p.draft += 1
        else:
            p.escalate += 1
    return p


def compare_preview(tenant_id: str = "default", *, tier: str, days: int = 30) -> dict[str, Any]:
    """对比"现在"和"换成某个档位"的区别。★ 界面上的那一块数字就是它。"""
    now = preview(tenant_id, days=days)
    after = preview(tenant_id, tier=tier, days=days)

    def delta(a: int, b: int) -> dict[str, Any]:
        d = b - a
        pct = (d / a) if a else 0.0
        return {"from": a, "to": b, "delta": d, "pct": round(pct, 4)}

    warn = ""
    if after.human_load > now.human_load and now.human_load:
        up = (after.human_load - now.human_load) / now.human_load
        if up >= 0.3:
            warn = (
                f"转到人工的量会增加 {up:.0%}"
                f"（每天 {now.human_load} 条 → {after.human_load} 条），"
                f"先确认客服人手够。"
            )
    if not now.total:
        warn = "还没有足够的历史数据可以推算，先跑一段时间再来调。"

    return {
        "now": now.as_dict(),
        "after": after.as_dict(),
        "coverage": delta(now.auto, after.auto),
        "draft": delta(now.draft, after.draft),
        "escalate": delta(now.escalate, after.escalate),
        "warning": warn,
        "basis": f"按过去 {days} 天的 {now.total} 条真实问答推算",
    }
