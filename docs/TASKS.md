# TASKS.md · sales-report-agent

> 状态：⏳待办 / 🔄进行中 / ✅完成 / ❌失败 / 🚫阻塞
> 顺序原则（2026-09-21 修订）：先打牢 **Spec + 确定性执行**，再串 **API + 前端**（前端要真连后端，不做假数据 demo）。

## 已完成
| ID | 任务 | 状态 | 备注 |
|---|---|---|---|
| TASK-001 | 写 50 条测试集 `tests/test_cases.json` | ✅ 完成 | 分布 16/16/18 · dev40/holdout10 |
| TASK-001-FIX | 修日期越界（挪到完整数据周）+ git | ✅ 完成 | 相对时间基准日 2011-12-12 → **2011-12-01**，故"上周"= **2011-11-21~11-27**（周一~周日，7 天完整）；上上周=11-14~11-20；今年=2011-01-01~11-30。越界数 **0**；分布仍 16/16/18 · dev40/holdout10。git 仓库已存在（`d186203` 初始化即含测试集，`.gitignore` 内容已符合要求），本次仅提交 `tests/test_cases.json` + 本文件。⚠️ **遗留待裁决**：`c03` / `e08` / `e09` / `e10` 的口径描述与已固化口径 **D16**（排除取消单 / Quantity≤0 / UnitPrice≤0）冲突——需确认这几条是否仍属"必须提问"的歧义，以及是否允许用户要求"含退货/含取消单"（FIX 指令限定只改日期，未擅自改） |
| TASK-002A | 销售额口径（D16）+ 独立 Oracle 验证 | ✅ 完成（待门禁） | 产物：`app/engine/loader.py` `metrics.py` `executor.py` + `tests/test_executor.py`；pytest 7 passed；2011-11-21~27 销售额 **316,412.16**，与独立 Oracle 差值为 0 |
| TASK-002B | Report Spec + Excel 渲染（openpyxl 原地改） | ✅ 完成（待门禁） | 产物：`app/spec/{__init__,models}.py` + `app/engine/renderer.py` + `templates/weekly_sales_template.xlsx`（代码生成）+ `tests/test_renderer.py`；全量 pytest **18 passed**（含 002A 的 7 条）；产出 `outputs/*.xlsx` 读回数字与 executor 一致，模板样式（字体/数字格式/列宽/合并区）逐项保留 |
| TASK-002C | 最小 API 闭环（upload/schema/execute/download/executions/health） | ✅ 完成（待门禁） | 产物：`app/api.py`（6 个端点）+ `app/state.py`（JSON 落盘上传/执行记录）+ `tests/test_api.py`；全量 pytest **36 passed**（002A 7 + 002B 11 + 002C 18）；真上传 `data/Online Retail.xlsx` → 541909 行/8 列、SHA256 与快照一致；`/api/execute` 2011-11-21~27 → **316,412.16**（行 19950）；`/api/download` 6008 字节真 xlsx，openpyxl 读回 B4=316412.16 且数字格式保留；执行记录落盘 `state/executions.json`（execution_id/数据 SHA256/spec/代码版本/校验结果/耗时）。⚠️ **边界**：executor 写死读冻结快照，故 `/api/execute` 只对 SHA256 一致的文件出数，任意上传文件取数需改造 002A（Scope 外，已上报） |

## 进行中 / 待办
| ID | 任务 | 交付物 | 验收标准 |
|---|---|---|---|
| ~~TASK-002~~ → **002A** | 数据加载 + 固定指标计算 | `app/engine/{loader,metrics,executor}.py` + `tests/test_executor.py` | ✅ **完成**（commit 8fc01a2）：独立 Oracle 差值 0.0；区间 19950 行；销售额 £316,412.16；SHA256 校验通过 |
| **TASK-003** | **FastAPI 接口层** | `app/api.py`：`/api/upload` `/api/schema` `/api/parse` `/api/spec/{id}` `/api/execute` `/api/tasks` | 每个端点 curl 有真实响应；上传真实 xlsx 能进库。**状态（2026-09-23）**：🔶 部分完成 —— TASK-002C 已交付 6 个端点 `/api/upload` `/api/schema` `/api/execute` `/api/download/{id}` `/api/executions` `/api/health`；本行另列的 `/api/parse` `/api/spec/{id}` `/api/tasks` **不在 002C 范围**（依赖 LLM 解析与任务模型，属 TASK-005/阶段3），尚未实现 —— 派发 004A 前端前需 Hermes 确认这几个端点是否必需 |
| **TASK-004** | ★ **前端（可交互，非 demo）** | `web/`（`index.html` + `app.js` + `api.js` + `style.css`，**分文件不堆单文件**） | ①上传真实 Excel ②一句话建任务 ③看到 AI 解析的 Spec 并可确认/改 ④点生成 → 下载真 Excel ⑤任务列表+执行记录；**无假数据、无 TODO 占位** |
| TASK-005 | AI Parser（自然语言 → Spec） | `app/parser.py` | 50 条样本解析；歧义时列候选让用户确认，不猜 |
| TASK-006 | Evaluation Harness | `run_eval.py` + 样例集 | 输出 5 项子指标（映射/指标定义/查询/计算/最终Excel） |
| TASK-007 | 定时 + 审计 | `app/scheduler.py` + 执行记录表 | 重启恢复 / 重复执行 / 任务锁；执行记录绑定 spec/数据/模板/代码版本 + execution_id |

## 用户明确要求（不可违反）
```
★ 前端"有交互、能实现功能"——不是 demo index 那种类型（不要假数据/静态展示/占位 TODO）
★ 数字一律代码算，LLM 只写文字
★ Excel 用 openpyxl 原地改，不重建
★ 执行前先出方案给用户确认
★ 找不到字段列候选让用户选，不瞎猜
```
