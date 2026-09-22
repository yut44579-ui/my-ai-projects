你现在执行项目 TASK-002A（v3，已按 R004 评审意见收口）。

【1. 角色】
你是代码执行器。只实现当前 TASK，不扩大范围。产出叫"完成"（COMPLETED），PASS 由 Hermes 门禁判定。

【2. 项目根目录】
D:\sales-report-agent（已 git init，最新 commit d186203）

【3. 必读】
- D:\sales-report-agent\CLAUDE.md（铁律）
- 本指令第 11 节的口径清单（= docs/DECISIONS.md 的 D16 原文，已内嵌，**以本指令为准**）

【4. 目标】
给一份 Excel，按第 11 节固定口径算指定时间段的销售额，并用**完全独立的第二条代码路径**验证数字正确。

【5. Scope】
新增：
- app/__init__.py
- app/engine/__init__.py
- app/engine/loader.py    —— 读 data/Online Retail.xlsx（pandas + openpyxl 引擎）
- app/engine/metrics.py   —— 实现第 11 节口径；**每条规则必须有对应常量/函数与注释**
- app/engine/executor.py  —— 计算接口（签名固定）：
      def compute_sales_amount(start, end, exclude_cancelled=True, exclude_negative_qty=True,
                               exclude_nonpositive_price=True) -> dict
        参数：start/end 接受 str("YYYY-MM-DD" 或 ISO 时间) / datetime.date / datetime.datetime；
              语义为**含首尾全天**（= start 00:00:00 ~ end 23:59:59.999）。
              ⚠️【评审建议 3】实现**允许且推荐**用等价半开区间（pandas datetime64[ns] 精度高于毫秒）：
                 InvoiceDate >= start_date 00:00:00  AND  InvoiceDate < (end_date + 1 day) 00:00:00
              两种写法业务语义必须等价；不得自行改成别的口径
      返回：{"amount":float, "rows_in_range":int, "rows_excluded":int, "excluded_amount":float,
             "excluded_detail":{"cancelled":int, "negative_qty":int, "nonpositive_price":int,
                                 "multi_rule_hit":int},
             "validations":dict, "seconds":float}
      **必须支持关闭排除规则**（三个 exclude_* 参数），供反向校验使用
- tests/test_executor.py
修改：
- docs/TASKS.md（只改 TASK-002A 状态）

【6. Out of Scope（严禁）】
- 严禁 Excel 渲染/输出（002B）｜严禁 FastAPI 接口（002C）｜严禁接 LLM（005）｜严禁前端（004A）
- 严禁引入新依赖｜**严禁修改 docs/DECISIONS.md**｜严禁顺手重构｜严禁改 data/ 原始文件

【7. Acceptance Criteria（逐条贴实际输出）】
AC-01 `\.venv\Scripts\python.exe -m pytest tests/ -q` → 0 failed
AC-02 【独立 Oracle 走不同代码路径 —— 评审补强：必须真正独立】
      测试内**自己**用 pandas 读 xlsx、**自己**写筛选与求和。
      Oracle 必须在测试文件内**独立定义**：Excel 路径、InvoiceDate 时间筛选、三条排除条件、
      Quantity*UnitPrice 公式、求和逻辑；
      **不得从 app.engine.* 导入任何常量、规则、字段定义或中间结果**（不只是"不复用函数"）；
      仅允许使用 pandas / openpyxl / 标准库等已有依赖；
      算 2011-11-21 ~ 2011-11-27 销售额，与 executor 断言相等；打印 executor 值、Oracle 值、差值（须为 0）
AC-03 【反向校验 —— 必须带前置断言】
      ① 先断言"该周被排除行数 > 0"（若为 0 则本 AC 判 FAIL 并说明，不得静默通过）
      ② 三个 exclude_* 全关后两边各算一次，断言：
         **金额发生变化**（与开启排除时的值不相等）**且 executor 与 Oracle 仍然相等**。
         ⚠️ 不要断言"变大"——被排除行（取消单/退货/负单价）的 Quantity*UnitPrice 之和通常为**负**，
            关闭排除规则后金额通常是**变小**。实现里请打印排除行净额并说明符号。
AC-04 【时间区间语义（含首尾全天）——评审建议 5 已改措辞】
      断言：**时间筛选阶段**完整纳入区间内**所有原始行**，即
            rows_in_range == 该时间区间内原始行数（用 Oracle 独立算同一个数并断言相等）；
      然后再独立应用排除规则（两步分离，不要混在一起说）。
      打印区间行数，并单独打印 2011-11-27 当天行数（须 > 0，证明含末日全天）
