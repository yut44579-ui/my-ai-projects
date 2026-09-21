# 施工指令 · TASK-002（Spec 核心 + 数据与执行最小闭环）

## TASK ID：TASK-002
## 任务名称：Report Spec 模型 + 数据加载 + 确定性执行（能算出"上周销售额"）

## 背景（先读这两份，别跳过）
- `D:\sales-report-agent\CLAUDE.md`（铁律：数字一律代码算 / Excel 用 openpyxl 原地改 / 找不到字段必须提问）
- `D:\sales-report-agent\docs\ROADMAP.md` 和 `docs\TASKS.md`（评审后方案：MVP 只做 3 条主链路）
- GPT 评审地址：`D:\GPT_Project_Reviews\reviews\001-project-direction-and-tech-stack.md`（P0：Report Spec 为核心；最大风险=口径错误）

## 大白话目标
不做前端、不做接口、不接 AI。**只做后端最核心那一步**：给一份规则（报表规格），能自动从 Excel 里把数算出来，
而且算出来的数字必须和你手算/pandas 直算**一模一样**。

## 要建的文件（一次做一个，写完自测再下一个）
1. `app/__init__.py`（空即可）
2. `app/spec/__init__.py`
3. `app/spec/models.py` —— Report Spec 模型（pydantic v2）：
   - 字段：`spec_id, name, version, data_source(file/sheet), time_range(start,end,time_field), metrics[](name,dsl), group_by, filters, output(template,path), created_at`
   - metrics 里的 `dsl` 用**受限结构**（如 `{"op":"sum","field":"revenue"}` / `{"op":"yoy","metric":"sales_amount"}`），**不要让 LLM 生成任意代码**
   - 支持 `to_dict()/from_dict()`、`version` 递增
4. `specs/metrics.yaml` —— 指标口径定义（**先定口径再算数**，这是评审说的最大风险点）：
   ```yaml
   sales_amount:
     definition: quantity * unit_price
     time_field: InvoiceDate
     exclude:
       - cancelled_order      # InvoiceNo 以 C 开头
       - negative_quantity    # 退货
     notes: "不含取消单与退货；如需含税/按支付日口径，改这里"
   ```
5. `app/engine/__init__.py`
6. `app/engine/loader.py` —— 读 `data/Online Retail.xlsx`（用 pandas 读，openpyxl 引擎），做基础清洗（按口径排除取消单/退货），返回 DataFrame
7. `app/engine/schema.py` —— 数据字典：列名 / 类型 / 非空率 / 前 3 个样例值 / 语义候选（给后面 AI 映射用）
8. `app/engine/executor.py` —— 按 Report Spec 执行：查询 → 计算 → **校验**（行数/时间范围/空值/合计是否对得上），返回 `ExecutionResult`（含：数字、校验项、耗时、数据行数）
9. `app/engine/renderer.py` —— 用 **openpyxl 原地改**一个简单模板输出 Excel（模板缺失时用代码生成一个基础模板，**不要用 pandas.to_excel 重建**）
10. `tests/test_executor.py` —— 单测：
    - 算 `2011-11-21 ~ 2011-11-27` 的 `sales_amount`
    - **同时**用 pandas 直接算一遍作对照，断言两者**相等**
    - 断言排除规则生效（含取消单/退货前后的差异）

## 禁止事项
- ❌ 不写前端（那是 TASK-004）
- ❌ 不写 FastAPI 接口（那是 TASK-003）
- ❌ 不接任何 LLM/API 调用（那是 TASK-005）
- ❌ 不引入新的重依赖（pandas/openpyxl/pydantic/pyyaml 已有；**不要装 LangChain/向量库/Flask**）
- ❌ 不做定时调度
- ❌ 不重构既有文件，不要动 `docs/`、`CLAUDE.md`

## 验收标准（做完必须贴实际命令输出）
1. `.venv\Scripts\python.exe -m pytest tests/ -q` 全绿
2. 手工跑一段：打印 "2011-11-21~2011-11-27 销售额"，并同时打印 pandas 直算的金额，**两个数字必须相同**
3. 改 `specs/metrics.yaml`（例如把 exclude 去掉退货），再跑一次，数字**应该变化**——证明口径生效
4. 输出一个 Excel 文件到 `outputs/`，打开能看到数字（贴文件路径 + 大小）

## 汇报格式
```
【任务】TASK-002
【新增文件】逐个列出
【验证结果】pytest 输出 / 两个数字对比 / 口径改动前后差异 / Excel 路径
【遇到的问题】没有就写"无"
【遗留】没有就写"无"
```
