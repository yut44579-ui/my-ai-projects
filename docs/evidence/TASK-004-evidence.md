# TASK-004 AI 回复真实验收证据（由 scripts/collect_evidence_task004.py 自动采集，未手工编辑）

采集时间：2026-10-03 22:42:29　后端：http://127.0.0.1:8103（失败态：http://127.0.0.1:8104）

★ 顺序铁律：① 代码层 PolicyGate →（命中即转人工，不调 LLM）→ ② 调 LLM → ③ 回复落库。
★ 本文件里的 LLM 调用是**真实调用**（AC1）；AC2 那条恰恰证明没有调用；AC3 那条调的是坏地址。

### ① AC4 PolicyGate 独立单测（每类敏感词至少一条用例）

```
用例清单（每类敏感词一条，见 tests/test_policy_gate.py:SENSITIVE_CASES）：
  · BANK_ACCOUNT  ←  麻烦把款项打到我们公司银行账户
  · BANK_ACCOUNT  ←  对公账号发我一下
  · LEGAL  ←  不解决的话我们要走法律途径起诉
  · LEGAL  ←  需要你们出具担保
  · COMPENSATION  ←  这次延期你们要赔偿我们的损失
  · COMPENSATION  ←  违约金怎么算
  · REFUND  ←  产品不好用，我要退款
  · REFUND  ←  能不能全额退
  · PAYMENT  ←  付款方式能不能改成分期
  · PAYMENT  ←  账期能不能给到 60 天
  · CONTRACT  ←  合同条款里这条要改一下
  · CONTRACT  ←  本周可以签约吗
  · ABNORMAL_FUNDS  ←  这笔大额转账走账不太正常
  · ABNORMAL_FUNDS  ←  先帮我垫资一下
  · LARGE_ORDER  ←  我们准备下 50 万元的大额订单
  · LARGE_ORDER  ←  这是批量采购，量很大
  · COMPLAINT  ←  再不处理我就要投诉了
  · COMPLAINT  ←  我要去举报你们
  · SPECIAL_RESOURCE  ←  能不能给我开绿灯优先排产
  · SPECIAL_RESOURCE  ←  内部名额能不能留一个
  · KEY_CUSTOMER_PROMISE  ←  我们是大客户，你们得保证优先供货
  · KEY_CUSTOMER_PROMISE  ←  这是关键承诺吗
  · QUOTE  ←  请给我一份正式报价
  · QUOTE  ←  这个产品多少钱
  · DISCOUNT  ←  老客户有没有折扣
  · DISCOUNT  ←  能不能打个八折
```

### ② AC1~AC3 接口测试（含 monkeypatch 计数断言：敏感时 LLM 一次都没调）

```
======================== 15 passed, 1 warning in 1.29s ========================
```

### ③ pytest 全量（只增不减）

```
from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_customers_module.py::test_detail_empty_blocks_are_explicit_no_data
FAILED tests/test_health.py::test_health_reports_real_db_connection - Asserti...
```

### ④ 后端连通性（普通/敏感两条都采自这个进程）

```
GET /api/health → status=ok / db=biz_assistant_wt345 / connected=True
```

### ⑤ AC1 普通问题 → 真调 LLM → 回复落库 REPLIED

```
POST /api/customers/6677/ai-reply  {"content": "你们的售后支持时间是怎样的？"}
→ HTTP 200
{
  "customer_id": 6677,
  "question": "你们的售后支持时间是怎样的？",
  "llm_called": true,
  "policy": {
    "allowed": true,
    "reason": "未命中任何敏感类别",
    "category": null,
    "label": null,
    "matched": null,
    "tone": "NONE"
  },
  "ai_status": "REPLIED",
  "message": {
    "id": 270,
    "customer_id": 6677,
    "sender_type": "AI",
    "message_type": "CHAT",
    "content": "您好，感谢您的咨询。关于售后支持的具体时间安排，我需要与相关同事确认后再回复您，暂时无法直接告知。",
    "source_type": "SYSTEM",
    "evidence_ref": "customer_message:270",
    "ai_status": "REPLIED",
    "created_at": "2026-10-03T22:42:24"
  },
  "failure_reason": null
}
```

### ⑥ AC2 敏感问题 → 不调 LLM（llm_called=false）、落库 HUMAN_REQUIRED、无 AI 回复

```
POST /api/customers/6677/ai-reply  {"content": "老客户有没有折扣？能给我 8 折吗"}
→ HTTP 200
{
  "customer_id": 6677,
  "question": "老客户有没有折扣？能给我 8 折吗",
  "llm_called": false,
  "policy": {
    "allowed": false,
    "reason": "命中敏感类别「折扣」（命中词：折扣），按 D7 必须人工处理，不调用 LLM",
    "category": "DISCOUNT",
    "label": "折扣",
    "matched": "折扣",
    "tone": "EXECUTION"
  },
  "ai_status": "HUMAN_REQUIRED",
  "message": {
    "id": 271,
    "customer_id": 6677,
    "sender_type": "SYSTEM",
    "message_type": "NOTE",
    "content": "命中敏感策略「折扣」（命中词：折扣），已转人工处理；本轮未调用 AI。",
    "source_type": "SYSTEM",
    "evidence_ref": "customer_message:271",
    "ai_status": "HUMAN_REQUIRED",
    "created_at": "2026-10-03T22:42:24"
  },
  "failure_reason": null
}
```

### ⑦ 「建议」≠「执行」：建议语气放行（tone=SUGGESTION，真调 LLM）

