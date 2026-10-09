"""冒烟测试：不调模型，验证核心四件事能否跑通。

★ 为什么要先做"不调模型"的测试：
  模型调用慢、要钱、结果不稳定。把能确定性验证的部分（检索/策略/学习循环）
  先测通，出问题时才能一眼看出"是检索坏了还是模型坏了"。
  另两个项目都吃过"分不清是哪一层坏了"的亏。
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

# ★ 必须用**独立的临时库**。
#   第一版直接用 data/kefu.db，结果服务在跑的时候测试删不掉这个文件
#   （Windows 下被占用 → PermissionError），而且更糟的是：
#   测试会污染真实数据。测试永远不该碰开发/生产的库。
os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test_")) / "test.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, policy, retrieval, store  # noqa: E402
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


print("=" * 62)
print("AI客服 · 核心冒烟测试（不调模型）")
print("=" * 62)

# ── 1. 建库 ────────────────────────────────────────────────────────────
print("\n[1] 建库")
if config.DB_PATH.exists():
    config.DB_PATH.unlink()
store.init_db()
check("数据库建好", config.DB_PATH.exists(), str(config.DB_PATH))

# ── 2. 灌知识 ──────────────────────────────────────────────────────────
print("\n[2] 灌知识（模拟一个 SME 的内部资料）")
docs = [
    (
        "kb/费用报销管理办法",
        "费用报销管理办法",
        "第3.2条 报销单经审批通过后，财务部应于5个工作日内完成付款。\n"
        "第3.3条 发票必须贴在报销单背面，拍照上传后提交。\n"
        "第2.1条 报销单填写需注明金额、事由、发生日期。",
        "2025-03-01T00:00:00+08:00",
    ),
    (
        "kb/员工手册",
        "员工手册",
        "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n"
        "第1.1条 工作时间：上午九点至下午六点，中午休息一小时。",
        "2025-06-15T00:00:00+08:00",
    ),
    (
        "kb/发票管理旧版",
        "发票管理规定（2022版，已过期）",
        "第5条 发票作废需在开票当月内到财务部办理，逾期不予受理。",
        "2022-01-01T00:00:00+08:00",
    ),
]
for doc_id, title, content, upd in docs:
    r = retrieval.upsert_document(
        doc_id=doc_id, tenant_id="default", source="folder",
        source_ref=doc_id, title=title, content=content, updated_at=upd,
    )
    print(f"     {title}: 切了 {r['chunks']} 块")

n_doc = store.one("SELECT COUNT(*) n FROM doc")["n"]
n_chunk = store.one("SELECT COUNT(*) n FROM chunk")["n"]
check("文档入库", n_doc == 3, f"实际 {n_doc}")
# ★ 这三篇测试文档都很短（<420 字），本来就该只切 3 块。
#   第一版我写了 >=4，是**断言写错了**，不是分块坏了 ——
#   分块逻辑另用长文本单独测（见下）。
check("短文档各切 1 块", n_chunk == 3, f"实际 {n_chunk}")

# 分块逻辑单独测：给一段长文本，必须切出多块，且块与块之间有重叠
_long = "".join(f"第{i}条 这是第{i}条规定的具体内容，用于验证长文档分块是否正确。" for i in range(1, 41))
_pieces = retrieval.chunk_text(_long)
check("长文档被切成多块", len(_pieces) >= 3, f"实际 {len(_pieces)} 块")
check("每块都不超过上限", all(len(p) <= config.CHUNK_CHARS + 40 for p in _pieces),
      f"最长 {max(len(p) for p in _pieces)}")
print(f"     长文 {len(_long)} 字 → {len(_pieces)} 块，最长 {max(len(p) for p in _pieces)} 字")

# ── 3. 检索 ────────────────────────────────────────────────────────────
print("\n[3] 检索（口语问法 → 命中书面材料）")
cases = [
    ("报销单咋填啊", "费用报销管理办法"),
    ("上班时间几点", "员工手册"),
    ("年假能休几天", "员工手册"),
]
for q, expect_title in cases:
    r = retrieval.search(q, tenant_id="default")
    got = r.top.title if r.top else "(无)"
    check(f"「{q}」→ {expect_title}", expect_title in got, f"实际命中 {got}  quality={r.quality}")
    if r.top:
        print(f"         质量 {r.quality}  信号 {r.signals}")

r_empty = retrieval.search("今天天气怎么样", tenant_id="default")
print(f"     [无关问题] 「今天天气怎么样」→ quality={r_empty.quality}  信号={r_empty.signals}")
check("无关问题质量低", r_empty.quality < 0.35, f"实际 {r_empty.quality}")

# ── 4. 时效性惩罚 ──────────────────────────────────────────────────────
print("\n[4] ★ 时效性：过期文档必须被打折")
r_stale = retrieval.search("发票作废怎么弄", tenant_id="default")
stale_hits = [h for h in r_stale.hits if h.stale]
check("识别出过期文档", len(stale_hits) > 0, f"信号 {r_stale.signals}")
if stale_hits:
    print(f"         过期命中: {stale_hits[0].title}  打折后分 {stale_hits[0].score}")

# ── 5. 出站检查（这次评审得到的教训）─────────────────────────────────
print("\n[5] 出站检查")
bad_role = "已经转给财务部李姐处理，她会联系你。"
res = policy.check_outbound(bad_role, is_internal=False)
check("拦下身份泄漏（对客户）", res.must_escalate, res.why())
print(f"         问题: {res.why()}")

res2 = policy.check_outbound(bad_role, is_internal=True)
check("内部同事之间不拦身份", not res2.must_escalate, res2.why())

self_talk = "我叫个同事帮你看下。审批过了5个工作日到账。"
res3 = policy.check_outbound(self_talk, is_internal=True)
check("删掉 AI 自解释", "我叫个同事" not in res3.cleaned, res3.cleaned)
check("保留真正有用的那句", "5个工作日到账" in res3.cleaned, res3.cleaned)
print(f"         清理后: {res3.cleaned}")

md = "**重点**：审批后 5 个工作日到账。\n\n您好！感谢您的咨询！"
res4 = policy.check_outbound(md, is_internal=True)
check("清掉 markdown", "**" not in res4.cleaned, res4.cleaned)
check("清掉客服腔", "感谢您的咨询" not in res4.cleaned, res4.cleaned)
# ★ 上一版这里漏了：只删词不删标点，会留下"！！"孤零零挂在末尾
# ★ 但断言不能写成"结尾不许是标点" —— 中文句子以「。」结尾本来就是对的。
#   只抓真正的孤儿标点：连续标点、或只剩一个感叹号。
import re as _re

check("不留孤儿标点", not _re.search(r"[！!？?，,。]{2,}|[！!]\s*$", res4.cleaned),
      repr(res4.cleaned))
print(f"         清理后: {res4.cleaned!r}")

commit = "这个价格我保证是最低价，一定能给你。"
res5 = policy.check_outbound(commit, is_internal=True)
check("拦下越权承诺", res5.must_escalate, res5.why())

# ── 6. 意图判定（不该让模型答的）───────────────────────────────────────
print("\n[6] 转人工判定（在调模型之前就拦）")
for q, should in [
    ("你们这个能便宜点吗", True),
    ("合同怎么签", True),
    ("我要退款", True),
    ("我要投诉", True),
    ("上班时间是几点", False),
    ("年假几天", False),
]:
    v = policy.judge_intent(q)
    check(f"「{q}」→ {'转人工' if should else '可以让AI答'}", v.escalate == should, v.reason)

# ── 7. 分级 ────────────────────────────────────────────────────────────
print("\n[7] 分级（自动发 / 起草 / 转人工）")
check("高分 → 自动发", policy.decide(0.9, tier="standard") == "auto")
check("中分 → 起草", policy.decide(0.55, tier="standard") == "draft")
check("低分 → 转人工", policy.decide(0.2, tier="standard") == "escalate")
check("意图命中优先于分数", policy.decide(0.99, intent_escalate=True) == "escalate")
check(
    "保守档门槛更高",
    policy.decide(0.8, tier="conservative") != "auto",
    f"实际 {policy.decide(0.8, tier='conservative')}",
)

# ── 8. ★ 学习闭环 ──────────────────────────────────────────────────────
print("\n[8] ★ 学习闭环（越用越聪明的核心）")

# 8a. 一条答不上来的
id1 = store.log_qa(
    tenant_id="default", question_raw="那个团建费能不能报啊",
    question_rewrite="团建 费用 报销", quality=0.12, decision="escalate",
)
store.decide(id1, outcome="unanswered", failure_reason="no_hit")

# 8b. 同一个问题又被问了两次（应合并计数）
for _ in range(2):
    i = store.log_qa(tenant_id="default", question_raw="请问那个团建费能不能报啊",
                     quality=0.10, decision="escalate")
    store.decide(i, outcome="unanswered", failure_reason="no_hit")

# 8c. 一条答了但被人改了
id3 = store.log_qa(
    tenant_id="default", question_raw="报销多久到账",
    answer_text="您好！根据《费用报销管理办法》第3.2条规定，报销单经审批通过后，财务部应于5个工作日内完成付款。感谢您的咨询！",
    quality=0.66, decision="draft",
)
store.decide(
    id3, outcome="edited", failure_reason="bad_style",
    edited_to="审批过了 5 个工作日到账。",
)
store.add_feedback(id3, source="operator_edit", kind="correction",
                   detail={"note": "去掉了客套和条款原文"})

m1 = L.mine_gaps("default")
m2 = L.mine_style_examples("default")
print(f"     挖待补充问题: {m1}")
print(f"     挖风格示例:   {m2}")

# ★ 学到的产物默认是 pending，要过评测关卡才生效（见 smoke3 与决策记录 D2）。
#   这里没有评测集，所以 promote 会直接放行并标注"未经验证"。
#   第一版没有这一步，于是"风格示例已沉淀"直接失败了 ——
#   那不是 bug，是设计变更：**学到的东西不该立刻生效**。
LE.promote_pending("default", use_llm=False)

gaps = L.open_gaps("default")
check("待补充问题已沉淀", len(gaps) >= 1, f"实际 {len(gaps)}")
if gaps:
    print(f"         「{gaps[0]['sample_question']}」被问了 {gaps[0]['occurrences']} 次")
    check("★ 同类问题已合并计数", gaps[0]["occurrences"] == 3, f"实际 {gaps[0]['occurrences']}")

styles = L.style_examples("default")
check("风格示例已沉淀", len(styles) >= 1, f"实际 {len(styles)}")
if styles:
    print(f"         问题: {styles[0]['question']}")
    print(f"         改成: {styles[0]['answer_after']}")
    print(f"         改了什么: {styles[0]['diff_summary']}")

# ── 9. 失败原因分布（决定该修哪里）──────────────────────────────────────
print("\n[9] 失败原因分布 —— 决定该修哪一层")
b = L.analyze("default")
for line in b.lines():
    print(f"     {line}")
check("能统计出失败原因", "bad_style" in b.by_reason, str(b.by_reason))
check("四种原因有各自的修法", len(L.FIX_BY_REASON) >= 5)

# ── 10. 同义词 ─────────────────────────────────────────────────────────
print("\n[10] 同义词 / 内部黑话")
L.add_synonym("default", "报消", "报销")
L.add_synonym("default", "打款", "付款")
# ★ 同样要先过闸门才生效（pending → active）
LE.promote_pending("default", use_llm=False)
syn = L.synonyms("default")
check("同义词已存", len(syn) == 2, str(syn))
r6 = retrieval.search("报消多久到账", tenant_id="default")
check("★ 加了同义词后能命中报销制度", "费用报销" in (r6.top.title if r6.top else ""),
      f"实际 {r6.top.title if r6.top else '(无)'}  quality={r6.quality}")
print(f"         扩展后命中: {r6.top.title if r6.top else '(无)'}  quality={r6.quality}")

# ── 11. 删除必须真删 ───────────────────────────────────────────────────
print("\n[11] ★ 删除文档必须真的删掉（否则旧内容会继续被回答）")
before = store.one("SELECT COUNT(*) n FROM chunk")["n"]
retrieval.delete_document("kb/发票管理旧版")
after = store.one("SELECT COUNT(*) n FROM chunk")["n"]
check("分块随之删除", after < before, f"{before} → {after}")
r7 = retrieval.search("发票作废", tenant_id="default")
check("删掉的文档不再被检索到",
      not any("2022版" in h.title for h in r7.hits),
      str([h.title for h in r7.hits]))

# ── 12. 评测关卡 ───────────────────────────────────────────────────────
print("\n[12] ★ 评测关卡（防止越学越偏）")
store.run(
    """INSERT INTO eval_case (tenant_id, question, must_not_contain, created_at)
       VALUES ('default', '报销多久到账', ?, ?)""",
    (store.jdump(["财务部", "感谢您的咨询", "根据《"]), store.now()),
)
case = L.eval_cases("default")[0]
ok1, p1 = L.check_answer(case, "审批过了 5 个工作日到账。")
ok2, p2 = L.check_answer(case, "您好！根据《费用报销管理办法》，财务部应于5个工作日完成付款。感谢您的咨询！")
check("好答案通过", ok1, str(p1))
check("★ 机器味答案被拦", not ok2, str(p2))
print(f"         拦下原因: {p2}")

# ── 汇总 ───────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
