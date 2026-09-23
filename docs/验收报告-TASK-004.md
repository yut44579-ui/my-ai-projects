# 验收报告 · TASK-004（自然语言对话入口）

> 施工：Claude Code · 日期：2026-09-23 · 依据：`docs/施工指令-TASK-004.md`（链路 / 3 个 Intent / 铁律 /
> AC-01..AC-10 / 硬约束）+ 会话交接指令
> 证据文件：`outputs/_chat_e2e.log`（真 HTTP + 真 DeepSeek，111 项断言）、
> `outputs/_chat_web_check.log`（真 Edge 无头渲染，25 项）、`outputs/sra_web_check_*/dom_*.html`（渲染后的真 DOM）

## Status

**PASS**（全量 pytest 169 绿 + 真 HTTP/真 DeepSeek 闭环 111 项全过 + 真 Edge 渲染 25 项全过）

**唯一没做的一项**：AC-07 里"用 CDP 点按实测"按项目约定归 Hermes（本项目不装 Playwright）。
我这边用 Edge 自带 `--headless --dump-dom` 把 JS 执行完的 DOM 抓下来做了**等价的渲染层验收**（见下面 AC-07）。

---

## 📊 关键结果（数字）

| 项目 | 数字 |
|---|---|
| 全量 pytest | **169 passed**（基准 121 → 本次 +48，`tests/test_chat.py` 40 个函数 / 48 个用例） |
| 真 DeepSeek 闭环（`scripts/chat_e2e.py`） | **111 项断言全过，0 失败**，耗时 251s，末行 `🎉 TASK-004 自然语言闭环全部通过` |
| 真 Edge 无头渲染 | **25 项断言全过**，DOM 里没有 `undefined` / `NaN` / `[object Object]` |
| **2011 年 11 月销售额** | **1,509,496.33**（与**直接调 `executor.compute_sales_amount`** 位级相等 `==`，不是 approx） |
| 11-21~11-27 那一周 | **316,412.16**（全精度 `316412.16000000003`；趋势的周桶与直接调 executor 一致） |
| 三条路径互相对账 | 趋势各桶之和 `1509496.33` == 汇总 `1509496.33` == 排行口径总额 `1509496.33` |
| **LLM 写出的数字个数** | **0**（真模型在【为什么】【建议行动】两段里一个阿拉伯数字都没写，提示词真的管住了） |
| 单次提问耗时 | 冷启动首次 8~9 秒（含读表 100 秒在服务进程首次请求时付掉），热态 7~10 秒（一次 `ask` 调两次 LLM：解析 + 组织） |
| 数据集画像（从真数据算） | 541909 行 / 8 列 / 2010-12-01 ~ 2011-12-09 / 38 国 / 4372 客户 / 4070 个 StockCode |

---

## 本次实际做了什么

### 后端（新增，`app/api.py` 只动 2 行）

| 文件 | 行数 | 做了什么 |
|---|---|---|
| `app/ai/llm.py` | 220 | DeepSeek 客户端，**全项目唯一的网络出口**。key 只从 `DEEPSEEK_API_KEY` 读；`content` 为空回退 `reasoning_content`（推理模型）；异常统一收成 `LLMError`；`status()` 只说"配没配"，**永不回声 key** |
| `app/ai/intent.py` | 518 | 问题 → Intent JSON。4 个 intent（3 个可算 + unsupported）、参数 Pydantic 校验（`extra="forbid"`）、`_BANNED_DIMENSIONS` **代码级闸门**（12 类区域/省份/门店/毛利关键词在 LLM 之前就拦）、中文/ISO 日期与区间解析、关键词降级 |
| `app/ai/tools.py` | 500 | 白名单工具（**恰好 3 个**）。`_valid_window()` 逐行复刻 executor 的 D16 掩码；`sales_summary` 直接调 `executor.compute_sales_amount` 并带自检（`bit_identical` / `executor_checks_all_passed`）；`sales_trend` 按日/按周（周一为起点）+ 跨区间的桶标 `partial`；`top_products` 分组排序 + 占比 |
| `app/ai/answer.py` | 405 | 分区回答 + **数字闸门**。【发生了什么】由代码生成（**不让 LLM 复述事实**）；LLM 只写【为什么】【建议行动】；两段都过闸——出现任何"不在确定性结果集合里"的数字 → **整段作废**换成代码降级文案，违规数字记进 `guard.violations` 一起落盘 |
| `app/ai/service.py` | 249 | 编排 + `_record()`（**唯一**的记录组装出口）+ 诚实的数据边界 `notice` |
| `app/api_chat.py` | 164 | 4 个新端点：`GET /api/chat/capabilities`、`POST /api/chat`、`GET /api/conversations`、`GET /api/conversations/{id}` |
| `app/state.py` | +函数 | `record_conversation` / `get_conversation` / `list_conversations` / `count_conversations`（走既有 Repository 抽象，没绕过去） |
| `app/repositories/__init__.py` | +接线 | `ConversationRepository` 第 5 个接口 + Json 实现（复用既有原子写） |