```
{
  "policy": {
    "allowed": true,
    "reason": "命中「折扣」但属于建议语气（命中词：9 折），未构成对外承诺，按 D7 放行",
    "category": "DISCOUNT",
    "label": "折扣",
    "matched": "9 折",
    "tone": "SUGGESTION"
  },
  "llm_called": true,
  "ai_status": "HUMAN_REQUIRED"
}
```

### ⑧ 该客户时间线（AI 回复 / 转人工说明都真落库，条数走 EvidenceValue）

```
id=270 sender=AI ai_status=REPLIED evidence_ref=customer_message:270 | 您好，感谢您的咨询。关于售后支持的具体时间安排，我需要与相关同事确认后再回复您，暂时无法直接告知
  id=271 sender=SYSTEM ai_status=HUMAN_REQUIRED evidence_ref=customer_message:271 | 命中敏感策略「折扣」（命中词：折扣），已转人工处理；本轮未调用 AI。
  id=272 sender=SYSTEM ai_status=HUMAN_REQUIRED evidence_ref=customer_message:272 | AI 草稿命中敏感策略「折扣」（命中词：折扣），草稿已拦截、未对外发出，请人工处理。

total = {"value": 3, "state": "VALID", "source_type": "SYSTEM", "evidence_ref": "customer_messages:count|customer_id=6677", "reason": null}
```

### ⑨ AC3 制造 LLM 失败 → 502 + 人工兜底文案 + 落库 FAILED（库里无假回复）

```
（失败态后端 http://127.0.0.1:8104，其 DEEPSEEK_BASE_URL 指向连不上的地址）
POST /api/customers/6677/ai-reply  {"content": "请问你们公司的服务网点有哪些？"}
→ HTTP 502
{
  "error": "llm_unavailable",
  "message": "AI 暂时无法回复，请人工处理",
  "customer_id": 6677,
  "message_id": 273,
  "ai_status": "FAILED",
  "reason": "LLM 返回 HTTP 502"
}
```

### ⑩ 直接查库：AI 相关消息（ai_status<>NONE）+ CUSTOMER 行数必须为 0（D9）

```
(273, 6677, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:273', 'FAILED', 'AI 暂时无法回复，请人工处理（原因：LLM 返回 HTTP 502）')
(272, 6677, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:272', 'HUMAN_REQUIRED', 'AI 草稿命中敏感策略「折扣」（命中词：折扣），草稿已拦截、未对外发出，请人工处')
(271, 6677, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:271', 'HUMAN_REQUIRED', '命中敏感策略「折扣」（命中词：折扣），已转人工处理；本轮未调用 AI。')
(270, 6677, 'AI', 'CHAT', 'SYSTEM', 'customer_message:270', 'REPLIED', '您好，感谢您的咨询。关于售后支持的具体时间安排，我需要与相关同事确认后再回复您，')
(217, 3290, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:217', 'FAILED', 'AI 暂时无法回复，请人工处理（原因：LLM 返回 HTTP 502）')
(215, 3290, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:215', 'HUMAN_REQUIRED', '命中敏感策略「折扣」（命中词：折扣），已转人工处理；本轮未调用 AI。')
(214, 3290, 'AI', 'CHAT', 'SYSTEM', 'customer_message:214', 'REPLIED', '李娜您好，感谢您的咨询。关于售后支持时间，我需要向相关同事确认后再回复您，以免给')
(213, 5501, 'SYSTEM', 'NOTE', 'SYSTEM', 'customer_message:213', 'FAILED', 'AI 暂时无法回复，请人工处理（原因：LLM 返回 HTTP 502）')

sender_type='AI' and ai_status='REPLIED' 的行数 → 3
sender_type='CUSTOMER' 的行数 → 0
```

### ⑪ 页面真实发出的接口调用（普通问题 + 敏感问题，docs/evidence/task004-api-calls.json）

```
200  http://127.0.0.1:5183/api/customers/3290/timeline
200  http://127.0.0.1:5183/api/customers/3290/timeline
200  http://127.0.0.1:5183/api/customers/3290
200  http://127.0.0.1:5183/api/customers/3290/messages?page_size=100
200  http://127.0.0.1:5183/api/customers/3290/messages?page_size=100
200  http://127.0.0.1:5183/api/customers/3290/ai-reply
200  http://127.0.0.1:5183/api/customers/3290/timeline
200  http://127.0.0.1:5183/api/customers/3290
200  http://127.0.0.1:5183/api/customers/3290/ai-reply
200  http://127.0.0.1:5183/api/customers/3290/messages?page_size=100
```

### ⑪ 页面真实发出的接口调用（LLM 失败，docs/evidence/task004-api-calls-failed.json）

```
200  http://127.0.0.1:5184/api/customers/stats?
200  http://127.0.0.1:5184/api/customers/stats?
200  http://127.0.0.1:5184/api/customers?page=1&page_size=20
200  http://127.0.0.1:5184/api/customers/3290/timeline
200  http://127.0.0.1:5184/api/customers/3290
200  http://127.0.0.1:5184/api/customers/3290/timeline
200  http://127.0.0.1:5184/api/customers/3290/messages?page_size=100
200  http://127.0.0.1:5184/api/customers/3290/messages?page_size=100
502  http://127.0.0.1:5184/api/customers/3290/ai-reply
200  http://127.0.0.1:5184/api/customers/3290
```

### ⑫ 页面截图（真实浏览器，见 docs/screenshots/）

```
task004-ai-failed-fallback.png
task004-ai-human-required.png
task004-ai-reply.png

· task004-ai-reply            普通问题：AI 气泡（AI 已回复）+ evidence_ref
· task004-ai-human-required   敏感问题：转人工提示 +「需人工处理」系统气泡（未调用 AI）
· task004-ai-failed-fallback  LLM 失败：「AI 暂时无法回复，请人工处理」+ 时间线 FAILED 行
```
