# ROADMAP.md · sales-report-agent

> 2026-09-21 按 ChatGPT 评审（reviews/001）+ 用户要求修订：**MVP 缩到 3 条主链路**，**前端垂直切片优先**。

## 定位（评审后）
> **面向固定业务报表的可确认、可复现、可审计 AI 报表自动化系统**
（不是"AI 自动做销售报表"——那已被 Ilka / ConnectReport 等覆盖）

## 三条主链路（MVP 只做这些）
```
① 数据接入 + 数据字典
② 自然语言建任务 → Report Spec → 用户确认/固化（版本冻结）
③ 确定性执行 + Excel 模板渲染 + 执行记录
```

## 阶段与任务

| 阶段 | 内容 | 验收标准 | 状态 |
|---|---|---|---|
| A | **Spec 核心**：Report Spec 模型 + Metric Definition 层（口径 YAML） | 模型可序列化/版本化；口径定义可加载 | ⏳ |
| B | **数据与执行**：loader（读 Excel/MySQL）+ schema（数据字典）+ executor（查询/计算/校验）+ renderer（openpyxl 原地渲染） | 能算出"上周销售额"，数字与 pandas 直算一致；单测通过 | ⏳ |
| C | **AI Parser**：自然语言 → Report Spec（受限 DSL/JSON，不让 LLM 算数） | 50 条测试样本解析，字段映射/歧义识别/拒答可测 | ⏳ |
| D | **接口层**：FastAPI（upload / parse / confirm / execute / tasks） | 每个端点有真实响应，可 curl 通 | ⏳ |
| E | ★ **前端（可交互，非 demo）**：上传数据 / 一句话建任务 / 看+确认 Spec / 生成报表 / 下载 Excel / 任务列表与执行记录 | 页面真实调用后端 API，全链路能跑通，无假数据 | ⏳ |
| F | **Evaluation Harness**：`run_eval.py` + 50-100 条固定样例（8 维度） | 产出准确率报表，可逐条复核 | ⏳ |
| G | **定时 + 审计**：调度（重启恢复/重复执行/任务锁）+ 执行记录绑定 6 项版本 | 定时能出报表；执行记录可追溯 | ⏳ |

## 明确延后（评审建议砍掉）
```
复杂失败处理体系 · 多数据源扩展 · 完整数据映射 UI · 高级调度能力 · AI 解读的复杂能力
```