### 前端（原生 JS/CSS，无框架）

| 文件 | 加了什么 |
|---|---|
| `web/api.js` | 4 个聊天端点（`chatCapabilities` / `chat` / `listConversations` / `getConversation`），复用既有 `request`/`json`/`query`/`ApiError` |
| `web/index.html` | **顶栏自然语言入口启用**（去掉 `disabled`）+ hero 输入框 + 三个示例 chip（问题原文写在 `data-question` 上，不在 JS 里另抄一份）+ 链路面板（5 个可折叠环节）+ 历史提问卡 + 右面板「AI 结论」卡改成真数据挂点 |
| `web/app.js` | +516 行：`askQuestion`（三态）/ `renderChat`（5 个环节 + 空环节自动收起）/ `renderChatHistory` / `renderAiConclusion` / `openConversation` / `bindChat`；`nl-note` 文案由后端能力清单驱动（页面不写死"支持三类问题"） |
| `web/style.css` | +92 行，**只加结构**：配色/圆角/字号全部复用既有 token 与 `.badge`/`.empty`/`.code`/`.table`/`.table-wrap` 等既有组件，没为聊天另造一套视觉 |

### 脚本

| 文件 | 做什么 |
|---|---|
| `scripts/chat_e2e.py`（426 行） | 真 uvicorn + 真 HTTP + 真 DeepSeek。**脚本进程内自己直接调 executor 拿参考值**，再跟 HTTP 回来的 `facts.sales_amount` 比**位级相等** |
| `tests/test_chat.py`（755 行） | 48 个用例。被替换的只有 `llm.chat` 这个**网络出口**（单测不能靠外网），**一个业务数字都没有假造** |
| `outputs/_chat_web_check.py` | 一次性：用本机真 Edge 无头渲染，验 JS 真的把响应画进了 DOM |

---

## 验收证据（AC 逐条）

### AC-01 真问真答，数字与直接调 metrics 逐位一致 ✅

真 DeepSeek，三个 Intent 各真问一次（`outputs/_chat_e2e.log`）：

```
【3】「2011年11月一共卖了多少？」 → intent=sales_summary  params={2011-11-01, 2011-11-30}
     ✅ AC-01：销售额与直接调 executor 逐位相等 | 1509496.33 == 1509496.33
     ✅ 工具自检：同一套 D16 掩码独立复算 → 与 executor 逐位一致
【4】「2011-11-01 到 2011-11-30 的销售趋势，按周看」 → sales_trend / granularity=week
     ✅ 趋势各桶之和 == 汇总（两条代码路径对得上）| 1509496.33 ≈ 1509496.33
     ✅ 11-21~11-27 那一周 == 直接调 executor 算的那一周 | 316412.16000000003 ≈ 316412.16000000003
【4】「2011年11月卖得最好的5个产品」 → top_products / top_n=5
     ✅ 排行口径的总额 == 直接调 executor（同一区间）| 1509496.33 == 1509496.33
     ✅ 每条占比 == 该条金额 / 区间总额（页面上的百分比能回算出来）
```