AC-05 【统计口径定义 —— 必须写进代码注释】
      ⚠️【评审 BLOCKER-2 已定死】所有统计**只以"当前启用的 exclude_* 规则"为准**：
      · rows_excluded     = 至少命中一条**已启用**排除规则的去重行数
      · excluded_detail   = 只统计**已启用**规则；被关闭规则对应的计数键**固定为 0**
      · multi_rule_hit    = 同时命中 ≥2 条**已启用**排除规则的去重行数
      · excluded_amount   = 最终因**已启用**规则被排除的行的 Quantity*UnitPrice 之和
      （例：exclude_cancelled=False 且某行同时是取消单+负数量 → cancelled 计 0、negative_qty 计 1、
        multi_rule_hit 计 0（已启用规则里只命中 1 条）、rows_excluded 计 1、该行计入 excluded_amount）
      打印：区间行数 / 被排除行数 / 各行明细 / 被排除金额 / 有效行 Quantity 合计
      executor 返回的 validations 必须是 dict，至少含这些键（键名固定，测试会断言）：
        customer_id_nulls : int     # CustomerID 为空的行数（D16：报告但不排除）
        duplicate_rows    : int     # 【评审建议 4】采用 pandas df.duplicated(keep="first").sum() 语义：
                                    #   每个完全重复组保留首次出现，其余重复出现的行计数；本 TASK 不删除这些行
        excluded_net_amount : float # 被排除行 Quantity*UnitPrice 净额（通常为负，请打印符号）
        checks            : list    # 其余自检项说明
AC-06 数据溯源：打印 data/Online Retail.xlsx 的 SHA256（应为
      43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d）与读取后 shape。
      若哈希**不匹配**，视为环境/数据错误 → 报 **FAIL** 并停止计算（不得继续）
AC-07 metrics.py 中第 11 节每条规则都有对应实现与注释，逐条可对照

【8. 必须执行的验证】
1. 静态：`.venv\Scripts\python.exe -m compileall app tests`
2. 单测：`.venv\Scripts\python.exe -m pytest tests/ -q`
3. API 冒烟：N/A ｜ 4. 真实文件 E2E：用真实 xlsx（禁造数据）｜ 5. 前端：N/A
6. Git diff：`git status --short` + `git diff --stat`（只含 Scope 内文件）

【9. 强制规则】
- 不得改测试让测试通过；不得删除/跳过失败测试
- **Oracle 不得复用被测代码任何函数**
- 不得 mock、不得造假数据、不得留 TODO/pass
- 范围外问题只记录（OUT-OF-SCOPE:），不修改
- 无法完成必须报 FAIL

【10. 报告格式（Status 只能是 COMPLETED / FAIL / BLOCKED）】
# TASK-002A Execution Report
## Status / ## Summary / ## Changed Files / ## Acceptance Criteria（AC-01..07 各含 Evidence）
## Gate Results（GATE-1/2/4/6 命令 + 输出）/ ## Out-of-Scope Findings / ## Tests Modified / ## Known Issues

【11. 口径清单（D16 原文，必须逐条实现）】
```markdown
## D16 · 销售额口径清单（R003 评审要求先固化，2026-09-22）

> 评审阻塞项：口径不定死，executor 与测试 Oracle 会共用隐含假设，测试全绿也不代表口径正确。
> **TASK-002A 必须严格实现本清单，不得自行解释。**

| 项 | 定义 |
|---|---|
| 时间字段 | `InvoiceDate`（下单时间） |
| 区间语义 | **含首尾全天**：start 00:00:00 ~ end 23:59:59.999 |
| 取消单 | 排除 `InvoiceNo` 以 `'C'` 开头的行 |
| 退货/负数量 | 排除 `Quantity <= 0` 的行 |
| 负/零单价 | 排除 `UnitPrice <= 0` 的行 |
| 缺失客户 | `CustomerID` 为空 **不排除**（只影响客户维度分析），但在 `validations` 里报告空值行数 |
| 重复行 | **不去重**（原始数据可能含同单据重复录入；去重策略留待 002B 决定，本 TASK 报告重复行数） |
| 金额公式 | `sales_amount = sum(Quantity * UnitPrice)` |

**实现要求**：这份清单必须逐条以注释形式出现在 `app/engine/metrics.py` 中（可枚举、可核对）。
```
