"""冒烟测试 2：文件夹连接器 + 编排层 + 降级链（不调模型）。

★ 编排层刻意能在**没有模型**的情况下跑完 ——
  这样出问题时能一眼分清是"检索/判定坏了"还是"模型坏了"。
  另两个项目都吃过"分不清哪一层坏了"的亏。
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

# ★ 独立临时库（见 smoke.py 的同名说明）：测试不能碰真实数据，
#   也不能因为服务在跑就删不掉自己的库。
os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test2_")) / "test.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import classify, config, llm, pipeline, policy, retrieval, store  # noqa: E402
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


print("=" * 62)
print("冒烟测试 2 · 连接器 + 编排层（不调模型）")
print("=" * 62)

if config.DB_PATH.exists():
    config.DB_PATH.unlink()
store.init_db()

# ── 1. 准备一个"客户的资料目录" ────────────────────────────────────────
print("\n[1] 文件夹连接器：同步一个目录")
tmp = Path(tempfile.mkdtemp(prefix="kb_"))
(tmp / "制度").mkdir()
(tmp / "制度" / "费用报销管理办法.md").write_text(
    "标题: 费用报销管理办法\n权限: external\n生效: 2025-03-01\n---\n"
    "第3.2条 报销单经审批通过后，财务部应于5个工作日内完成付款。\n"
    "第3.3条 发票必须贴在报销单背面，拍照上传后提交。\n",
    encoding="utf-8",
)
(tmp / "员工手册.txt").write_text(
    "员工手册\n第1.1条 工作时间：上午九点至下午六点，中午休息一小时。\n"
    "第4.2条 年假：入职满一年享有5天，此后每满一年增加1天，最多15天。\n",
    encoding="utf-8",
)
(tmp / "内部").mkdir()
(tmp / "内部" / "报价底线.md").write_text(
    "标题: 报价底线\n权限: internal\n---\n最低可以打到 7 折，低于这个数要总监批。\n",
    encoding="utf-8",
)
(tmp / "扫描件.pdf").write_bytes(b"%PDF-1.4\n%fake")  # 假装是个解析不了的 PDF
(tmp / "note.docx").write_bytes(b"PK\x03\x04fake")     # 假装 docx（没装库）

conn = folder_conn.FolderConnector(tmp)
docs = conn.list_docs()
print(f"     扫到 {len(docs)} 个文件: {docs}")
check("列出了可处理的文件", len(docs) == 5, str(docs))
check("跳过了隐藏/无关文件", not any(d.startswith(".") for d in docs))

res = folder_conn.sync(conn, tenant_id="default")
print(f"     同步结果: {res.line()}")
# ★ 这里期望的是 3 不是 5。
#   测试目录里那两篇 note.docx / 扫描件.pdf 是**故意造的假文件**
#   （内容是 b"PK\\x03\\x04fake" 这种）。
#   装了 python-docx / pypdf 之后它们会解析失败 → 内容为空 → **被如实跳过**。
#
#   第一版期望 5，是因为那时候没装解析库：代码只能返回
#   "（未安装 pypdf，无法解析…）" 这种占位文字，于是它们也进了索引。
#   ★ 装了库之后的行为更对：**解析不了就不进索引**，
#     而不是往索引里塞一句"我读不了"——那种东西被检索到毫无价值，
#     还会稀释检索质量。
check("新文档入库（假文件被跳过）", res.added == 3, f"实际 {res.added}")
check("★★ 解析不了的文件被如实跳过", len(res.skipped) == 2, str(res.skipped))
if res.skipped:
    print(f"     跳过（如实报告，不假装读过）: {res.skipped}")

n_doc = store.one("SELECT COUNT(*) n FROM doc")["n"]
check("库里只有能解析的 3 篇", n_doc == 3, f"实际 {n_doc}")

# 权限识别
p = store.one("SELECT permission FROM doc WHERE title='报价底线'")
check("★ 权限头被识别（报价底线=internal）", p and p["permission"] == "internal", str(p))
p2 = store.one("SELECT permission FROM doc WHERE title='费用报销管理办法'")
check("权限头被识别（报销=external）", p2 and p2["permission"] == "external", str(p2))

# 标题识别
t = store.one("SELECT title FROM doc WHERE source_ref LIKE '%费用报销%'")
check("★ 用文件里的「标题:」当标题", t and t["title"] == "费用报销管理办法", str(t))

# ── 2. 增量同步：未变的跳过 ────────────────────────────────────────────
print("\n[2] 增量同步：内容没变就不重做")
res2 = folder_conn.sync(conn, tenant_id="default")
print(f"     {res2.line()}")
check("未变的不重做", res2.unchanged == 3, f"实际 {res2.unchanged}")
check("没有误报新增", res2.added == 0)
check("被跳过的仍然被跳过（不会突然冒出来）", len(res2.skipped) == 2, str(res2.skipped))

# ── 3. 变更 + 删除 ─────────────────────────────────────────────────────
print("\n[3] ★ 变更与删除（最容易出事故的两件事）")
# ★ 第一版这里删的是"费用报销管理办法"，结果 [5] 要问报销的事就查不到了 ——
#   测试自己把后续步骤的数据删了，却看起来像"功能坏了"。
#   所以专门放一篇临时文档来删，别动后面还要用的。
(tmp / "临时通知.md").write_text("临时通知\n本周五下午团建。\n", encoding="utf-8")
folder_conn.sync(conn, tenant_id="default")

(tmp / "员工手册.txt").write_text(
    "员工手册\n第1.1条 工作时间：上午九点半至下午六点半，中午休息一小时。\n",
    encoding="utf-8",
)
(tmp / "临时通知.md").unlink()  # 删掉这篇临时的
res3 = folder_conn.sync(conn, tenant_id="default")
print(f"     {res3.line()}")
check("识别出更新", res3.updated == 1, f"实际 {res3.updated}")
check("★ 识别出删除", res3.deleted == 1, f"实际 {res3.deleted}")

r_old = retrieval.search("团建通知", tenant_id="default")
check("★ 删掉的文档不再被检索到",
      not any("临时通知" in h.title for h in r_old.hits),
      str([h.title for h in r_old.hits]))
r_new = retrieval.search("上班时间", tenant_id="default")
check("★ 改过的内容已生效（九点半）",
      any("九点半" in h.text for h in r_new.hits),
      str([h.text[:40] for h in r_new.hits]))

# ── 4. 权限隔离 ────────────────────────────────────────────────────────
print("\n[4] ★ 权限隔离（这一块出过一个把内外搞反的 bug）")
r_ext = retrieval.search("最低能打几折", tenant_id="default", permission="external")
r_int = retrieval.search("最低能打几折", tenant_id="default", permission="internal")
print(f"     外部客户检索: {[h.title for h in r_ext.hits]}")
print(f"     内部员工检索: {[h.title for h in r_int.hits]}")
check("★ 外部客户查不到报价底线", not any("报价底线" in h.title for h in r_ext.hits))
check("内部员工能查到报价底线", any("报价底线" in h.title for h in r_int.hits))

# ★★ 这里补的是"内外搞反"那个 bug 的回归断言。
#   当时的错法：permission="internal" 被当成"只搜 internal 文档"，
#   于是**内部员工比外部客户看到的还少**。
#   这个 bug 在只测外部视角时完全发现不了 —— 所以必须单独测"内部能看到 external"。
r_ext_doc = retrieval.search("上班时间", tenant_id="default", permission="external")
r_int_doc = retrieval.search("上班时间", tenant_id="default", permission="internal")
check("外部客户能查到 external 文档", len(r_ext_doc.hits) > 0, str(len(r_ext_doc.hits)))
check(
    "★★ 内部员工也能查到 external 文档（不能比客户还少）",
    len(r_int_doc.hits) >= len(r_ext_doc.hits) and len(r_int_doc.hits) > 0,
    f"外部 {len(r_ext_doc.hits)} 条 / 内部 {len(r_int_doc.hits)} 条",
)
# ★ 这里第一版用了 search("折") —— 单字在 2-gram 索引里本来就不匹配
#   （中文 2-gram 不生成单字 token），所以断言失败。
#   这是分词方式的固有特性，不是 bug；用户也不会问一个字。
check("不过滤时能看到全部（含 internal）",
      any("报价底线" in h.title for h in retrieval.search("报价底线", tenant_id="default").hits))
check("单字查询命中不了（2-gram 的固有特性，如实记录）",
      len(retrieval.search("折", tenant_id="default").hits) == 0)

# ── 5. 编排层：正常路径（无模型 → 走原文直出降级）─────────────────────
print("\n[5] 编排层：问一个知识库里有答案的问题")
L.add_synonym("default", "咋填", "填写")
ans = pipeline.answer_question("报销单咋填啊", tenant_id="default", use_llm=False)
print(f"     判定: {ans.decision}  质量: {ans.quality}")
print(f"     理由: {ans.reason}")
print(f"     回答:\n{ans.text[:300]}")
check("产出了回答", bool(ans.text.strip()))
check("★ 降级为原文直出时不自动发", ans.decision != "auto", f"实际 {ans.decision}")
check("记了日志", ans.qa_log_id is not None)
check("降级被标记", ans.degraded, str(ans.problems))

row = store.one("SELECT * FROM qa_log WHERE id=?", (ans.qa_log_id,))
check("★ 日志里有原始问法", row and row["question_raw"] == "报销单咋填啊")
check("日志里有检索信号", row and bool(row["signals_json"]))
check("日志里有判定理由可供复盘", row and row["decision"] is not None)

# ── 6. 编排层：敏感问题（不调模型就转人工）────────────────────────────
print("\n[6] 编排层：敏感问题不调模型直接转人工")
for q, name in [("你们能便宜点吗", "报价"), ("我要退款", "退款"), ("我要投诉", "投诉")]:
    a = pipeline.answer_question(q, tenant_id="default", use_llm=False)
    check(f"「{q}」→ 转人工（{name}）", a.decision == "escalate", a.reason)
    check(f"   并说明未调模型", "未调模型" in a.reason, a.reason)

# ── 7. ★ 转人工的话术（评审意见 D6/D7）────────────────────────────────
print("\n[7] ★ 转人工话术：不暴露岗位/姓名，不解释自己")
a = pipeline.answer_question("我要投诉", tenant_id="default", use_llm=False)
print(f"     发给用户的是: {a.text!r}")
check("不说岗位", not any(w in a.text for w in ("财务", "法务", "售后")), a.text)
check("不说姓名/工号", "工号" not in a.text and "李姐" not in a.text, a.text)
check("★ 不解释自己（没有'我叫…'）",
      not any(w in a.text for w in ("我叫", "我查", "我已经", "我给你")), a.text)
check("话很短", len(a.text) <= 24, f"{len(a.text)} 字: {a.text}")

# ── 8. 降级链：没有模型时的行为 ────────────────────────────────────────
print("\n[8] 降级链")
check("无 key 时 available() 为假", not llm.available() or bool(config.LLM_API_KEY))

# ★ 第一版这里直接调 generate_answer 断言"返回空" ——
#   但那时候环境里没配 key，所以真的是"模型不可用"。
#   后来配上了 key，这条就变成"对空资料调真模型"，
#   而模型正确地回了〔无法回答〕 —— 断言反而失败。
#   教训：**测试要显式构造自己依赖的前置条件**，不能依赖环境恰好是什么样。
_saved_key, _saved_client = config.LLM_API_KEY, llm._client
config.LLM_API_KEY = ""
llm._client = None
gen_nomodel = llm.generate_answer("测试", "资料：无", ["《测试》"])
check("★ 模型不可用时返回空而不是抛异常", gen_nomodel.text == "", repr(gen_nomodel.text))
check("并如实报告原因（不假装答了）", bool(gen_nomodel.notes), str(gen_nomodel.notes))

raw = llm.raw_fallback([])
check("查不到时的兜底话术不含岗位", "财务" not in raw, raw)
raw2 = llm.raw_fallback([type("H", (), {"title": "员工手册", "source_ref": "", "text": "九点到六点。"})()])
check("★ 原文直出会明说这是原文（不假装是 AI 答的）", "原文" in raw2, raw2)
config.LLM_API_KEY = _saved_key
llm._client = _saved_client

check("★ 模型说『无法回答』能被识别",
      llm.is_unanswerable("〔无法回答〕") and not llm.is_unanswerable("审批过了5个工作日"),
      "标记识别有问题")

# ── 9. 反馈 → 学习闭环 ─────────────────────────────────────────────────
print("\n[9] ★ 反馈闭环：人工改一条草稿，系统立刻学到")
a2 = pipeline.answer_question("年假能休几天", tenant_id="default", use_llm=False)
before_n = len(L.style_examples("default", limit=50))
pipeline.operator_edit(a2.qa_log_id, "满一年 5 天，之后每年加 1 天，最多 15 天。")
# ★ 学到的示例默认 pending，要过闸门才生效（见 smoke3 与决策记录 D2）。
#   没有评测集时 promote 会直接放行并标注"未经验证"。
LE.promote_pending("default", use_llm=False)
after_n = len(L.style_examples("default", limit=50))
print(f"     风格示例: {before_n} → {after_n}")
check("★ 人工改稿立刻变成风格示例", after_n > before_n, f"{before_n} → {after_n}")

row2 = store.one("SELECT outcome, failure_reason, edited_to FROM qa_log WHERE id=?", (a2.qa_log_id,))
check("结果被标为 edited", row2 and row2["outcome"] == "edited", str(row2))
check("失败原因被标为 bad_style", row2 and row2["failure_reason"] == "bad_style", str(row2))
check("保留了人工最终版本", row2 and "满一年" in (row2["edited_to"] or ""), str(row2))

fb = store.rows("SELECT * FROM feedback WHERE qa_log_id=?", (a2.qa_log_id,))
check("反馈也记了一条", len(fb) == 1, str(len(fb)))

# ── 10. 答不上来的问题自动沉淀 ─────────────────────────────────────────
print("\n[10] ★ 答不上来的问题自动沉淀成待补充")
for _ in range(3):
    pipeline.answer_question("团建费能不能报啊", tenant_id="default", use_llm=False)
gaps = L.open_gaps("default")
top = [g for g in gaps if "团建" in g["sample_question"]]
print(f"     待补充列表前 3: {[(g['sample_question'][:14], g['occurrences']) for g in gaps[:3]]}")
check("不会答的问题进了待补充列表", len(top) == 1, str(len(top)))
if top:
    check("★ 同类问题合并计数", top[0]["occurrences"] == 3, str(top[0]["occurrences"]))
# ★★ 这条是截图里发现的问题：意图转人工（投诉/报价）质量也是 0，
#   第一版被误判成"缺知识"，于是"我要投诉"堆到了待补充列表前排。
#   但投诉本来就该转人工，补知识库补不出"投诉答案"，
#   把它列进去会把管理员带偏。
bad = [g for g in gaps if any(w in g["sample_question"] for w in ("投诉", "便宜", "退款", "合同"))]
check("★★ 故意不答的（投诉/报价/退款）不能进待补充列表", len(bad) == 0,
      str([g["sample_question"] for g in bad]))

# ── 11. 失败原因分布 ───────────────────────────────────────────────────
print("\n[11] 失败原因分布")
b = L.analyze("default")
for line in b.lines():
    print(f"     {line}")
check("能分出多种失败原因", len(b.by_reason) >= 2, str(b.by_reason))

# ── 12. ★ 接入时的内容预扫描（被一次测试逼出来的）────────────────────
print("\n[12] ★ 接入预扫描：标着「可对客户说」的文档里有没有敏感内容")
rep = classify.audit("default")
print(f"     共 {rep['total']} 篇，干净 {rep['clean']} 篇，"
      f"有提示 {len(rep['risky'])} 篇（其中标着可公开的 {rep['severe_count']} 篇）")
for r in rep["risky"][:5]:
    mark = "★高危" if r["severe"] else "  仅内部"
    print(f"     {mark} {r['title']}: " + "；".join(f.line() for f in r["flags"]))
check("能扫出敏感内容", len(rep["risky"]) >= 1, str(rep["risky"]))
check("★ 能区分『会真的发给客户』和『仅内部』", rep["severe_count"] >= 1, str(rep))
check("给出的是提示不是判决", "你判断" in classify.advice(), classify.advice()[:40])

print("\n" + "=" * 62)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 62)
sys.exit(1 if FAIL else 0)