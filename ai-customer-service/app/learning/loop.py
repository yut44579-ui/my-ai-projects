"""学习闭环 —— "越用越聪明"的真正实现。

★★ 核心判断（这条如果搞错，整个产品方向就错了）：
    "越用越聪明"**主要不是靠训练模型**，而是靠四件事：

      ① 没答上来的问题 → 沉淀成待补充列表 → 人补一条 → **秒级生效**
      ② 检索没命中的   → 补同义词/黑话       → **分钟级生效**
      ③ 答了但被人改过 → 变成风格示例        → **下次请求就生效**
      ④ 积累够了       → 微调                → 几小时到几天，且装不了事实

    ①②③ 贡献了"变聪明"的绝大部分，而且几乎不花钱、不花时间。
    ④ 只在风格和判定这两件事上补刀。

★★ 第二核心判断：**没有验证的自我进化是放大器，不是进步。**
    人工随手改的一条草稿，如果直接当成"标准答案"喂回去，
    AI 就会把那个人的临时口味学成规则 → 用的人越多，歪得越厉害。
    所以每次学习都必须过评测集，确认"准确率是涨了还是跌了"。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import store

# 失败原因（与 store.qa_log.failure_reason 的取值一致）
FAILURE_LABELS = {
    "no_hit": "检索没命中（缺知识）",
    "stale": "命中了但过期（缺时效）",
    "bad_style": "答了但不像人话（缺风格示例）",
    "wrong_fact": "答错了（事实约束不够）",
    "should_escalate": "本来就不该答（判定错了）",
    "banned": "命中了禁用内容（安全问题）",
    "no_answer": "模型没给出可用回答",
}

# 四类失败 → 四种完全不同的修法。
# ★ 不分清这个，"学习"就是瞎改：缺知识的去改话术，永远修不好。
FIX_BY_REASON = {
    "no_hit": "补知识：把这个问题加进知识库，或加同义词让检索能命中",
    "stale": "补时效：给文档加日期/有效期，或把旧版本下架",
    "bad_style": "补风格：把人工改后的说法加进风格示例",
    "wrong_fact": "加固事实：检查生成时的约束，或该问题改为「原文直出」",
    "should_escalate": "改判定：把这类问题加进转人工规则",
    "banned": "改安全：把命中的词加进禁用表，并检查为什么没拦住",
    "no_answer": "查模型：看是提示词问题还是模型降级了",
}

_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】~～\-—_]+")
_FILLER = re.compile(r"^(请问|你好|您好|麻烦问一下|麻烦|我想问一下|想问下|问下|那个|这个)+")


def normalize_question(q: str) -> str:
    """把口语问题归一化，用于把"同一个问题"的多次提问合并成一条。

    ★ 为什么要归一化：客户问"报消单咋填"和"报销单怎么填"是同一件事，
      不合并的话待补充列表会被同一类问题刷屏，管理员根本看不出重点。
    ★ 但只做轻量归一化（去标点、去开头客套）。做重了会把不同问题合并，
      反而更糟——宁可合并少了，不可合并错了。
    """
    s = _PUNCT.sub("", (q or "").strip())
    s = _FILLER.sub("", s)
    return s.lower()


@dataclass
class FailureBreakdown:
    total: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)
    by_decision: dict[str, int] = field(default_factory=dict)

    @property
    def answered_ok(self) -> int:
        return self.by_outcome.get("sent", 0) + self.by_outcome.get("accepted", 0)

    @property
    def hit_rate(self) -> float:
        if not self.total:
            return 0.0
        return self.answered_ok / self.total

    def lines(self) -> list[str]:
        out = [
            f"共 {self.total} 条问答",
            f"  自动发/直接可用 : {self.answered_ok} 条（{self.hit_rate:.0%}）",
        ]
        for k, v in sorted(self.by_outcome.items(), key=lambda x: -x[1]):
            out.append(f"  结果 {k:<12} : {v}")
        if self.by_reason:
            out.append("  失败原因：")
            for k, v in sorted(self.by_reason.items(), key=lambda x: -x[1]):
                out.append(f"    {FAILURE_LABELS.get(k, k):<22} {v} 条  → {FIX_BY_REASON.get(k, '')}")
        return out


def analyze(tenant_id: str = "default", days: int = 30) -> FailureBreakdown:
    """统计这段时间的问答结果 + **失败原因分布**。

    ★ 失败原因分布比成功率重要得多：
      成功率只告诉你"好不好"，原因分布才告诉你"该修哪里"。
    """
    since = f"-{int(days)} days"
    b = FailureBreakdown()
    rows = store.rows(
        """SELECT outcome, failure_reason, decision, COUNT(*) n
             FROM qa_log
            WHERE tenant_id=?
              AND created_at >= datetime('now', ?)
            GROUP BY outcome, failure_reason, decision""",
        (tenant_id, since),
    )
    for r in rows:
        n = r["n"]
        b.total += n
        b.by_outcome[r["outcome"] or "pending"] = b.by_outcome.get(r["outcome"] or "pending", 0) + n
        if r["failure_reason"]:
            b.by_reason[r["failure_reason"]] = b.by_reason.get(r["failure_reason"], 0) + n
        if r["decision"]:
            b.by_decision[r["decision"]] = b.by_decision.get(r["decision"], 0) + n
    return b


# ══════════════════════════════════════════════════════════════════════
# ① 没答上来 → 待补充问题
# ══════════════════════════════════════════════════════════════════════

def record_gap(tenant_id: str, question: str) -> None:
    """当场把一个答不上来的问题记进"待补充列表"。

    ★ 为什么是"当场"而不是等每日批量任务：
      答不上来是最该被立刻知道的事。等批量任务的话，
      管理员要第二天才看到，而这时候可能已经被问了二十遍。
      这条 SQL 就是一次 UPSERT，成本可以忽略。
    """
    norm = normalize_question(question)
    if not norm:
        return
    existing = store.one(
        "SELECT id FROM gap_item WHERE tenant_id=? AND question_norm=?",
        (tenant_id, norm),
    )
    if existing:
        store.run(
            "UPDATE gap_item SET occurrences = occurrences + 1, last_seen_at=? WHERE id=?",
            (store.now(), existing["id"]),
        )
    else:
        store.run(
            """INSERT INTO gap_item
                 (tenant_id, question_norm, sample_question, occurrences,
                  first_seen_at, last_seen_at, status)
               VALUES (?,?,?,1,?,?,'open')""",
            (tenant_id, norm, question, store.now(), store.now()),
        )


def mine_gaps(tenant_id: str = "default", days: int = 30) -> dict[str, int]:
    """把"没答上来"和"答错了"的问题沉淀成待补充列表。

    ★ 关键点：**按归一化后的问题合并计数**。
      同一个人反复问、或者很多人问同一件事，次数越高越该先补。
    """
    since = f"-{int(days)} days"
    rows = store.rows(
        """SELECT question_raw, failure_reason, created_at
             FROM qa_log
            WHERE tenant_id=?
              AND created_at >= datetime('now', ?)
              AND (outcome IN ('unanswered','rejected','escalated')
                   OR failure_reason IS NOT NULL)""",
        (tenant_id, since),
    )
    created = 0
    for r in rows:
        norm = normalize_question(r["question_raw"])
        if not norm:
            continue
        existing = store.one(
            "SELECT id, occurrences FROM gap_item WHERE tenant_id=? AND question_norm=?",
            (tenant_id, norm),
        )
        if existing:
            store.run(
                """UPDATE gap_item
                      SET occurrences = occurrences + 1, last_seen_at = ?
                    WHERE id=?""",
                (store.now(), existing["id"]),
            )
        else:
            store.run(
                """INSERT INTO gap_item
                     (tenant_id, question_norm, sample_question, occurrences,
                      first_seen_at, last_seen_at, status)
                   VALUES (?,?,?,1,?,?,'open')""",
                (tenant_id, norm, r["question_raw"], store.now(), store.now()),
            )
            created += 1
    return {"scanned": len(rows), "created": created}


def open_gaps(tenant_id: str = "default", limit: int = 30) -> list[dict[str, Any]]:
    """待补充问题清单（按被问次数排序）。

    ★ 这份清单是要直接推给管理员的：
      「这 10 个问题被问了 47 次，要不要补一下？」
      这是产品"越用越好"的飞轮入口——没有它，覆盖率永远停在原地。
    """
    return store.rows(
        """SELECT id, sample_question, occurrences, first_seen_at, last_seen_at
             FROM gap_item
            WHERE tenant_id=? AND status='open'
            ORDER BY occurrences DESC, last_seen_at DESC
            LIMIT ?""",
        (tenant_id, limit),
    )


def resolve_gap(gap_id: int, *, status: str, doc_id: str | None = None) -> None:
    store.run(
        "UPDATE gap_item SET status=?, resolved_doc_id=? WHERE id=?",
        (status, doc_id, gap_id),
    )


# ══════════════════════════════════════════════════════════════════════
# ③ 答了但被改 → 风格示例
# ══════════════════════════════════════════════════════════════════════

def mine_style_examples(
    tenant_id: str = "default", days: int = 30, min_delta: int = 8
) -> dict[str, int]:
    """把"人工改过的草稿"变成风格示例。

    ★ 这是整个学习闭环里**最划算的一环**：
      · 数据零成本（人工本来就要改）
      · 效果来得极快（下次请求就生效）
      · 学到的是**这家公司自己的说法**，不是通用客套

    ★ 但有个过滤器：改动太小（比如只加了个标点）不值得当示例，
      否则示例库里全是噪音。min_delta 就是干这个的。
    """
    since = f"-{int(days)} days"
    rows = store.rows(
        """SELECT id, question_raw, answer_text, edited_to
             FROM qa_log
            WHERE tenant_id=?
              AND created_at >= datetime('now', ?)
              AND outcome='edited'
              AND edited_to IS NOT NULL AND edited_to <> ''
              AND answer_text IS NOT NULL""",
        (tenant_id, since),
    )
    added = 0
    skipped_same = 0
    for r in rows:
        before, after = r["answer_text"], r["edited_to"]
        # ★ 去掉出处尾巴再比。人工发出去时通常不带出处，
        #   不去掉的话每次改稿都会被当成"改了"，示例库全是噪音。
        if _strip_citation(before) == _strip_citation(after):
            skipped_same += 1
            continue
        if abs(len(_strip_citation(after)) - len(_strip_citation(before))) < min_delta:
            skipped_same += 1
            continue
        try:
            store.run(
                """INSERT OR IGNORE INTO style_example
                     (tenant_id, question, answer_before, answer_after,
                      diff_summary, enabled, weight, created_at)
                   VALUES (?,?,?,?,?,1,1.0,?)""",
                (
                    tenant_id,
                    r["question_raw"],
                    before,
                    after,
                    _diff_summary(before, after),
                    store.now(),
                ),
            )
            added += 1
        except Exception:  # noqa: BLE001 —— 单条失败不该中断整轮挖掘
            continue
    return {"scanned": len(rows), "added": added, "skipped_same": skipped_same}


def _strip_citation(text: str) -> str:
    """去掉「〔出处：…〕」这类**程序加的**尾巴，再比较。

    ★ 为什么必须去：出站检查要求回答带出处，人工发出去时往往不带
      （他就在聊天窗口里回一句，没人会把出处打给客户）。
      如果不去掉，每次人工改稿都会被判定成"改短了、去掉了书名号引用"，
      风格示例库就全是这种噪音 —— 学不到真正的措辞差别。
    """
    if not text:
        return ""
    out = re.sub(r"[〔\[]\s*出处[：:][^〕\]]*[〕\]]", "", text)
    out = re.sub(r"[〔\[]\s*来源[：:][^〕\]]*[〕\]]", "", out)
    return out.strip()


def _diff_summary(before: str, after: str) -> str:
    """用程序判断"改了什么"，而不是让模型猜。

    ★ 这一步刻意做成确定性的：改动的性质是可判断的，
      交给模型描述反而会引入不确定性，而且这里不需要"文采"。
    """
    before = _strip_citation(before)
    after = _strip_citation(after)
    bits: list[str] = []
    if len(after) < len(before) * 0.7:
        bits.append("改短了")
    elif len(after) > len(before) * 1.3:
        bits.append("改长了")
    if "\n" in before and "\n" not in after:
        bits.append("去掉了分点")
    if len(before.splitlines()) > len(after.splitlines()) + 1:
        bits.append("合并成了整段")
    for w in ("您好", "感谢您的咨询", "如有", "请随时", "根据《"):
        if w in before and w not in after:
            bits.append(f"去掉了「{w}」")
    return "；".join(bits) or "措辞调整"


def style_examples(tenant_id: str = "default", limit: int = 8) -> list[dict[str, Any]]:
    """取风格示例（放进提示词的 few-shot）。按权重和新鲜度排。

    ★ 只取 status='active' 的 —— 也就是**过了评测关卡**的。
      没过关的（pending/rejected）绝对不能进提示词，
      否则一条噪音示例会污染之后所有回答，而且你事后查不出是哪条带偏的。
    """
    return store.rows(
        """SELECT question, answer_after, diff_summary
             FROM style_example
            WHERE tenant_id=? AND enabled=1 AND status='active'
            ORDER BY weight DESC, created_at DESC
            LIMIT ?""",
        (tenant_id, limit),
    )


# ══════════════════════════════════════════════════════════════════════
# ② 检索没命中 → 同义词 / 黑话候选
# ══════════════════════════════════════════════════════════════════════

def missing_term_report(tenant_id: str = "default", days: int = 30) -> list[dict[str, Any]]:
    """列出"检索没命中"的高频问题，供人工补同义词。

    ★ 这里刻意**不自动生成同义词**：
      口语→标准词的映射有歧义（"打款"可能是付款也可能是收款），
      自动猜错会让检索把不相关的文档拉进来，比不命中更糟。
      所以只给候选，让人确认。
    """
    since = f"-{int(days)} days"
    rows = store.rows(
        """SELECT question_raw, COUNT(*) n
             FROM qa_log
            WHERE tenant_id=? AND created_at >= datetime('now', ?)
              AND failure_reason IN ('no_hit','stale')
            GROUP BY question_norm_hint
            ORDER BY n DESC LIMIT 40""".replace(
            "question_norm_hint", "question_raw"
        ),
        (tenant_id, since),
    )
    out: list[dict[str, Any]] = []
    have = {
        r["colloquial"]
        for r in store.rows("SELECT colloquial FROM synonym WHERE tenant_id=?", (tenant_id,))
    }
    for r in rows:
        q = (r["question_raw"] or "").strip()
        if not q or q in have:
            continue
        out.append({"question": q, "count": r["n"]})
    return out


def add_synonym(tenant_id: str, colloquial: str, standard: str, source: str = "manual") -> int:
    return store.run(
        """INSERT OR IGNORE INTO synonym
             (tenant_id, colloquial, standard, source, created_at)
           VALUES (?,?,?,?,?)""",
        (tenant_id, colloquial.strip(), standard.strip(), source, store.now()),
    )


def synonyms(tenant_id: str = "default") -> dict[str, str]:
    """取同义词表（检索时拿它做查询扩展）。

    ★ 只取 status='active' 的。同义词加错的后果比"检索不命中"更严重：
      它会把不相关的文档拉进来，模型拿着错的资料答得很自信。
    """
    return {
        r["colloquial"]: r["standard"]
        for r in store.rows(
            "SELECT colloquial, standard FROM synonym WHERE tenant_id=? AND status='active'",
            (tenant_id,),
        )
    }


def pending_count(tenant_id: str = "default") -> dict[str, int]:
    """还没过验证的学习产物有多少。★ 界面上要显示它 ——
    否则管理员不知道"系统学到了东西但还没生效"。"""
    s = store.one(
        "SELECT COUNT(*) n FROM style_example WHERE tenant_id=? AND status='pending'", (tenant_id,)
    )
    y = store.one(
        "SELECT COUNT(*) n FROM synonym WHERE tenant_id=? AND status='pending'", (tenant_id,)
    )
    return {"styles": (s or {}).get("n", 0), "synonyms": (y or {}).get("n", 0)}


# ══════════════════════════════════════════════════════════════════════
# ★★ 验证关卡 —— 没有这一步，自我进化就是放大器
# ══════════════════════════════════════════════════════════════════════

@dataclass
class EvalResult:
    total: int
    passed: int
    failed: int
    failures: list[dict[str, Any]]

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def eval_cases(tenant_id: str = "default") -> list[dict[str, Any]]:
    return store.rows(
        """SELECT id, question, must_contain, must_not_contain, expect_decision, note
             FROM eval_case WHERE tenant_id=? AND enabled=1""",
        (tenant_id,),
    )


def check_answer(
    case: dict[str, Any], answer: str, decision: str | None = None
) -> tuple[bool, list[str]]:
    """单条评测：要点必须出现、禁用词必须不出现、判定要符合预期。

    ★ must_not_contain 是最重要的一类：
      它专门抓"答得挺像样，但说了不该说的"——
      比如泄漏了内部部门名、做了超范围承诺。
      这类错误用户不会投诉（他不知道自己看到了不该看的），
      只能靠这条断言来防。
    """
    problems: list[str] = []
    text = answer or ""
    for kw in store.jload(case.get("must_contain"), []) or []:
        if kw and kw not in text:
            problems.append(f"缺少要点「{kw}」")
    for kw in store.jload(case.get("must_not_contain"), []) or []:
        if kw and kw in text:
            problems.append(f"出现禁用词「{kw}」")
    exp = case.get("expect_decision")
    if exp and decision and exp != decision:
        problems.append(f"判定应为 {exp}，实际 {decision}")
    return (not problems), problems
