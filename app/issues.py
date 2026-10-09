"""问题上报：客服/销售 → 技术。

★★ 这个模块存在的理由（用户的原始需求）：

    「加一个远程提醒类似的功能，就是可以随时提醒技术开发人员报错、有 bug、
      需要去改（这里最好是要填写问题、问题类型、具体错误原因等）。
      因为使用这套系统的通常都是不怎么会用 AI 的小白销售或者客服，
      只会去回复话术。点了这个就会有关于技术人员专门处理这些问题的界面。
      开发人员专门在这里处理问题，最后提交问题，提交完之后客服或者销售
      这里的能够知道 —— 就这种设计逻辑。」

★★ 所以这个模块的设计核心只有一条：**报的人不需要懂技术。**

    小白客服面对一个 AI 答错的回答，他能提供的信息只有：
      "这答得不对" —— 就这样。

    如果表单要求他填"错误堆栈""复现步骤""请求参数"，
    结果只有一个：**他不会填，于是这个问题永远不被上报**，
    然后他会在微信里私聊技术，或者干脆不管了。

    ★ 所以：
      他填的  = 哪儿不对（下拉）+ 一句话描述（可选）
      系统填的 = 哪个会话、哪句问答、AI 当时怎么判的、
                检索质量多少、命中了什么、有没有报错、在哪个界面点的

★★ 另一条：**闭环必须由报的人确认，不能由技术自己宣布修好了。**

    技术说"修好了"但他改的地方和用户遇到的问题不是一回事 —— 这种事极其常见。
    所以状态是：
        new → working → resolved（技术提交）
                          ↓
                  reporter_ack = ok  →  closed（真的关了）
                  reporter_ack = no  →  working（打回去，带着他的补充说明）
"""

from __future__ import annotations

from typing import Any

from . import store

# ══════════════════════════════════════════════════════════════════════
# 类型和影响面：**用小白能懂的话，不是技术术语**
# ══════════════════════════════════════════════════════════════════════

ISSUE_KINDS = [
    ("wrong_answer", "答错了", "AI 说的和资料对不上，或者明显不对"),
    ("no_answer", "答不上来", "明明有资料，它却说不知道"),
    ("error", "点了没反应 / 报错", "页面打不开、点了没反应、弹出错误"),
    ("slow", "太慢", "等很久才回，或者一直转圈"),
    ("leak", "说了不该说的", "把内部的东西说出去了，或者语气不对"),
    ("format", "格式不对", "出处显示错了、排版乱了、话术很奇怪"),
    ("cant_use", "不会用", "这个功能我不知道怎么操作"),
    ("other", "其他", "上面都不是，我描述一下"),
]

SEVERITIES = [
    ("low", "不影响使用", "就是想提一下"),
    ("normal", "影响我干活", "这次得绕过它才能继续"),
    ("high", "客户已经看到了", "客户那边已经收到错的了，需要尽快处理"),
]

STATUS_LABEL = {
    "new": "待处理",
    "working": "处理中",
    "resolved": "已解决（等确认）",
    "closed": "已关闭",
    "rejected": "不是问题",
}

RESOLUTION_KINDS = [
    ("kb_missing", "知识库缺资料 —— 已让客户补"),
    ("kb_wrong", "知识库内容有错 —— 已改"),
    ("code_bug", "代码 bug —— 已修"),
    ("config", "配置问题 —— 已调"),
    ("usage", "使用方式问题 —— 已教他怎么用"),
    ("platform", "平台/网络问题 —— 已处理"),
    ("wontfix", "暂不处理（说明理由）"),
]


# ══════════════════════════════════════════════════════════════════════
# ★★ 自动采集现场：这是整个模块最有价值的部分
# ══════════════════════════════════════════════════════════════════════

