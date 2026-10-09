"""真模型端到端验证：走 HTTP 接口，真的调 DeepSeek。

★ 用 Python 发请求，不用 PowerShell ——
  PowerShell 的 ConvertTo-Json 会把中文请求体搞乱，
  之前因此误判过"AI 全是答不上来"，其实是测试方法的问题。
"""

from __future__ import annotations

import json
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8600"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 64)
print("真模型端到端验证（走 HTTP，真调模型）")
print("=" * 64)

h = get("/api/health")
print(f"\n健康：资料 {h['docs']} 篇 · 片段 {h['chunks']} 个 · 模型就绪 {h['model_ready']}")

CASES = [
    # (问题, 是否内部, 期望判定, 说明)
    ("报销单咋填啊", False, None, "口语问法，资料里有答案"),
    ("上班时间是几点", False, None, "最简问题，应该一句话答完"),
    ("年假能休几天", False, None, "资料里有具体数字"),
    ("住宿费一晚能报多少", False, None, "需要跨到差旅费标准"),
    ("你们能便宜点吗", False, "escalate", "★ 报价类，不该调模型"),
    ("我要投诉", False, "escalate", "★ 投诉类，直接转人工"),
    ("团建费能不能报啊", False, None, "★ 资料里没有，应如实说答不了"),
    ("住宿费能报多少", True, None, "同样的问题，内部员工问"),
]

saved: list[tuple[int, str, str]] = []
for q, internal, expect, note in CASES:
    d = post("/api/ask", {"question": q, "is_internal": internal, "use_llm": True})
    who = "内部" if internal else "客户"
    flag = ""
    if expect and d["decision"] != expect:
        flag = f"  ❌ 期望 {expect}"
    print(f"\n【{who}】{q}   （{note}）")
    print(f"  判定 {d['decision']} · 质量 {d['quality']} · 意图 {d['intent'] or '-'}")
    if d.get("rewrite") and d["rewrite"] != q:
        print(f"  改写 → {d['rewrite'][:70]}")
    print(f"  回答：{d['text'][:220]}")
    if d.get("citations"):
        print(f"  出处：{'、'.join(d['citations'])}")
    if d.get("problems"):
        print(f"  出站检查：{'；'.join(d['problems'])}")
    print(f"  理由：{d['reason']}{flag}")
    saved.append((d["qa_log_id"], q, d["text"]))

# ── 反馈闭环：把一条改掉，看系统学不学 ────────────────────────────────
print("\n" + "=" * 64)
print("反馈闭环：人工改一条 → 看它有没有学到")
print("=" * 64)
target = next((s for s in saved if "上班" in s[1]), saved[0])
print(f"\n改这条：{target[1]}")
print(f"  AI 原话：{target[2][:120]}")
better = "九点到六点，中午休一个小时。"
post(f"/api/feedback/{target[0]}/edit", {"final_text": better})
print(f"  人工改成：{better}")

styles = get("/api/learning/styles")["items"]
print(f"\n  风格示例库现在有 {len(styles)} 条：")
for s in styles[:3]:
    print(f"    · 问：{s['question']}")
    print(f"      改成：{s['answer_after']}")
    print(f"      改了什么：{s['diff_summary']}")

# ── 再问一次同一句，看风格有没有生效 ──────────────────────────────────
print("\n  再问一次同一句，看回答有没有变：")
d2 = post("/api/ask", {"question": target[1], "use_llm": True})
print(f"    改之前：{target[2][:100]}")
print(f"    改之后：{d2['text'][:100]}")

# ── 学习看板 ──────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print("学习看板")
print("=" * 64)
rep = get("/api/learning/report")
print(f"\n共 {rep['total']} 条问答，自动/直接可用 {rep['ok']} 条（{rep['rate']:.0%}）")
print("失败原因分布（决定该修哪里）：")
for r in rep["by_reason"]:
    print(f"  {r['label']:<24} {r['count']} 条  → {r['fix']}")

gaps = get("/api/learning/gaps")["items"]
print(f"\n待补充问题 {len(gaps)} 条（按被问次数排）：")
for g in gaps[:5]:
    print(f"  {g['occurrences']} 次  {g['sample_question']}")

aud = get("/api/kb/audit")
print(f"\n接入预扫描：共 {aud['total']} 篇，标着可公开但有提示的 {aud['severe_count']} 篇")
for r in aud["risky"][:5]:
    print(f"  {'★高危' if r['severe'] else '  仅内部'} {r['title']}: {'；'.join(r['flags'])}")
