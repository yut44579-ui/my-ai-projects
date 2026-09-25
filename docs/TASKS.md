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

| TASK-002D | 任务层最小实现（5 个任务端点 + `state/tasks.json` 落盘） | ✅ 完成（待门禁） | 产物：`app/api.py` **新增** 5 个任务端点（`POST /api/tasks`、`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/run`、`GET /api/tasks/{id}/runs`；既有 6 个端点语义/契约**一行未改**）+ `app/state.py`（`state/tasks.json`：`schema_version` + 冻结 Spec 快照，`run_count` 由执行记录现算）+ `app/spec/models.py`（Spec 序列化/还原）+ `tests/test_tasks.py`（26 条）+ `scripts/task_smoke.py` + `scripts/serve.py`；**定向测试 44 passed**（test_api 18 + test_tasks 26）；统一错误体 `{error:{code,message,detail}}` 且**保留顶层 detail**（旧契约只增不改）；`time_range` schema 定形 `{mode,start?,end?,tz?}`（只实现 `absolute`，相对模式报 `relative_time_not_implemented`）；执行记录扩展 `task_id/spec_id/spec_version/resolved_range/base_date`；对外只给**相对** `download_url`（不给服务器绝对路径） |

| TASK-STEP-C | 账号审批（管理员）+ 游客限制 + 登录页文案简化 | `app/accounts.py`（+`delete_account`）、`app/api_auth.py`（+`DELETE /api/auth/accounts/{u}`）、`app/repositories/{base,json_repo,json_store}.py`（+`remove`）、`app/state.py`（+`remove_account`）、`web/{session.js,app.js,api.js,index.html,style.css}`、`tests/{test_auth_admin.py,test_web_guest.py}`（新）、`scripts/{stepc_accounts_e2e.py,stepc_guest_cdp.mjs}`（新） | ① 硬证据一：真 uvicorn **杀掉再拉起**，注册过的账号照旧能登（`scripts/stepc_accounts_e2e.py` 34/34）；② 硬证据二：游客点受限入口，**网络请求次数一次都不涨**（真 Edge + CDP 数 `window.fetch`，55/55）；③ 四种「登不上」给四句不同的话；④ ⚠ 只出现在 9 个静态受限入口 + 动态生成的导出按钮上，别处为 0。详见 **D21** |
## 进行中 / 待办
| ID | 任务 | 交付物 | 验收标准 |
|---|---|---|---|
| ~~TASK-002~~ → **002A** | 数据加载 + 固定指标计算 | `app/engine/{loader,metrics,executor}.py` + `tests/test_executor.py` | ✅ **完成**（commit 8fc01a2）：独立 Oracle 差值 0.0；区间 19950 行；销售额 £316,412.16；SHA256 校验通过 |
| **TASK-003** | **FastAPI 接口层** | `app/api.py`：`/api/upload` `/api/schema` `/api/parse` `/api/spec/{id}` `/api/execute` `/api/tasks` | 每个端点 curl 有真实响应；上传真实 xlsx 能进库。**状态（2026-09-23 更新）**：🔶 大部分完成 —— 002C 交付 6 个端点 `/api/upload` `/api/schema` `/api/execute` `/api/download/{id}` `/api/executions` `/api/health`；**TASK-002D 已补上任务层 5 个端点** `POST /api/tasks` `GET /api/tasks` `GET /api/tasks/{id}` `POST /api/tasks/{id}/run` `GET /api/tasks/{id}/runs`。**仅剩** `/api/parse` `/api/spec/{id}` **未实现** —— 它们依赖 LLM 把大白话解析成 Spec，属 **TASK-005（AI Parser）/阶段3**，不在 002C/002D 范围（铁律4/铁律5：解析器没冻结前不猜、不假造） |
| **TASK-004** | ★ **前端（可交互，非 demo）** | `web/`（`index.html` + `app.js` + `api.js` + `style.css`，**分文件不堆单文件**） | ①上传真实 Excel ②一句话建任务 ③看到 AI 解析的 Spec 并可确认/改 ④点生成 → 下载真 Excel ⑤任务列表+执行记录；**无假数据、无 TODO 占位**。**状态（2026-09-23）**：004A 上次报 🚫**BLOCKED**（缺口无任务实体的接口）→ 用户拍板"先补任务层"→ **TASK-002D 已补上**，故 004A **已解锁可开工**。③"AI 解析的 Spec"仍依赖 **TASK-005**，004A 阶段由**人工构造 Spec**（走 `POST /api/tasks` 的全 Spec 路径）兑现，**不假造 AI**。AC-09 取证方式见 D19-2（httpx 十步真 HTTP 闭环 + 用户手动浏览器走查截图，**不装 Playwright**）。**状态（2026-09-23）**：✅ **TASK-004A 最小垂直切片已完成（待门禁）** —— `web/{index.html,api.js,app.js,style.css}`（原生 JS，五区块各有真实数据源/交互/空状态/按前置禁用；①上传 ②建任务 ③后端返回的真 Spec ④执行+真下载 ⑤执行记录，刷新后从 `GET /api/tasks` 重读）；`app/api.py` **只加静态挂载**（`app.mount("/")`，放在文件末尾保证 `/api/*` 优先级更高，既有逻辑未动）；测试 `tests/test_web.py`（12 条）+ 真服务十步闭环 `scripts/web_e2e.py`。**005 之前 ③ 用人工构造的 Spec**（走 `POST /api/tasks` 便捷路径，后端生成并冻结），**不假造 AI 解析** |
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