def collect_context(
    *,
    qa_log_id: int | None = None,
    user_ref: str | None = None,
    page: str = "",
    note: str = "",
) -> dict[str, Any]:
    """自动把"现场"收集起来。

    ★ 报的人一个字都不用填，这些东西由系统抓。

    抓什么（按重要性排）：
      ① 那一条问答的完整记录 —— AI 判成了哪一档、为什么、检索质量、
         命中了哪些资料、原始问题和改写后的检索词
      ② 这个用户前后的对话 —— 只给一条问答，技术看不出上下文
      ③ 最近的服务端报错 —— 如果他说"点了报错"，日志就是关键
    """
    ctx: dict[str, Any] = {"page": page, "note": note}

    # ① 那一条问答
    if qa_log_id:
        row = store.one(
            """SELECT id, tenant_id, channel, user_ref, question_raw, question_rewrite,
                      quality, decision, would_be_decision, intent, failure_reason,
                      answer_text, retrieved_json, citations_json, signals_json,
                      latency_ms, tokens_in, tokens_out, created_at
                 FROM qa_log WHERE id=?""",
            (qa_log_id,),
        )
        if row:
            ctx["qa"] = {
                "id": row["id"],
                "channel": row["channel"],
                "user": row["user_ref"],
                "question": row["question_raw"],
                "rewrite": row["question_rewrite"],
                "answer": (row["answer_text"] or "")[:800],
                "decision": row["decision"],
                "would_be": row["would_be_decision"],
                "intent": row["intent"],
                "failure_reason": row["failure_reason"],
                "quality": row["quality"],
                "latency_ms": row["latency_ms"],
                "tokens": (row["tokens_in"] or 0) + (row["tokens_out"] or 0),
                "at": row["created_at"],
            }
            # 命中了什么（技术最需要的：是不是检索就没找到）
            try:
                import json

                hits = json.loads(row["retrieved_json"] or "[]")
                ctx["hits"] = [
                    {"doc": h.get("doc") or h.get("title"), "score": h.get("score"),
                     "text": (h.get("text") or "")[:180]}
                    for h in hits[:5]
                ]
            except Exception:  # noqa: BLE001
                ctx["hits"] = []
            try:
                import json

                ctx["citations"] = json.loads(row["citations_json"] or "[]")
            except Exception:  # noqa: BLE001
                ctx["citations"] = []
            if not user_ref:
                user_ref = row["user_ref"]

    # ② 这个用户前后的对话 —— 一条问答看不出上下文
    if user_ref:
        qa_row = (ctx.get("qa") or {})
        ch = qa_row.get("channel") or "web"
        prev = store.rows(
            """SELECT question_raw, answer_text, decision, created_at
                 FROM qa_log WHERE user_ref=? AND channel=?
                ORDER BY id DESC LIMIT 6""",
            (user_ref, ch),
        )
        prev.reverse()
        ctx["conversation"] = [
            {"q": r["question_raw"], "a": (r["answer_text"] or "")[:200],
             "decision": r["decision"], "at": r["created_at"]}
            for r in prev
        ]

    # ③ 最近的服务端报错
    try:
        from .channels import dispatch

        errs = dispatch.errors() if hasattr(dispatch, "errors") else []
        ctx["recent_errors"] = errs[-5:] if errs else []
    except Exception:  # noqa: BLE001
        ctx["recent_errors"] = []

    return ctx


# ══════════════════════════════════════════════════════════════════════
# 增删改查
# ══════════════════════════════════════════════════════════════════════

def create(
    *,
    title: str,
    kind: str,
    detail: str = "",
    severity: str = "normal",
    reporter: str = "",
    reporter_role: str = "",
    qa_log_id: int | None = None,
    user_ref: str | None = None,
    page: str = "",
    tenant_id: str = "default",
) -> dict[str, Any]:
    """报一个问题。★ 只有前两个参数是必填的。"""
    if kind not in {k for k, _l, _h in ISSUE_KINDS}:
        kind = "other"
    if severity not in {k for k, _l, _h in SEVERITIES}:
        severity = "normal"
    ctx = collect_context(qa_log_id=qa_log_id, user_ref=user_ref, page=page, note=detail)
    now = store.now()
    iid = store.run(
        """INSERT INTO issue
             (tenant_id, title, kind, detail, severity, reporter, reporter_role,
              context_json, page, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'new', ?, ?)""",
        (tenant_id, title.strip()[:200], kind, (detail or "").strip()[:2000],
         severity, reporter[:60], reporter_role[:30],
         store.jdump(ctx), page[:80], now, now),
    )
    return {"id": iid, "ok": True, "attached": _summarize_context(ctx)}


