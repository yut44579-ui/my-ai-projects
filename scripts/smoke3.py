"""冒烟测试 3：评测关卡 —— 防止"越学越偏"的那道闸门。

★ 这一轮测的是本系统最核心的一条判断（见 docs/决策记录.md D2）：
    没有验证的自我进化是**放大器**，不是进步。

    所以要证明三件事：
      ① 没验证的学习**不能生效**（否则噪音会污染全局）
      ② 改动让准确率下降时，闸门能**发现并回滚**
      ③ 被拦下的东西**看得见**（否则管理员以为系统什么都没学到）
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

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test3_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pipeline, retrieval, store  # noqa: E402
from app.connectors import folder as folder_conn  # noqa: E402
from app.learning import eval as LE  # noqa: E402
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
print("冒烟测试 3 · 评测关卡（越用越聪明的保险丝）")
print("=" * 64)

store.init_db()

# ── 准备知识 ──────────────────────────────────────────────────────────
print("\n[1] 准备知识（刻意不含内部部门名，方便测断言）")
tmp = Path(tempfile.mkdtemp(prefix="kb3_"))
(tmp / "员工手册.md").write_text(
    "标题: 员工手册\n权限: external\n---\n"
    "第1.1条 工作时间：上午九点至下午六点，中午休息一小时。\n"
    "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n",
    encoding="utf-8",
)
(tmp / "折扣政策.md").write_text(
    "标题: 折扣政策\n权限: internal\n---\n"
    "第9条 标准折扣为 7 折，低于此数需审批。\n",
    encoding="utf-8",
)
# ★ 再加一篇 **external** 的。
#   第一版只用了 internal 那篇来测"有害改动"，结果闸门根本没被触发 ——
#   因为外部视角看不到 internal 文档（权限隔离在正常工作）。
#   要测闸门，得用一个外部真能看到、但内容不该被答出来的文档。
(tmp / "促销活动.md").write_text(
    "标题: 促销活动\n权限: external\n---\n"
    "本次促销最低可到 8 折，仅限老客户。\n",
    encoding="utf-8",
)
res = folder_conn.sync(folder_conn.FolderConnector(tmp), tenant_id="default")
print(f"     {res.line()}")
check("知识入库", res.added == 3, str(res.added))

# ── 2. 攒一些"已被确认可用"的问答，用来生成评测集 ────────────────────
print("\n[2] 评测集自动生长：从「已被确认可用」的回答里抽断言")
for q in ["上班时间是几点", "年假能休几天"]:
    a = pipeline.answer_question(q, tenant_id="default", use_llm=False)
    store.decide(a.qa_log_id, outcome="accepted")  # 人工点了"这句可以"
    print(f"     「{q}」→ 判定 {a.decision}，质量 {a.quality}")

r = LE.seed_from_log("default")
print(f"     生成评测用例: {r}")
cs = LE.cases("default")
check("生成了评测用例", r["added"] >= 1, str(r))
for c in cs:
    print(f"       · {c['question']}")
    print(f"         必须出现: {store.jload(c['must_contain'], [])}")
    print(f"         禁止出现: {store.jload(c['must_not_contain'], [])[:3]}…")
check("★ 自动抽出了要断言的事实", any(store.jload(c["must_contain"], []) for c in cs))
check("★ 安全断言被自动带上（禁用词）",
      all("财务部" in store.jload(c["must_not_contain"], []) for c in cs))

# ── 3. 跑一遍评测 ─────────────────────────────────────────────────────
print("\n[3] 跑一遍评测（不调模型，用原文直出）")
out = LE.run("default", label="基线", use_llm=False)
print(f"     {out.summary()}")
for f in out.failures(3):
    print(f"       失败：{f['question']} → {'；'.join(f['problems'])}")
check("评测跑起来了", out.total > 0, str(out.total))
check("★ 基线是通过的（否则后面测不了「变差」）", out.passed == out.total,
      f"{out.passed}/{out.total}")

# ── 4. ★★ 核心：没验证的学习不能生效 ──────────────────────────────────
print("\n[4] ★★ 没验证的学习不能生效")
before_used = len(L.style_examples("default", limit=50))
# 人工改一条草稿 → 应该落成 pending，而不是立刻生效
a = pipeline.answer_question("上班时间是几点", tenant_id="default", use_llm=False)
pipeline.operator_edit(a.qa_log_id, "九点到六点，中午休一小时。")
after_used = len(L.style_examples("default", limit=50))
pend = L.pending_count("default")
print(f"     pending: {pend}")
check("★ 人工改稿后，示例进了待验证队列", pend["styles"] >= 1, str(pend))
check("★★ 但**没有生效**（取不到）", after_used == before_used,
      f"生效数 {before_used} → {after_used}")

# ── 5. 冷启动：没有评测集时直接放行（但如实标注）──────────────────────
print("\n[5] 冷启动：评测集为空时的行为")
store.run("DELETE FROM eval_case WHERE tenant_id='default'")
store.run("UPDATE style_example SET status='pending' WHERE tenant_id='default'")
r5 = LE.promote_pending("default", use_llm=False)
print(f"     {r5}")
check("没有评测集时放行（不能把学习全堵死）", r5["allowed"], str(r5))
check("★ 但如实说明是「未经验证」", "未经验证" in r5.get("reason", ""), r5.get("reason", ""))
check("放行后确实生效了", len(L.style_examples("default", limit=50)) >= 1)

# ── 6. ★★ 闸门能发现"变差"并回滚 ─────────────────────────────────────
print("\n[6] ★★ 改动让准确率下降时，闸门必须发现并回滚")
# ★ 注意这里**不写 expect_decision**：
#   测试用 use_llm=False（走原文直出），而原文直出会被强制降成 draft，
#   写 expect_decision="auto" 的话这条用例永远失败，基线就成了 0 分，
#   后面"变差"也就测不出来了。第一版就是这么错的。
LE.add_case(
    "default", "上班时间是几点",
    must_contain=["九点"],
    must_not_contain=["折"],  # ★ 不许把内部折扣政策的内容答出去
    note="手工加的：测闸门能不能拦住污染",
)
base = LE.run("default", label="重新测基线", use_llm=False)
print(f"     基线 {base.summary()}")
check("基线是满分的（否则测不出变差）", base.passed == base.total, base.summary())

# 故意加一条**有害的**同义词：让"上班"也去匹配那篇促销文档
L.add_synonym("default", "上班", "促销")
r6 = LE.promote_pending("default", use_llm=False)
print(f"     {r6['line']}")
print(f"     理由: {r6['reason']}")
check("★★ 闸门拦下了这次改动", not r6["allowed"], str(r6))
check("★★ 并且回滚了", r6.get("reverted"), str(r6))
check("准确率确实下降了", r6["after"] < r6["before"],
      f"{r6['before']} → {r6['after']}")

# ── 7. 被拦下的看得见 ─────────────────────────────────────────────────
print("\n[7] 被拦下的学习产物必须看得见")
rej = LE.rejected_items("default")
print(f"     被拒同义词: {[s['colloquial'] + '→' + s['standard'] for s in rej['synonyms']]}")
check("★ 被拦下的东西能查到（否则以为系统没学到）", len(rej["synonyms"]) >= 1, str(rej))
check("被拦下的同义词没有生效", "促销" not in L.synonyms("default").values(),
      str(L.synonyms("default")))
check("★ 而且没有留在 pending 反复重试",
      L.pending_count("default")["synonyms"] == 0,
      str(L.pending_count("default")))

# ── 8. 评测历史 ───────────────────────────────────────────────────────
print("\n[8] 评测历史（回答「哪次学习有效」）")
hist = LE.history("default", limit=10)
print(f"     共 {len(hist)} 次评测记录：")
for h in hist[:5]:
    print(f"       {h['label']}  {h['passed']}/{h['total']}")
check("记下了评测历史", len(hist) >= 4, str(len(hist)))

# ── 9. 好改动能通过 ───────────────────────────────────────────────────
print("\n[9] 无害的改动应该能通过")
n_before = len(L.synonyms("default"))
L.add_synonym("default", "上班时间", "工作时间")  # 无害：只是补个同义说法
r9 = LE.promote_pending("default", use_llm=False)
print(f"     {r9['line']}")
check("无害改动放行", r9["allowed"], str(r9))
check("生效后同义词数增加", len(L.synonyms("default")) >= n_before, str(L.synonyms("default")))

# ── 10. 安全检查优先于事实检查 ────────────────────────────────────────
print("\n[10] ★ 判定顺序：先查安全，再查事实")
# ★ 这里第一版把 must_contain 直接写成 list，而 _judge 当时用 jload 解析，
#   遇到 list 会**静默返回 []** —— 所有断言被跳过，用例假装通过。
#   这就是所谓的"假通过比失败危险"：你以为在验证，其实什么都没验证。
#   现在 _as_list 两种形式都接受。
c = {
    "must_contain": ["九点"],
    "must_not_contain": ["财务部", "感谢您的咨询"],
    "expect_decision": None,
}
probs = LE._judge(c, "抱歉，财务部会处理，感谢您的咨询！", "auto")
print(f"     对一个既有安全问题、又缺事实的答案，报出来的问题：{probs}")
check("安全问题被报出来", any("财务部" in p for p in probs), str(probs))
check("事实缺失也被报出来（不能被静默跳过）", any("九点" in p for p in probs), str(probs))

# ── 11. 单条用例炸了不该中断整轮 ──────────────────────────────────────
print("\n[11] 健壮性：单条用例出问题不该中断整轮评测")
LE.add_case("default", "上班时间是几点", must_contain=["九点"], note="重复用例，测健壮性")
out11 = LE.run("default", label="健壮性", use_llm=False)
check("仍然跑完了全部用例", out11.total == len(LE.cases("default")), str(out11.total))
check("没有因为重复用例崩掉", out11.total >= 2)

# ★ 12. 用例格式不对时必须**报出来**，不能静默通过
print("\n[12] ★ 用例格式不对不能静默通过（假通过比失败危险）")
probs_bad = LE._judge({"must_contain": "九点", "must_not_contain": None}, "别的答案", "auto")
check("★ 非 JSON 的 must_contain 也当普通词处理（不静默跳过）",
      any("九点" in p for p in probs_bad), str(probs_bad))
probs_list = LE._judge({"must_contain": ["九点"], "must_not_contain": ["财务部"]}, "财务部说的，没有那个数", "auto")
check("★ 直接传 list 也能正确断言",
      any("财务部" in p for p in probs_list) and any("九点" in p for p in probs_list),
      str(probs_list))

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