参考值不是从别处抄的常量：**脚本进程内直接 `executor.compute_sales_amount(...)` 算出来**，
与 `app/ai/` 一行关系都没有 —— 这就是"直接调 metrics"的字面含义。

### AC-02 解析失败 → 明确提示（不瞎猜、不乱算）✅

真模型问「今天天气怎么样？」→ `status=unsupported`、`tool=None`、`facts=None`、没有夹带任何金额（e2e 日志【5b】）。
另外 4 种"必须拒绝"的情形在 `tests/test_chat.py` 里钉死：解析不出来（`intent_unparseable`，含"没听懂"且提示里不夹带数字）、
LLM 给了白名单外的 intent、意图合法但参数非法（多传字段必须被点名）、结束日期早于起始日期。

### AC-03 数据不支持的维度 → 明确告知，绝不用 Country 顶替 ✅

```
【5】「华南区2011年11月卖了多少？」 → status=unsupported / 没有调用任何工具 / 没有产出任何事实数字
     ✅ 回答里如实说明数据集没有区域字段
     ✅ 并且点名「Country 不能当区域用」
     ✅ 拒绝的回答里没有夹带任何金额
```
`GET /api/chat/capabilities` 也把边界摆给前端：`data_profile.has_region_field = false`，
`unsupported.reason` 明写"**不会用 Country 代替区域**"。
12 类关键词（区域/大区/片区/省份/城市/门店/渠道/销售员/部门/毛利/成本/折扣）在 `_BANNED_DIMENSIONS` 里
**先于 LLM** 被拦，单测参数化覆盖。

### AC-04 LLM 不参与算数（证据：数字 === metrics 输出）✅

三层落地，每一层都有取证点：
1. **【发生了什么】这一段由代码写** —— 不让 LLM 复述事实，因为它一复述就有改写数字的机会（e2e 断言 `source == "code"`，文本里就是 `1,509,496.33`）；
2. **数字闸门**：LLM 的两段里任何数字必须能在"确定性结果集合"里找到出处，否则**整段作废**（单测用 monkeypatch 让模型写 `999999.99`，断言这一段被丢掉且 `guard.violations` 记下来）；
3. **真模型实测**：`✅ 模型写的那两段里一个阿拉伯数字都没有 | found=[]`。

### AC-05 无 key / LLM 失败 → 降级且不编造 ✅

单测覆盖 4 条：没 key、`llm.chat` 抛错、`content` 空但 `reasoning_content` 有内容（推理模型）、两者都空（`llm_empty_response`）。
真服务另跑一次 `use_llm=false` 强制降级（e2e【7】）：`status=degraded`、`llm.used=false`、
**金额照样逐位一致**（数字本来就不是模型算的）、三段全部 `source=code`、闸门如实标 `checked=false`（不假装核过）。

### AC-06 对话落盘 + 刷新后可查 ✅

```
✅ state/conversations.json 真的落盘了
✅ 盘上那条记录带着完整链路 | keys=[answer, conversation_id, created_at, data_profile, error,
                                    facts, intent, items, llm, notice, params, parse, question, series, status, tool]
✅ GET /api/conversations/{id} 与提问时的响应逐字段一致（刷新恢复的真相来源）
✅ 换一条新连接重新 GET 能读回同样的记录
✅ 落盘的记录里没有 API key
```
**浏览器侧也验了**（不只接口）：先真提一问，再让 Edge 重新加载页面 —— 页面从后端把那条记录取回来，
五个环节、事实表（含 `1,509,496.33`）、三段回答、历史列表、右面板结论全部重新画出来。

### AC-07 前端可操作 ✅（渲染层已实测；点按实测留给 Hermes CDP）

本机真 Edge 无头渲染（`outputs/_chat_web_check.log`，25 项全过）：
空态（能力清单文案由 JS 填入）→ 真提一问 → 重载页面 → **链路面板展开、5 个环节全画出来、
事实表里就是后端算的那个数、三段回答、历史行带「看链路」按钮、右面板接着最近一次渲染**，
且整份 DOM 里没有 `undefined` / `NaN` / `[object Object]`。