def _summarize_context(ctx: dict[str, Any]) -> str:
    """用白话说清楚"系统自动附上了什么" —— 报的人看到会安心。"""
    bits = []
    if ctx.get("qa"):
        bits.append("这条问答的完整记录")
    if ctx.get("conversation"):
        bits.append(f"{len(ctx['conversation'])} 轮对话")
    if ctx.get("hits"):
        bits.append(f"命中的 {len(ctx['hits'])} 条资料")
    if ctx.get("recent_errors"):
        bits.append(f"{len(ctx['recent_errors'])} 条服务端报错")
    return "已自动附上：" + "、".join(bits) if bits else "没有可附的现场信息"


def listing(
    *, tenant_id: str = "default", status: str = "", limit: int = 100, mine: str = ""
) -> dict[str, Any]:
    """列表。★ 技术看全部，报的人看自己报的。"""
    where, args = ["tenant_id=?"], [tenant_id]
    if status and status != "all":
        if status == "open":
            where.append("status IN ('new','working')")
        elif status == "resolved":
            where.append("status='resolved'")
        else:
            where.append("status=?")
            args.append(status)
    if mine:
        where.append("reporter=?")
        args.append(mine)
    sql = f"""SELECT * FROM issue WHERE {' AND '.join(where)}
              ORDER BY
                CASE status WHEN 'new' THEN 0 WHEN 'working' THEN 1
                            WHEN 'resolved' THEN 2 ELSE 3 END,
                CASE severity WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                id DESC LIMIT ?"""
    args.append(limit)
    rows = store.rows(sql, tuple(args))
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "title": r["title"], "kind": r["kind"],
            "kind_label": {k: l for k, l, _h in ISSUE_KINDS}.get(r["kind"], r["kind"]),
            "severity": r["severity"],
            "severity_label": {k: l for k, l, _h in SEVERITIES}.get(r["severity"], r["severity"]),
            "status": r["status"], "status_label": STATUS_LABEL.get(r["status"], r["status"]),
            "reporter": r["reporter"], "reporter_role": r["reporter_role"],
            "assignee": r["assignee"], "resolution": r["resolution"],
            "resolution_kind": r["resolution_kind"],
            "reporter_ack": r["reporter_ack"], "reporter_note": r["reporter_note"],
            "detail": r["detail"], "created_at": r["created_at"],
            "resolved_at": r["resolved_at"], "ack_at": r["ack_at"],
        })
    # 角标用的数字
    c = store.one(
        """SELECT
             SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) new_n,
             SUM(CASE WHEN status='working' THEN 1 ELSE 0 END) working_n,
             SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) resolved_n,
             SUM(CASE WHEN status='resolved' AND (reporter_ack IS NULL OR reporter_ack='')
                      THEN 1 ELSE 0 END) wait_ack_n
           FROM issue WHERE tenant_id=?""",
        (tenant_id,),
    ) or {}
    return {"items": items, "counts": {
        "new": c.get("new_n") or 0,
        "working": c.get("working_n") or 0,
        "resolved": c.get("resolved_n") or 0,
        "wait_ack": c.get("wait_ack_n") or 0,
        "open": (c.get("new_n") or 0) + (c.get("working_n") or 0),
    }}


