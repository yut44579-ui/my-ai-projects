"""冒烟测试 8：网页客服渠道。

★ 这个渠道和企微/飞书有三个本质不同，每一个都要单独验：

    ① **回复路径不同**：没有"主动发消息 API"，回复走发件箱 + 轮询。
       所以要验：消息进发件箱、按 seq 能取增量、**同一秒的两条不会漏**。

    ② **去重策略相反**：企微必须去重（平台会重发），
       网页**不能去重**（用户真的可能问两遍，那是他想再问一遍）。
       所以要验：同一个会话连发两条一样的话，两条都要被处理。

    ③ ★ **转人工时必须给用户交代**：其他渠道人工很快会回，
       网页用户是盯着一个挂件看 —— 不给他"已转人工"会以为坏了。
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

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test8_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import store  # noqa: E402
from app.channels import dispatch as D  # noqa: E402
from app.channels.web import WebChannel  # noqa: E402
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
print("冒烟测试 8 · 网页客服渠道")
print("=" * 64)

store.init_db()
tmp = Path(tempfile.mkdtemp(prefix="kb8_"))
(tmp / "员工手册.md").write_text(
    "标题: 员工手册\n权限: external\n---\n"
    "第1.1条 工作时间：上午九点至下午六点，中午休息一小时。\n"
    "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n",
    encoding="utf-8",
)
folder_conn.sync(folder_conn.FolderConnector(tmp), tenant_id="default")

CH = WebChannel()
S = "sess-001"

# ── 1. 解析 ───────────────────────────────────────────────────────────
print("\n[1] 解析")
m = CH.parse({"text": "上班时间是几点", "session": S})
check("解析出消息", m is not None)
if m:
    check("会话 id 当 user_id", m.user_id == S, m.user_id)
    check("★ 默认是外部客户（网站访客）", m.is_internal is False, str(m.is_internal))
    check("每条消息 id 都不同", m.message_id != CH.parse({"text": "x", "session": S}).message_id)
check("空消息返回 None", CH.parse({"text": "  ", "session": S}) is None)
check("没会话 id 返回 None", CH.parse({"text": "x"}) is None)

# ── 2. ★★ 网页渠道不能去重 ────────────────────────────────────────────
print("\n[2] ★★ 网页渠道不去重（和企微相反）")
r1 = D.accept(CH.parse({"text": "上班时间是几点", "session": S}), CH)
r2 = D.accept(CH.parse({"text": "上班时间是几点", "session": S}), CH)
check("第一条受理", r1.accepted, r1.line())
check("★★ 用户重复问同一句，第二条也要处理（不是平台重发）",
      r2.accepted and not r2.duplicate, r2.line())

D.drain(timeout=60)
sent = WebChannel.poll(S, 0)
print(f"     发件箱里有 {len(sent['messages'])} 条")
check("两条都被处理了", len(sent["messages"]) >= 2, str(len(sent["messages"])))

# ── 3. 回复路径：发件箱 + 增量拉取 ────────────────────────────────────
print("\n[3] ★ 回复路径：发件箱 + 按 seq 拉增量")
first = WebChannel.poll(S, 0)
check("能取到回复", len(first["messages"]) >= 2, str(len(first["messages"])))
check("回复里有内容", any("九点" in x["content"] for x in first["messages"]),
      str([x["content"][:20] for x in first["messages"]]))

cursor = first["last"]
again = WebChannel.poll(S, cursor)
check("用上次的游标再拉，不会重复拿到旧消息", len(again["messages"]) == 0, str(again))

# 再发一条
D.accept(CH.parse({"text": "年假能休几天", "session": S}), CH)
D.drain(timeout=60)
delta = WebChannel.poll(S, cursor)
print(f"     增量: {[x['content'][:24] for x in delta['messages']]}")
check("★ 只拿到新增的那条", len(delta["messages"]) == 1, str(len(delta["messages"])))
check("新游标前进了", delta["last"] > cursor, f"{cursor} → {delta['last']}")

# ── 4. ★★ seq 游标：同一秒的两条不能漏 ───────────────────────────────
print("\n[4] ★★ 同一秒内两条消息不能漏（用 seq 而不是时间戳的原因）")
S2 = "sess-fast"
store.run(
    """INSERT INTO web_outbox (tenant_id, session_id, content, kind, created_at)
       VALUES ('default',?,'第一条','ai',?), ('default',?,'第二条','ai',?)""",
    (S2, store.now(), S2, store.now()),
)
got = WebChannel.poll(S2, 0)
check("★★ 同一秒的两条都取到了", len(got["messages"]) == 2, str(len(got["messages"])))
check("顺序正确（按 seq）", [x["content"] for x in got["messages"]] == ["第一条", "第二条"],
      str([x["content"] for x in got["messages"]]))

# ── 5. ★★ 转人工时必须给用户交代 ──────────────────────────────────────
print("\n[5] ★★ 转人工时网页用户必须收到提示（不能干等）")
S3 = "sess-ho"
D.accept(CH.parse({"text": "你们能便宜点吗", "session": S3}), CH)
D.drain(timeout=60)
msgs = WebChannel.poll(S3, 0)["messages"]
print(f"     用户收到: {[(m['kind'], m['content'][:20]) for m in msgs]}")
check("★ 用户收到了消息（不是一片寂静）", len(msgs) >= 1, str(msgs))
# ★ 现在是 waiting（会转圈的等待指示），不是一句 system 提示 ——
#   用户的原话：「会一直打转……人工接管之后才会停止转」
check("★★ 是等待指示（不是 AI 在说话）",
      any(m["kind"] in ("waiting", "system") for m in msgs), str([m["kind"] for m in msgs]))
sysmsg = next((m["content"] for m in msgs if m["kind"] in ("waiting", "system")), "")
print(f"     系统提示: {sysmsg!r}")
check("提示里说了转人工", "人工" in sysmsg, sysmsg)
check("★ 提示里说了会有人来接手", "接手" in sysmsg or "接入" in sysmsg or "人工" in sysmsg, sysmsg)
check("★ 提示里没有内部岗位/姓名（D6）",
      not any(w in sysmsg for w in ("财务", "法务", "李姐", "工号")), sysmsg)
check("★ 提示里没有 AI 自解释（D7）",
      not any(w in sysmsg for w in ("我叫", "我查", "我已经")), sysmsg)
check("提示很短", len(sysmsg) <= 24, f"{len(sysmsg)} 字")

# 敏感问题本身不能发给用户
ai_msgs = [m for m in msgs if m["kind"] == "ai"]
check("★★ 敏感问题没有 AI 的回答（只有系统提示）", len(ai_msgs) == 0, str(ai_msgs))

# ── 6. 转人工的进了队列 ───────────────────────────────────────────────
print("\n[6] 转人工进了队列")
ho = store.rows("SELECT user_id, question, decision FROM handover_queue WHERE user_id=?", (S3,))
print(f"     队列: {[(r['question'][:12], r['decision']) for r in ho]}")
check("进了人工队列", len(ho) == 1, str(ho))
check("平台标成了 web", store.one(
    "SELECT platform FROM handover_queue WHERE user_id=?", (S3,))["platform"] == "web")

# ── 7. 急停 ───────────────────────────────────────────────────────────
print("\n[7] 急停期间")
from app import governance as G  # noqa: E402

G.set_stop("default", on=True, by="测试", reason="")
S4 = "sess-stop"
D.accept(CH.parse({"text": "上班时间是几点", "session": S4}), CH)
D.drain(timeout=60)
m4 = WebChannel.poll(S4, 0)["messages"]
check("★ 急停期间 AI 不发回答", not any(x["kind"] == "ai" for x in m4), str(m4))
check("但用户还是收到了等待指示（不是干等）",
      any(x["kind"] in ("waiting", "system") for x in m4), str(m4))
G.set_stop("default", on=False, by="测试", reason="")

# ── 8. 会话隔离 ───────────────────────────────────────────────────────
print("\n[8] ★ 不同访客的会话必须隔离")
Sa, Sb = "sess-A", "sess-B"
D.accept(CH.parse({"text": "上班时间是几点", "session": Sa}), CH)
D.drain(timeout=60)
ma = WebChannel.poll(Sa, 0)["messages"]
mb = WebChannel.poll(Sb, 0)["messages"]
check("A 收到了自己的回复", len(ma) >= 1, str(len(ma)))
check("★★ B 看不到 A 的任何消息", len(mb) == 0, str(mb))

# ── 9. 限流（网页渠道的安全边界在频率上）──────────────────────────────
print("\n[9] ★ 网页渠道的安全边界在频率，不在签名")
check("网页渠道 verify 恒为真（公开挂件，防的不是伪造）", CH.verify({}, {}))
n = store.one(
    """SELECT COUNT(*) n FROM inbound_log
        WHERE platform='web' AND user_id=? AND created_at >= datetime('now','-1 minutes')""",
    (Sa,),
)["n"]
print(f"     会话 {Sa} 最近 1 分钟发了 {n} 条")
check("统计得到会话频率", n >= 1, str(n))

# ── 10. 统计与错误 ────────────────────────────────────────────────────
print("\n[10] 调度统计")
st = D.stats()
print(f"     {st}")
check("网页渠道的消息被算进统计", st["accepted"] >= 5, str(st))
check("没有处理失败", st["failed"] == 0, str(st))


# ── 11. ★★ 转人工时的转圈：必须人工接管才停 ─────────────────────────
print("\n[11] ★★ 转人工后给用户一个在等的状态（转圈），人工接管才停")
S5 = "sess-spin"
D.accept(CH.parse({"text": "能报个价吗", "session": S5}), CH)
D.drain(timeout=60)
msgs = WebChannel.poll(S5, 0)["messages"]
kinds = [m["kind"] for m in msgs]
print(f"     转人工后用户收到: {[(m['kind'], m['content'][:16]) for m in msgs]}")
check("★★ 发的是 waiting（会转圈的那种），不是一句话", "waiting" in kinds, str(kinds))
check("waiting 之后没有立刻出现 taken", "taken" not in kinds, str(kinds))

# 人工接管
ho_id = store.one("SELECT id FROM handover_queue WHERE user_id=? ORDER BY id DESC", (S5,))["id"]
from app.channels.web import WebChannel as W2
W2.stop_waiting(S5, "客服已接入")
after = W2.poll(S5, msgs[-1]["seq"] if msgs else 0)["messages"]
print(f"     接管后新增: {[(m['kind'], m['content']) for m in after]}")
check("★★ 接管后发 taken（客户端据此停转）",
      any(m["kind"] == "taken" for m in after), str(after))
check("提示文案是给人看的", any("接入" in m["content"] for m in after), str(after))

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
D.shutdown()
sys.exit(1 if FAIL else 0)