Hermes 起服务复验（一次提问约 8 秒）：

```bash
cd /d/sales-report-agent && ./.venv/Scripts/python.exe scripts/serve.py --port 8500
# 浏览器开 http://127.0.0.1:8500/  → 顶栏自然语言入口不再 disabled
```

### AC-08 无 mock / 假数据 / 硬编码业务数字 / 假 ID ✅

- 单测扫 `app/ai/*.py`：搜不到 `316412.16` / `541909` / `19950` / 数据 SHA256 这些业务数字字面量，也搜不到 key 字面量；
- 前端扫 `web/*`：没有 `setTimeout` / `mock` / `TODO` / `占位` / `已开启`，也没有 `316,412.16` / `19950`；
- 白名单恰好 3 个工具（多一个都断言失败）；`app/ai/` 不 import `app/engine` 之外的业务层，依赖方向单向；
- 数据集画像是**从真数据算的**（行数/列名/时间边界逐项与 `loader.load_raw()` 比对），不是写死的常量。

### AC-09 Legacy Contract：既有端点语义 + 冻结资产零改动 ✅

```
✅ /api/health 的 state 块键集与冻结版逐字一致 | [executions, readable, schema_version, state_dir, tasks, uploads]
✅ 问答没有污染任务/执行计数
✅ 路径总数 = 14 个冻结 + 4 个新增（没有别的搭车）| 18
✅ 老路径仍在：/api/{health,upload,schema,execute,tasks,tasks/{id},tasks/{id}/run,tasks/{id}/runs,
                executions,download/{id},documents,documents/{id},documents/{id}/text,documents/{id}/summary}
```
`metrics/executor/renderer/loader` **一个字节没动**；`app/api.py` 只加 1 行 import + 1 行 `include_router`
（位置在 `mount("/")` 之前）；错误体仍是 api.py 的统一形状（`ChatApiError` 靠鸭子类型接住，没有循环 import）。

### AC-10 全量 pytest 仍全绿 ✅

```
169 passed, 1 warning in 49.54s
```
（基准 121 + 本次新增 48 = 169；热跑 50 秒，冷跑约 5.5 分钟 —— 慢的是真上传解析 23MB Excel）

---

## ⚠️ 踩到的坑（分清"实现真缺陷"和"我自己写错的断言"）

**实现真缺陷（6 个，都改了实现，不是改断言）：**

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 日期反过来报 `tool_failed` 而不是"参数非法" | `validated_params()` 从没调 `resolved()` | 校验时顺带查起始是否晚于结束 |
| 2 | 「2011年11月21日到11月27日」被**静默**当成整个 11 月算（1509496.33） | 关键词解析只认 `YYYY-MM` | 补 ISO 与中文日期/区间正则 + 年份继承 + 夹到数据边界 |
| 3 | 数字闸门放行了 `D16` 里的 `16` | 正则把字母后的数字也当 token | 加负向后顾 `(?<![A-Za-z])` |
| 4 | 周桶会溢出问句区间（问 11 月，桶标签是 10-31） | 周聚合按自然周切，不问区间 | **保留真实周标签**但标 `partial` + `covered_start/end/days`，代码段写明「不完整」（不把标签裁进区间——那会让人以为是两个短周） |
| 5 | 拒绝回答时说"（未接 LLM —— …）"，但 LLM 明明配着 | 两种"没说话"的原因混成了一句 | 拆开：没接 LLM / 没有可依据的事实 |
| 6 | `_empty_window_note` 直接 SyntaxError | f-string 里混了 ASCII 双引号 | 换成「」 |

**我自己写错的断言（5 个，实现是对的 —— 都先读真实记录核对过才改）：**

