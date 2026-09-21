你现在执行项目 TASK-002A。

【1. 角色】
你是代码执行器。只负责实现当前 TASK，不负责改变需求、不负责扩大范围。
你的"完成"只代表代码写完，不代表 PASS——最终 PASS 由 Hermes 按门禁独立判定。

【2. 项目根目录】
D:\sales-report-agent

【3. 必读文件（先读，不要跳过）】
- D:\GPT_Project_Reviews\WORKFLOW.md（协作规范；你只需遵守第 14-19、26、49 节：范围、门禁、禁止假实现、不得自证）
- D:\GPT_Project_Reviews\decisions\002-adopt-review-2.md（本项目批准方案与执行顺序）
- D:\sales-report-agent\CLAUDE.md（项目铁律）
- D:\sales-report-agent\docs\TASKS.md（任务队列）
- D:\sales-report-agent\docs\DECISIONS.md（技术决策，尤其 D11-D14）
（注意：本项目暂无 README.md，不要假装它存在）

【4. 当前 TASK】
TASK-002A —— 数据加载 + 固定指标计算（**不做 Excel 渲染、不做 API、不接 LLM**）

大白话目标：给一份 Excel，能按固定口径把"某个时间段的销售额"算出来，
并且算出来的数字必须和"另写一段 pandas 直接算"的结果**完全相等**（这是独立 Oracle 的要求）。

【5. Scope（允许动的）】
允许新增：
- app/__init__.py
- app/engine/__init__.py
- app/engine/loader.py      —— 读 data/Online Retail.xlsx；基础清洗；返回 DataFrame
- app/engine/metrics.py     —— 固定指标定义 sales_amount（口径写死在这里，含注释说明每个排除项的含义）
- app/engine/executor.py    —— 按时间范围计算 sales_amount，返回结果（含：金额、行数、时间范围、校验项、耗时）
- tests/test_executor.py    —— pytest，含独立 Oracle 对照
允许修改：
- docs/TASKS.md（只把 TASK-002A 状态改为完成，并贴你的执行报告要点）

【6. Out of Scope（严禁动的）】
- 严禁做 Excel 渲染/输出（那是 TASK-002B）
- 严禁写 FastAPI / 任何接口（那是 TASK-002C）
- 严禁接任何 LLM/AI（那是 TASK-005）
- 严禁写前端（那是 TASK-004A）
- 严禁引入新依赖（pandas / openpyxl / pytest 已装；禁止 LangChain / 向量库 / Flask / FastAPI 相关）
- 严禁重构既有文件、严禁动 CLAUDE.md / docs/DECISIONS.md / docs/ROADMAP.md
- 严禁顺手修改 data/ 下的原始数据文件

【7. Acceptance Criteria（必须逐条贴实际输出）】
AC-01  `.venv\Scripts\python.exe -m pytest tests/ -q` 全部通过（0 failed）
AC-02  计算 2011-11-21 ~ 2011-11-27 的 sales_amount，同时用一段**独立写的 pandas 直算**（写在测试里，不许复用 executor 的函数）算同一个值，
       断言两者相等；把两个数字都打印出来
AC-03  验证排除规则生效：分别打印"含取消单与退货"和"不含"的金额，两者必须不同；
       并说明排除规则是：InvoiceNo 以 'C' 开头（取消单）、Quantity < 0（退货）
AC-04  验证时间过滤：打印该周筛选后的行数，并断言所有行的 InvoiceDate 都落在 [2011-11-21, 2011-11-27] 内
AC-05  `app/engine/metrics.py` 里的口径必须写明注释（时间字段用哪个、排除什么、为什么）

【8. 必须执行的验证（顺序不可变）】
1. 静态检查：`.venv\Scripts\python.exe -m compileall app tests`
2. 单元测试：`.venv\Scripts\python.exe -m pytest tests/ -q`
3. API 冒烟：N/A（本 TASK 无接口）
4. 真实文件 E2E：用真实 data/Online Retail.xlsx（不是造的假数据），跑出一周销售额
5. 前端：N/A
6. Git diff 检查：`git status --short`（本 TASK 尚未 git init，如无仓库就说明）

【9. 强制规则（违反即 FAIL）】
- 不得修改测试来让测试通过；不得删除失败测试
- 不得 mock 真实业务流程冒充通过；不得造假的 Excel 数据
- 不得留下 TODO / pass 占位
- 发现范围外问题只记录，不修改（用 OUT-OF-SCOPE: 开头报告）
- 无法完成必须报 FAIL，不得报 PASS

【10. 完成后的报告格式（固定）】
# TASK-002A Execution Report
## Status
PASS / FAIL / BLOCKED
## Summary
<一句话>
## Changed Files
### Added / ### Modified
## Acceptance Criteria
### AC-01 .. AC-05（每条：Status + Evidence 实际输出）
## Gate Results
GATE-1 Static: 命令 + 输出
GATE-2 Unit: 命令 + 输出
GATE-4 Real File E2E: 证据
GATE-6 Git Diff: git status --short 结果
## Out-of-Scope Findings
NONE 或 列出
## Tests Modified
YES / NO
## Known Issues
NONE 或 列出
