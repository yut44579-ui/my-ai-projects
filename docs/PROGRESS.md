# PROGRESS.md · sales-report-agent

## 2026-09-22 凌晨（用户休息，Hermes 自动执行）

### 已完成
| 时间 | 事项 | 结果 |
|---|---|---|
| 04:1x | ChatGPT 外部评审 R001/R002/R003 | 方向+拆解+施工方案全部通过（reviews/001-003） |
| 04:30 | TASK-002A 施工指令 v5（含 D16 口径） | 评审 PASS，可交 Claude |
| 05:0x | **TASK-002A 由 Claude Code 完成** | `app/engine/{loader,metrics,executor}.py` + `tests/test_executor.py` |
| 05:1x | **Hermes 独立门禁 + 独立核对** | GATE-1/2/6 + 数据 SHA256 全过；**自写 pandas 实现 vs executor 差值 0.0** |
| 05:2x | Git commit | `8fc01a2` |

### 关键数字（可复核）
```
区间 2011-11-21 ~ 2011-11-27：
  原始行数 19950 / 排除 296（C开头230 + Qty≤0 260 + 单价≤0 66，多规则命中 260）
  有效行 19654 / 销售额 £316,412.16 / 排除净额 -8,227.14
  数据 SHA256 43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d
```

### 进行中
- TASK-002B（Excel 渲染，openpyxl 原地改）
- 待办：002C（API）→ 004A（★ 前端，用户最关心）

### 流程（已固化）
```
用户提需求 → Hermes 出方案 → ChatGPT 评审 → Claude CLI 写代码 → Hermes 独立门禁验收 → git commit
```

## 2026-09-25 · STEP-C 账号审批 + 游客限制（TASK-STEP-C / D21）

| 项 | 结果 |
|---|---|
| 真服务重启后账号仍在 | `scripts/stepc_accounts_e2e.py` **34/34**（kill 掉真 uvicorn 再拉起，两个账号照旧能登） |
| 游客真的被拦住（真浏览器） | `scripts/stepc_guest_cdp.mjs` **55/55**（点受限入口前后 `window.fetch` 次数 10 → 10） |
| 游客被拦住（服务端） | 管理三条端点：游客 401 / 普通账号 403（不是靠前端不显示按钮） |
| 前端受限行为 | `scripts/session_check.mjs` **253/253**（`Session.guard()` 逐个动作要 false） |
| 单测 | 新增 `tests/test_auth_admin.py`（19 条）、`tests/test_web_guest.py`（10 条） |

产物：`account` 记录多了 `status / role / reviewed_at / reviewed_by`；
`DELETE /api/auth/accounts/{u}`；`web/session.js` 的 `guard()/mark()/applyGuards()`；
`index.html` 的 `#guest-bar` / `#guard-modal` / `#set-accounts-card`。

## 2026-09-25 · 排序语义 + 时间范围误导（用户报的两个问题 / GPT 冻结规格）

用户原话：「指标按销售额 或者订单的其他某个限制词时 他的顺序也没有对上 会有 127 的订单数排在
147 的前面」+「时间范围是 2026 年的某月某天到某个时间点 但是他会弹出 2010 的时间是对不上的」。

| 项 | 结果 |
|---|---|
| 全量单测 | `pytest` **537 passed**（新增 30 条后端回归 + 4 条前端静态，基线 503 全绿） |
| 真浏览器交互实测 | `scripts/sort_range_cdp.mjs` **42/42**（点表头 → 切指标 → 翻页 → 切维度，逐步量 DOM） |
| 截图证据 | `outputs/_cdp_shots/sort-01-default-day.png` / `sort-02-user-sort-kept.png` / `sort-03-out-of-range.png` |

### 五条规则（前端 `web/app.js`，状态只有一处：`panel.sort_source`）
```
默认排序   按日/按周 → dimension_value ASC（时间序列语义，★不许改成指标降序）
           按国家   → 当前指标 DESC；客户表/产品表 → sales_amount DESC
Rule 2 切指标   sort_source=default → 排序交回后端默认；=user → **原样保留**（不许覆盖）
Rule 3 切维度   → 清掉用户排序，回 default，用新维度默认排序
Rule 4 点表头   → sort=该列、order 翻转、sort_source=user
Rule 5 翻页     → dimension / metric / sort / order 完整保留（翻页不碰排序）
```
「当前排序：销售额 ↓」一行文字 + 表头 ▲▼ + 未排序列无标记 = 三层提示（只靠表头高亮用户看不见）。

### 时间范围
```
页面文案   删掉「更新于 2026-09-22」（数据源登记时间，误导）→「数据覆盖：2010-12-01 ~ 2011-12-09」
日期框     钉 min/max = data_range；敲进去就判，越界即时提示 + 「使用数据范围」一键填满
0 条        后端仍如实返回 0 条、notes 原话照旧（不截断、不修正、不假装）；前端另给快捷入口
后端回显    query.start/end = **用户填的那个区间**；query.window_start/end = 收紧后的统计窗口
```
口径与算法一个字没改（`resolve_window` 收紧统计窗口的老行为保留，桶完整性/说明都挂它上面）。