| # | 我原本断言 | 真相 |
|---|---|---|
| 7 | `D16_AMOUNT == 316412.16` | 全精度是 `316412.16000000003`，用 `approx(abs=1e-6)`（对 executor 的那条仍用位级 `==`） |
| 8 | 11 月按日应该有 7 个周桶 | **2011-11-26 原始数据里零行**（直接 pandas 核对过）—— 数据事实，不是过滤缺陷 |
| 9 | `test_web` 断言自然语言入口**必须 disabled** | 那是 TASK-003 时代的契约；TASK-004 的交付物就是启用它 → 反过来断言"不再禁用" |
| 10 | e2e 三条：桶不许溢出 / 前 5 名之和 == 区间总额 / 记录数 +1 | 都是我的断言错：桶该带 `partial` 标记；前 5 名只占总额 8.53%（当然不等于总额）；`asked` 计数器算错了 |
| 11 | Edge 抓的 DOM 里不该出现"还没有提问" | **`hidden` 的元素文字仍在 DOM 里** —— 只能验 `hidden` 属性，不能验"文字不存在" |

---

## ⚠️ 需决策 / 遗留问题

1. **`top_products` 第 1 名是 `DOT / DOTCOM POSTAGE`（邮费编码，不是商品）**，第 4 名之后还有人工调整类编码。
   现在做法：**不过滤，但在口径说明里明确标"未过滤非商品编码"，LLM 也如实解释了**。
   要过滤就得维护一张黑名单（等于往代码里塞硬编码业务规则）—— 请你定：不过滤 / 加黑名单 / 让 LLM 判断（我倾向前两个里选，别让模型碰这个）。
2. **相对时间**（"上个月卖了多少"）现在是 **LLM 解析并回显**成绝对区间的，
   而 `DECISIONS.md` 把"相对时间解析"归在 TASK-007。当前实现能用且会在回答里回显绝对区间（可核对），但**归口需要确认**。
3. **没有 `requirements.txt`**。本次新增两个依赖：`openai==3.19.0`、`python-dotenv==1.2.3`
   （`.env` 与 `.gitignore` 都已就位，key 从未进代码/日志/响应）。
4. 指令里写"既有 **12** 端点"，实际冻结的是 **14** 个（TASK-002C/002D 之后又多了一个）。
   e2e 按真实集合断言 `14 + 4 = 18`。如果 Gate 的账是 12，那是文档口径需要对齐。
5. **TASK-003 与 TASK-004 都还没提交**（working tree 里 `?? app/ai/`、`?? app/api_chat.py`、`?? tests/test_chat.py`、
   `?? scripts/chat_e2e.py` + 11 个 `M`）。要不要我提交、怎么拆 commit，等你一句话。
6. 一次 `ask()` 调**两次** LLM（解析 + 组织），热态 7~10 秒/问。
   合并成一次调用能省一半时间，但会让"解析"和"组织"共用同一段输出 —— 把关反而变难。**我建议保持两次**，不省。

---

## 复用情况（没有造新轮子）

| 能力 | 复用什么 | 没做什么 |
|---|---|---|
| 算数 | `engine/{loader,metrics,executor}` 原样调用，`_valid_window()` 逐行复刻它的 D16 掩码以便独立复算 | 没另写一套口径，没碰冻结资产一个字节 |
| 落盘 | `state.py` + Repository 抽象 + json_store 原子写（第 5 个仓库，复刻既有 4 个的写法） | 没自己开文件句柄、没绕开抽象 |
| LLM 通道 | `openai` SDK（DeepSeek 兼容端点）+ `python-dotenv` | 没自己搓 HTTP/重试/环境变量解析 |
| 错误体 | `api.py` 的统一错误体（`ChatApiError` 鸭子类型） | 没在聊天端点里另造一套错误格式 |
| 新能力挂载 | 复刻 `api_documents.py` 的"新文件 + 新路径 + api.py 只加两行、mount 之前"套路 | 没往 `api.py` 里塞端点 |
| 前端 | `api.js` 的 `request/json/query/ApiError`、`.badge/.empty/.code/.table/.table-wrap`、三态与错误条 | 没引框架、没为聊天单造视觉、没写死能力文案 |
| 浏览器验收 | **本机 Edge 自带 `--headless --dump-dom`** | 没装 Playwright（用户拍板过） |
