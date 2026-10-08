# 施工指令 · TASK-006（客户分析 + 产品分析）

> 依据：GPT Gate 评审（`docs/_gpt_review_task6.txt`，7774 字符，结论 **APPROVED_WITH_CONDITIONS**）
> 前置：TASK-002/003/004/005/010 已验收；全量 pytest 基线 **276 passed**

## 一、最终范围（评审冻结版，不许扩大）

```
TASK-006
├── customer_analysis          ← 新 Intent 1
│   ├── top                    客户 TOP N（按 sales_amount / order_count / purchase_count）
│   ├── purchase_frequency     购买频次分布 / 频次 TOP
│   ├── repeat_rate            复购率
│   ├── new_customers          数据集内新客
│   └── inactive_customers     规则型沉睡客户
└── product_analysis           ← 新 Intent 2
    ├── top                    产品 TOP（必须与既有 top_products 同参数结果一致）
    ├── trend                  产品趋势（按日/周）
    └── return                 退货/取消分析
```

**明确暂缓 / 不做**（写进代码注释与 capabilities，避免后续误加）：
- ❌ RFM 分群（能确定性算，但口径复杂度高，收益不足）
- ❌ 产品关联（support/confidence/lift —— 组合爆炸，偏离主线）
- ❌ 流失预测 / 任何 ML
- ❌ VIP / 客户等级 / 大客户 / 客户行业 / 客户地区 / 客户渠道 / 客户生命周期 → **unsupported**

**Intent 数量：5 → 7**（只加 2 个，用 `operation` 参数收口，不许一个 operation 一个 Intent）

## 二、口径铁律（评审点名的"一等公民"，必须严格实现）

### 1. CustomerID 空值（TASK-006 的核心）
- **客户维度指标只覆盖 CustomerID 非空的记录**
- **销售额仍按现有口径**：CustomerID 为空的记录**仍计入销售额**，但不计入客户数及客户维度分析
- **每个客户分析结果必须返回 `customer_scope`**，字段至少：
  ```
  customer_id_null_rows        (19113)
  customer_id_nonnull_rows
  customer_scope_sales_amount
  total_sales_amount
  customer_scope_sales_share   ← 这个比例必须代码算，不许 LLM 算
  ```
- 回答里要能说出「本次客户分析覆盖有 CustomerID 的成交，占全部销售额 XX%」

### 2. 购买次数定义（锁死）
- **购买次数 = CustomerID + distinct InvoiceNo**（**不是 DataFrame 行数**）
  （否则一个订单含 20 个商品行会被误认为买了 20 次）
- **复购客户** = `purchase_count >= 2`
- **复购率** = `repeat_customers / customers_with_purchase`
  目标客户写死：**目标区间内至少有一次有效购买的、CustomerID 非空客户**

### 3. 新客定义（锁死，避免业务含义扩大）
- 叫 **「数据集内新客」**，不是"人生第一次购买"
- 定义：CustomerID 非空 **且** 该客户在**整个数据集**中的首次有效购买日期**落在目标区间**
- 老客：首次购买日期 < 目标区间开始（同样叫「数据集内老客」）

### 4. 沉睡客户（规则型，不是预测）
- 叫 **「沉睡客户 / 长期未购买客户」**，**不要叫"流失客户"**
- 定义：`reference_date - last_purchase_date >= inactivity_days`（如 90）
- **不得出现** `churn_probability` / `churn_prediction` 之类字段

### 5. 退货分析：两个口径必须拆开
- **不能合并成一个"退货率"**，至少分别输出：
  - `negative_quantity_rate`（Quantity < 0）
  - `cancel_invoice_rate`（InvoiceNo 以 C 开头）
- 两者**可能重叠**：若要给统一指标，必须**去重**后定义 `return_or_cancel_order_rate` 并单独测试重叠样本

### 6. 不支持的客户维度（必须拒绝，不许顶替）
`VIP客户 / 客户等级 / 大客户 / 企业客户 / 个人客户 / 会员等级 / 客户行业 / 客户地区 / 客户渠道 / 客户生命周期阶段`
→ 全部 `unsupported`
**尤其**：「VIP 客户 TOP10」**不许**悄悄降级成「销售额 TOP10」代替

### 7. 产品 TOP 的兼容性
`product_analysis(operation=top)` 对同一参数**必须与既有 `top_products` 结果一致**（回归保护）

