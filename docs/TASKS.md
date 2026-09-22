# TASKS.md · sales-report-agent

> 状态：⏳待办 / 🔄进行中 / ✅完成 / ❌失败 / 🚫阻塞
> 顺序原则（2026-09-21 修订）：先打牢 **Spec + 确定性执行**，再串 **API + 前端**（前端要真连后端，不做假数据 demo）。

## 已完成
| ID | 任务 | 状态 | 备注 |
|---|---|---|---|
| TASK-001 | 写 50 条测试集 `tests/test_cases.json` | ✅ 产物已生成 | 分布 16/16/18 · dev40/holdout10；⚠️ 16 条 normal 用例日期越界待修 |
| TASK-001-FIX | 修日期越界（挪到完整数据周） | ⏳ 待办 | 指令已写：`docs/施工指令-TASK-001-FIX.md` |
| TASK-002A | 销售额口径（D16）+ 独立 Oracle 验证 | ✅ 完成（待门禁） | 产物：`app/engine/loader.py` `metrics.py` `executor.py` + `tests/test_executor.py`；pytest 7 passed；2011-11-21~27 销售额 **316,412.16**，与独立 Oracle 差值为 0 |

## 进行中 / 待办
| ID | 任务 | 交付物 | 验收标准 |
|---|---|---|---|
| **TASK-002** | **Spec 核心 + 数据与执行最小闭环** | `app/spec/`（Report Spec 模型）+ `specs/metrics.yaml`（口径）+ `app/engine/loader.py` `schema.py` `executor.py` + 单测 | 能算出"上周销售额"，数字与 pandas 直算**完全一致**；pytest 通过 |
| **TASK-003** | **FastAPI 接口层** | `app/api.py`：`/api/upload` `/api/schema` `/api/parse` `/api/spec/{id}` `/api/execute` `/api/tasks` | 每个端点 curl 有真实响应；上传真实 xlsx 能进库 |
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
