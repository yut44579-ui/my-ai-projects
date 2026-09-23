# 施工指令 · TASK-005（销售分析扩展）

> 依据：GPT Gate 评审（`docs/_gpt_review_task5.txt`，结论 APPROVED_WITH_CONDITIONS）
> 前置：TASK-004 已验收（3 个 Intent：sales_summary / sales_trend / top_products），181 tests 全绿

## 一、最终范围（评审已定，不许自行扩大）

### A. `sales_compare`（在两区间比较上扩展）
```
A 区间 vs B 区间
├── sales_amount / order_count / customer_count / avg_order_value
├── change_amount / change_rate
└── attribution_dimension  ← 受控参数，不是新 Intent
      ├── null（普通比较）
      ├── country
      └── stock_code
```
比较类型 `comparison_type`：`custom` | `wow`（周环比）| `mom`（月环比）| `yoy`（同比）

### B. `sales_breakdown_by_country`（只做分布，**不承担变化归因**）
```
某时间段各 Country 销售额分布：TOP N + amount + share（占比）+ total 一致性
```
**注意**：`sales_breakdown_by_country` 回答「某时段谁卖得多」；
「两时段之间谁造成了变化」归 `sales_compare` 的 attribution。两者语义不要混。

### C. 明确拒绝（继续拒绝，不许用 Country 顶替）
`region / province / city / store / channel / salesperson` → `unsupported`

### D. LLM 边界（第五层加固，比 TASK-004 更严）
LLM **不计算、不排序、不判断主要贡献者、不产生事实数字**，只负责受限事实的语言表达。

## 二、归因的架构要求（评审点名，最重要）

数据流必须是：
```
Step 1  代码计算：每个维度的 current / previous / delta = current - previous
Step 2  代码排序：按 |delta| 排序，取 positive_contributors_top_n / negative_contributors_top_n
        （建议各取 3，作为常量，不要散落）
Step 3  facts 输出结构化结果：
        { "total_delta": ..., "positive_contributors": [{dimension,name,current,previous,delta}...],
          "negative_contributors": [...] }
Step 4  【主要贡献】这一段由**代码生成文本**（不是 LLM）
Step 5  LLM 只拿 facts，做语言表达
```
回答分区建议变成：
```
【发生了什么】  代码生成（含事实数字）
【主要贡献】    代码生成（正/负贡献者名单 + delta + contribution_share_of_change）
【为什么】      LLM 生成，标注「推断」
【建议行动】    LLM 生成
```

## 三、硬校验（评审要求，做成 AC）

### 1. 归因一致性校验（必须）
```
Σ(各维度 delta) == total_delta
```
**不相等 → 整个归因结果不得进入回答**（这是内部一致性校验，比测几个数字更重要）。

### 2. 贡献率命名与取值
- 字段名用 `delta_amount` 与 `contribution_share_of_change`，**不要只叫 percentage**
- 允许 `>100%` 和 `<0%`（数学上正常）：
  - 例：Total Δ=+1000 / Germany Δ=+1500 / Spain Δ=-500 → Germany 150%、Spain -50%
- 前端展示时**不要**让用户误读成"占全部销售额的比例"

### 3. 除零处理
`previous == 0` 时 **不得产生 Infinity / NaN** → 必须输出 `not_available` / `undefined` 明确状态。

### 4. 同比的可用性判断（不删、不硬做）
`yoy` 必须**先判断是否存在完整等长可比窗口**：
- 例：「2011 年 12 月 vs 2010 年 12 月」→ 2011-12 只覆盖到 **12-09** → 整月同比 → `comparison_status = insufficient_data`
  返回类似：「无法进行完整月度同比：2011 年 12 月数据仅覆盖至 12 月 9 日。」
- 若用户要的是**等长窗口**（如 2011-12-01~12-09 vs 2010-12-01~12-09）→ 可以算，并注明是等长窗口
- **不猜、不补、不偷偷截断**

### 5. 区域边界（写进 AC）
「不会用 Country 代替区域」必须保留，并新增测试钉死。

## 四、AC（我会逐条独立复验）

| # | 验收标准 |
|---|---|
| AC-01 | 普通两区间比较：「比较 2011-11 和 2011-10 的销售额」→ 识别 `sales_compare`、两个明确区间、executor 确定性计算、返回 current/previous/delta/rate、HTTP 200、页面可展示 |
| AC-02 | 自定义区间：「比较 2011-11-01~11-15 与 2011-10-01~10-15 的销售额」→ 两个区间进入结构化参数、**不允许 LLM 自行解释日期**、结果可被独立复算 |
| AC-03 | 环比：本周 vs 上周、本月 vs 上月；`delta = current - previous`、`rate = delta / previous`；`previous == 0` 时输出 `not_available`（不许 Infinity/NaN） |
| AC-04 | 同比三种情况：① 有完整等长窗口 → 正常计算 ② 窗口不完整 → `insufficient_data` + 明确原因 ③ 用户要求不支持的窗口 → 如实拒绝 |
| AC-05 | 国家分布：TOP N + share；**Σ(各国家额) == 区间总额**（一致性） |
| AC-06 | 归因（country）：正/负贡献者由代码排序选出；`Σ(delta) == total_delta`；不一致则整段不进回答 |
| AC-07 | 归因（stock_code）：同 AC-06 |
| AC-08 | **LLM 不参与**：贡献者名单、delta、share 全部来自代码；LLM 段落里无任何「不在确定性结果集合里」的数字（数字闸门继续生效） |
| AC-09 | 「华南区/华东/上海门店/线上渠道/张三销售」→ 全部 `unsupported`，且**不许映射到 Country** |
| AC-10 | 数字一致性：回答里的金额与直接调 `executor` 的结果**位级相等**（沿用 TASK-004 的取证方式） |
| AC-11 | 前端可操作（Hermes 用 CDP 实测）：新能力的回答能正常分区渲染，含【主要贡献】段 |
| AC-12 | Legacy Contract：既有 12 端点语义 + metrics/executor/renderer/loader + TASK-004 的 3 个 Intent 行为**不回归** |
| AC-13 | 全量 pytest 全绿（当前基准 **181 passed**）+ 新的 e2e 脚本真 HTTP 真 DeepSeek 跑通 |

## 五、硬约束（不变）
- 不动冻结资产（metrics/executor/renderer/loader）
- 前端原生 HTML/CSS/JS，复用既有组件与 token
- 不顺手重构（发现重构点只记录）
- 禁 mock / 假数据 / 硬编码业务数字
- 需要用户决策的事写进报告「需决策」段，继续做别的，不要停下等

## 六、会话健康（上次 6MB 崩过）
- 大文件用 Grep + 定向 Read(offset/limit)，不要整篇读
- 长输出写文件，不要贴在对话里

## 七、速度
先跑通再打磨；只跑相关测试文件。完成后按报告格式输出
（Status / 做了什么 / 结果数字 / AC 逐条证据 / 遗留与需决策）。
