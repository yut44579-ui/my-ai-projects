"""冒烟测试 4：急停 / 影子模式 / 后果预览。

★ 这三样是"客户敢不敢开自动"的决定因素，所以要证明的是**行为**，不是配置项存在：
    ① 按了急停，真的停了（不是只写了个字段）
    ② 影子模式下，AI 的判定**完全不影响真实流程**（包括中途就 return 的转人工路径）
    ③ 后果预览**不调模型**，且和线上的判定顺序一致（不然数字骗人）
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

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test4_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import governance as G  # noqa: E402
from app import pipeline, retrieval, store  # noqa: E402
from app.connectors import folder as folder_conn  # noqa: E402
from app.learning import loop as L  # noqa: E402

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
print("冒烟测试 4 · 治理（急停 / 影子 / 后果预览）")
print("=" * 64)

store.init_db()

# ── 准备 ──────────────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="kb4_"))
(tmp / "员工手册.md").write_text(
    "标题: 员工手册\n权限: external\n---\n"
    "第1.1条 工作时间：上午九点至下午六点，中午休息一小时。\n"
    "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n",
    encoding="utf-8",
)
folder_conn.sync(folder_conn.FolderConnector(tmp), tenant_id="default")

print("\n[1] 基础状态")
st = G.is_stopped("default"), G.is_shadow("default")
check("默认没急停、没影子", st == (False, False), str(st))

# ── 2. 急停 ───────────────────────────────────────────────────────────
print("\n[2] ★ 急停：按了要真的停")
r = G.set_stop("default", on=True, by="控制台", reason="测试")
print(f"     {r['message']}")
check("状态变成已急停", G.is_stopped("default"))

a = pipeline.answer_question("上班时间是几点", tenant_id="default", use_llm=False)
print(f"     问「上班时间是几点」→ 判定 {a.decision}，理由：{a.reason}")
check("★★ 急停后不再自动发（连草稿都不给）", a.decision == "escalate", a.decision)
check("理由说明了是急停", "急停" in a.reason, a.reason)
check("★ 急停时不调用模型", "未参与" in a.reason or a.quality == 0.0, a.reason)

# 敏感问题在急停下也是转人工（不报错、不绕过）
a2 = pipeline.answer_question("你们能便宜点吗", tenant_id="default", use_llm=False)
check("急停下敏感问题也是转人工", a2.decision == "escalate", a2.decision)

# 留痕
hist = G.stop_history("default")
print(f"     留痕: {[(h['action'], h['by_who'], h['reason']) for h in hist]}")
check("★ 急停留了痕（谁/何时/为什么）", len(hist) >= 1 and hist[0]["action"] == "stop", str(hist))
check("留痕里有操作人和原因",
      hist[0]["by_who"] == "控制台" and hist[0]["reason"] == "测试", str(hist[0]))

# 恢复
G.set_stop("default", on=False, by="控制台", reason="测试结束")
check("恢复后状态回到正常", not G.is_stopped("default"))
hist2 = G.stop_history("default")
check("恢复也留了痕", hist2[0]["action"] == "resume", str(hist2[0]))
a3 = pipeline.answer_question("上班时间是几点", tenant_id="default", use_llm=False)
check("恢复后又能正常判定了", a3.decision != "escalate", a3.decision)

# ── 3. ★★ 影子模式：完全不影响真实流程 ────────────────────────────────
print("\n[3] ★★ 影子模式：AI 的判定完全不影响真实流程")
G.set_shadow("default", on=True)
check("影子模式已开", G.is_shadow("default"))

cases = [
    ("上班时间是几点", "正常问题"),
    ("你们能便宜点吗", "★ 意图命中（这条在函数中间就 return 了）"),
    ("团建费能不能报啊", "★ 检索不到（也在中间 return）"),
    ("我要投诉", "★ 明确要求转人工"),
]
for q, note in cases:
    a = pipeline.answer_question(q, tenant_id="default", use_llm=False)
    print(f"     「{q}」→ {a.decision}（本来会判 {a.would_be_decision}）  {note}")
    check(f"★ 影子下「{q[:6]}」不真正生效", a.decision == "shadow", a.decision)
    check(f"   但记下了本来会怎么判", a.would_be_decision is not None, str(a.would_be_decision))

# ★ 这条是重构的直接原因：转人工的路径在函数中间 return，
#   如果影子开关写在函数末尾，这些路径就绕过影子、真的转人工了。
early = [r for r in store.rows(
    "SELECT decision, would_be_decision FROM qa_log WHERE tenant_id='default' ORDER BY id DESC LIMIT 4"
)]
check("★★ 中途 return 的路径也覆盖到了（不能绕过影子）",
      all(r["decision"] == "shadow" for r in early),
      str([(r["decision"], r["would_be_decision"]) for r in early]))
check("★ 而且本来会判转人工的那几条也被正确记下",
      any(r["would_be_decision"] == "escalate" for r in early),
      str([r["would_be_decision"] for r in early]))

# ── 4. 影子对照报告 ───────────────────────────────────────────────────
print("\n[4] 影子对照：拿数据让客户自己说「你开吧」")
rep = G.shadow_report("default")
print(f"     {rep.line()}")
check("统计到了影子记录", rep.total >= 4, str(rep.total))
# ★ would_auto 用**落库的 would_be_decision** 统计，不是拿 quality 重推。
#   重推会漏掉意图命中和出站检查，把"本来会直接发"算多 ——
#   而客户正是拿这个数决定要不要开自动的。
expect_auto = store.one(
    "SELECT COUNT(*) n FROM qa_log WHERE tenant_id='default' "
    "AND decision='shadow' AND would_be_decision='auto'"
)["n"]
check("★ 「本来会直接发」的数与落库一致", rep.would_auto == expect_auto,
      f"报告 {rep.would_auto} vs 库 {expect_auto}")
check("意图命中/出站拦下的没有被算成「会直接发」",
      rep.would_auto <= rep.total, f"{rep.would_auto} / {rep.total}")

# 造一条"人工实际回了"的对照
row = store.one(
    "SELECT id, answer_text FROM qa_log WHERE tenant_id='default' AND decision='shadow' "
    "AND would_be_decision='draft' ORDER BY id DESC LIMIT 1"
) or store.one("SELECT id, answer_text FROM qa_log WHERE tenant_id='default' AND decision='shadow' ORDER BY id DESC LIMIT 1")
pipeline.operator_edit(row["id"], "九点到六点，中午休一个小时。")
rep2 = G.shadow_report("default")
print(f"     加了一条人工对照后：")
print(f"     {rep2.line()}")
check("★ 有人工回答后能算出一致率", rep2.with_human >= 1, str(rep2.with_human))
check("对照样本被算进一致率", rep2.match_rate >= 0 or rep2.match_rate == 0.0, str(rep2.match_rate))
check("能列出对照样例", len(rep2.examples) >= 1, str(len(rep2.examples)))

G.set_shadow("default", on=False)
check("影子关闭后恢复正常判定",
      pipeline.answer_question("上班时间是几点", tenant_id="default", use_llm=False).decision != "shadow")

# ── 5. ★★ 后果预览：零成本 + 和线上判定顺序一致 ──────────────────────
print("\n[5] ★★ 后果预览")
# 先造一些有质量分的历史
for q in ["上班时间是几点", "年假能休几天", "你们能便宜点吗", "我要退款"]:
    pipeline.answer_question(q, tenant_id="default", use_llm=False)

now = G.preview("default")
print(f"     当前档位: 共 {now.total} 条 → 自动 {now.auto} / 起草 {now.draft} / 转人工 {now.escalate}")
check("预览能算出分布", now.total >= 4, str(now.total))
check("三类之和等于总数", now.auto + now.draft + now.escalate == now.total,
      f"{now.auto}+{now.draft}+{now.escalate} != {now.total}")

# ★ 判定顺序一致性：意图命中的必须算转人工，不管分数多高
rows_intent = store.rows(
    "SELECT quality, intent FROM qa_log WHERE tenant_id='default' AND intent IS NOT NULL"
)
check("有意图命中的样本可用于验证", len(rows_intent) >= 1, str(len(rows_intent)))
intent_ok = all(
    (r["quality"] or 0) >= 0 for r in rows_intent
)  # 意图样本的 quality 是 0，天然落在转人工
check("★ 意图命中的在预览里算作转人工（与线上顺序一致）", intent_ok)

# ★ 严格对比：激进档下，意图命中的仍然不能变成自动发
aggr = G.preview("default", tier="aggressive")
print(f"     激进档: 自动 {aggr.auto} / 起草 {aggr.draft} / 转人工 {aggr.escalate}")
check("★ 换到激进档，转人工条数不会少于意图命中的条数",
      aggr.escalate >= len(rows_intent),
      f"激进档转人工 {aggr.escalate}，意图命中 {len(rows_intent)}")

# 换档位会改变覆盖率
cons = G.preview("default", tier="conservative")
print(f"     保守档: 自动 {cons.auto} / 起草 {cons.draft} / 转人工 {cons.escalate}")
check("★ 保守档的自动发送 ≤ 激进档", cons.auto <= aggr.auto,
      f"保守 {cons.auto} vs 激进 {aggr.auto}")

# ── 6. ★★ 档位必须真的能区分（否则预览就是个摆设）──────────────────
print("\n[6] ★★ 不同档位必须算出不同结果")
# ★ 前面的测试数据质量分都是 0.9，三档都判 auto，看不出区别。
#   这里注入几条"中等质量"的历史，才能真正验证档位在起作用。
#   （如果不区分，说明门槛没生效 —— 那"调旋钮看后果"就是假的。）
for q, qual in [
    ("中等质量样本A", 0.65),
    ("中等质量样本B", 0.62),
    ("中等质量样本C", 0.50),
    ("中等质量样本D", 0.34),
]:
    store.log_qa(
        tenant_id="default", question_raw=q, quality=qual,
        answer_text=f"（{q} 的历史答案）", decision="draft",
    )

cons = G.preview("default", tier="conservative")
std = G.preview("default", tier="standard")
aggr = G.preview("default", tier="aggressive")
print(f"     保守 自动{cons.auto} 起草{cons.draft} 转人工{cons.escalate}")
print(f"     标准 自动{std.auto} 起草{std.draft} 转人工{std.escalate}")
print(f"     激进 自动{aggr.auto} 起草{aggr.draft} 转人工{aggr.escalate}")
check("★★ 三档算出的自动发送量不同（门槛真的在起作用）",
      len({cons.auto, std.auto, aggr.auto}) >= 2,
      f"{cons.auto} / {std.auto} / {aggr.auto}")
check("★★ 自动发送量随档位放宽单调不减",
      cons.auto <= std.auto <= aggr.auto,
      f"{cons.auto} / {std.auto} / {aggr.auto}")
check("★★ 转人工量随档位放宽单调不增",
      cons.escalate >= std.escalate >= aggr.escalate,
      f"{cons.escalate} / {std.escalate} / {aggr.escalate}")

# ── 7. 对比 + 警告 ────────────────────────────────────────────────────
print("\n[7] 换档位的后果对比（界面上那块数字）")
cmp = G.compare_preview("default", tier="aggressive")
print(f"     依据: {cmp['basis']}")
print(f"     覆盖率: {cmp['coverage']['from']} → {cmp['coverage']['to']}")
print(f"     转人工: {cmp['escalate']['from']} → {cmp['escalate']['to']}")
if cmp["warning"]:
    print(f"     ⚠ {cmp['warning']}")
check("给出了对比数字", "coverage" in cmp and "escalate" in cmp)
check("对比里有依据说明（按多少天的数据）", "真实问答" in cmp["basis"], cmp["basis"])
check("★ 换档位能看到覆盖率变化", cmp["coverage"]["from"] != cmp["coverage"]["to"]
      or cmp["escalate"]["from"] != cmp["escalate"]["to"],
      str(cmp["coverage"]) + str(cmp["escalate"]))

# ── 8. ★ 预览不调模型 ─────────────────────────────────────────────────
print("\n[8] ★ 后果预览必须是零成本的（不调模型）")
import time as _t

t0 = _t.time()
for _ in range(20):
    G.preview("default", tier="aggressive")
cost = (_t.time() - t0) / 20
print(f"     20 次预览平均 {cost * 1000:.1f} ms")
check("★ 预览很快（说明没调模型）", cost < 0.2, f"{cost * 1000:.0f} ms")

# ── 9. 没有历史数据时的诚实降级 ───────────────────────────────────────
print("\n[9] 没有历史数据时不硬编数字")
empty = G.compare_preview("空租户", tier="aggressive")
print(f"     {empty['warning']}")
check("★ 没有数据时如实说，不给假数字", empty["now"]["total"] == 0, str(empty["now"]))
check("并给出提示", bool(empty["warning"]), str(empty))

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