### 8. LLM 边界（延续既有三层闸门）
- LLM 输入**只能含 structured facts**，不得含 DataFrame / raw rows / SQL 结果 / 完整客户明细 / 完整产品明细
- **TOP 顺序必须由代码给出**；LLM 即使说"B 是最高的"也不能成为事实来源
- 数字闸门 / 币种闸门 / 事实段由代码生成 —— 全部继续生效

## 三、AC（Hermes 会逐条独立复验）

| # | 验收标准 |
|---|---|
| AC-01 | 客户 TOP N：`operation=top, metric=sales_amount, top_n=10`；CustomerID 非空；distinct CustomerID；排序由代码；TOP10 与独立参考计算完全一致；并列排序规则固定 |
| AC-02 | 客户购买次数 TOP10：按 **CustomerID + distinct InvoiceNo** 算，**不是行数**；Hermes 用独立参考程序复算逐项比对 |
| AC-03 | 复购率：`repeat_customers = purchase_count >= 2`；`repeat_rate = repeat_customers / customers_with_purchase`；HTTP 返回 total_customers / repeat_customers / repeat_rate，**三者可互相计算验证** |
| AC-04 | 数据集内新客：`2011-11 的新客数量`；CustomerID 非空 + 全数据集首次购买落在目标区间；回答中明确写「数据集内新客」 |
| AC-05 | `customer_scope`：每个客户分析结果都含 customer_id_null_rows / nonnull_rows / customer_scope_sales_amount / total_sales_amount / customer_scope_sales_share；金额与比例**全部代码算** |
| AC-06 | 两套口径同时成立：总销售额**含** CustomerID 为空的行；客户分析**不含**空值 |
| AC-07 | 沉睡客户：固定 `inactive_days=N`，`reference_date - last_purchase_date >= N`；**不得出现 churn_probability / churn_prediction** |
| AC-08 | 产品 TOP：既有 `top_products` 回归保持；`product_analysis(operation=top)` 同参数结果一致 |
| AC-09 | 产品趋势：`product_codes=[A,B], granularity=day/week`；每个产品分别聚合；时间桶完整；**partial bucket 标记保持现有规则**；独立参考值逐桶比对 |
| AC-10 | 退货分析：分别验证 Quantity<0 与 InvoiceNo 以 C 开头，**两个指标不能混为一谈**；若给统一指标必须去重并加重叠样本测试 |
| AC-11 | 不支持的客户维度（VIP客户/客户等级/大客户/客户行业/客户地区/客户渠道/客户生命周期）必须拒绝；尤其**销售额TOP10 不能自动解释成 VIP TOP10** |
| AC-12 | 区域边界回归：TASK-005 已有的「华南区/华东区/上海/门店/销售员」**继续 unsupported**，不得因加入 CustomerID 而改变 |
| AC-13 | LLM 不接触原始客户/产品数据（输入只含 structured facts） |
| AC-14 | LLM 不负责 TOP/排序（最终顺序必须来自代码） |
| AC-15 | 数字闸门回归：人为让 LLM 输出错误数字必须被拦 |
| AC-16 | 真 HTTP + CDP：Hermes 最终能在真服务 + 真数据 + 真 HTTP + CDP 页面上独立验证：客户TOP / 客户复购 / 数据集内新客 / 沉睡客户 / 产品TOP / 产品趋势 / 产品退货 / unsupported VIP / unsupported 客户地区 —— **不能依赖 mock** |
| AC-17 | 全量 pytest 通过（基线 **276 passed**），新测试覆盖新 Intent，且 TASK-002~005、TASK-010 不回归 |

## 四、硬约束（不变）
- 不动冻结资产（metrics/executor/renderer/loader；renderer 除用户可见文案外不许改）
- 前端原生 HTML/CSS/JS + 既有 TDesign token，**复用既有组件**，不加新技术细节展示
- 禁 mock / 假数据 / 硬编码业务数字
- 不重复造轮子：**复用**现有 5 个 Intent 的计算能力与分区渲染
- 不顺手重构（发现重构点只记录）
- 需用户决策的事写进报告，继续做别的，不要停下等

## 五、效率与上下文（重要）
- 本机已配好 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000`，上下文窗口按 1M 计
- 大文件用 Grep + 定向 Read，**不要整篇读**
- 长输出写文件，只回贴关键几行
- **不要用 sleep 轮询等长任务** —— 用后台任务 + 一次性阻塞等待
- 长任务必须后台跑（本机不操作会自动休眠）

## 六、速度
先跑通再打磨；只跑相关测试文件；最后跑一次全量。
完成后按报告格式输出：✅ 做了什么 / 📊 关键结果数字 / ⚠️ 遗留与需决策 / AC 逐条证据。