def detail(issue_id: int) -> dict[str, Any] | None:
    """详情 —— ★ 把自动采集的现场展开给技术看。"""
    r = store.one("SELECT * FROM issue WHERE id=?", (issue_id,))
    if not r:
        return None
    import json

    try:
        ctx = json.loads(r["context_json"] or "{}")
    except Exception:  # noqa: BLE001
        ctx = {}
    return {
        "id": r["id"], "title": r["title"],
        "kind": r["kind"], "kind_label": {k: l for k, l, _h in ISSUE_KINDS}.get(r["kind"], r["kind"]),
        "detail": r["detail"], "severity": r["severity"],
        "severity_label": {k: l for k, l, _h in SEVERITIES}.get(r["severity"], r["severity"]),
        "reporter": r["reporter"], "reporter_role": r["reporter_role"],
        "status": r["status"], "status_label": STATUS_LABEL.get(r["status"], r["status"]),
        "assignee": r["assignee"], "taken_at": r["taken_at"],
        "resolution": r["resolution"], "resolution_kind": r["resolution_kind"],
        "resolved_at": r["resolved_at"],
        "reporter_ack": r["reporter_ack"], "reporter_note": r["reporter_note"],
        "ack_at": r["ack_at"], "page": r["page"],
        "created_at": r["created_at"], "context": ctx,
    }


def take(issue_id: int, *, by: str) -> dict[str, Any]:
    store.run(
        """UPDATE issue SET status='working', assignee=?, taken_at=?, updated_at=?
            WHERE id=? AND status IN ('new','working')""",
        (by[:60], store.now(), store.now(), issue_id),
    )
    return {"ok": True}


def resolve(issue_id: int, *, resolution: str, kind: str = "", by: str = "") -> dict[str, Any]:
    """技术提交解决方案。

    ★★ 注意状态是 `resolved` 而**不是** `closed` ——
      "技术说修好了"和"用户那边确实好了"是两件事。
      必须等报的人确认才算关掉。
    """
    now = store.now()
    store.run(
        """UPDATE issue SET status='resolved', resolution=?, resolution_kind=?,
                            assignee=COALESCE(NULLIF(?,''), assignee),
                            resolved_at=?, updated_at=?,
                            reporter_ack=NULL
            WHERE id=?""",
        (resolution[:2000], kind[:40], by[:60], now, now, issue_id),
    )
    return {"ok": True, "note": "已提交。等报问题的人确认之后才算真的关闭。"}


def ack(issue_id: int, *, ok: bool, note: str = "", by: str = "") -> dict[str, Any]:
    """报的人确认。

    ★ 说"还没好"就**打回 working**，并带上他的补充说明 ——
      这是这个闭环里最有价值的一步：
      很多时候技术改的和他遇到的不是一回事，不打回就永远发现不了。
    """
    now = store.now()
    if ok:
        store.run(
            """UPDATE issue SET reporter_ack='ok', reporter_note=?, ack_at=?,
                                status='closed', updated_at=? WHERE id=?""",
            (note[:500], now, now, issue_id),
        )
        return {"ok": True, "status": "closed", "note": "已确认关闭，谢谢反馈。"}
    store.run(
        """UPDATE issue SET reporter_ack='no', reporter_note=?, ack_at=?,
                            status='working', updated_at=? WHERE id=?""",
        (note[:500], now, now, issue_id),
    )
    return {"ok": True, "status": "working",
            "note": "已打回给技术，并带上你的补充说明。"}


def reject(issue_id: int, *, reason: str, by: str = "") -> dict[str, Any]:
    """技术判定"不是问题"。

    ★ 也必须说理由 —— 不然报的人会觉得"我的问题被无视了"，
      下次就不报了。**让人愿意继续报问题，比这一次的判定更重要。**
    """
    now = store.now()
    store.run(
        """UPDATE issue SET status='rejected', resolution=?, assignee=COALESCE(NULLIF(?,''),assignee),
                            resolved_at=?, updated_at=? WHERE id=?""",
        (reason[:2000], by[:60], now, now, issue_id),
    )
    return {"ok": True}
