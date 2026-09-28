# ARCHITECTURE.md · sales-report-agent

## 0. 文档状态

| 项 | 值 |
|---|---|
| 文档版本 | v2（2026-09-28 重写，替代 v1 的"设计设想"版） |
| 最后核对日期 | 2026-09-28 |
| 对应 commit | `017fa8e` |
| 事实来源 | 代码本身（逐项 ls / grep 核对）+ `docs/DECISIONS.md`（记录"为什么变成这样"） |

**如本文与代码冲突，以代码和最新 DECISIONS 为准，并立即修正文档。**

本文只回答一个问题：**现在的架构是什么样**。
升级方案、历史设想、"本来打算这么做"一律不写在这里——历史归 `docs/DECISIONS.md`，
将来归第 7 节。**第 2~6 节里出现的每一层、每一个模块，都是磁盘上真实存在、现在就在跑的。**

---

## 1. 系统定位

**企业销售分析 Agent** —— 通过自然语言进行销售、客户、产品、异常等分析，
并提供真实数据表、周报、数据管理及账号能力。

### 1.1 当前能力（已实现，界面上能跑通）

| 能力 | 说明 |
|---|---|
| 自然语言分析 | 一句话提问 → Intent → 白名单工具 → 确定性计算 → 分区回答（销售 / 客户 / 产品 / 异常） |
| 真实数据表 | 客户 / 产品 / 销售 / 原始数据四类业务表，分页、排序、按区间取数、导出 |
| 周报 / 月报 | 按区间生成，可导出 Word / Excel / Markdown 三种真文件 |
| 数据管理 | 数据源上传、登记、检查（`inspect`）、导入（`import`） |
| 账号能力 | 注册 / 登录 / **可选的管理员审批**（通过·拒绝·停用·恢复；默认不审批，见 D22）/ 游客模式 / **密码恢复**（恢复码自助重置 + 管理员一次性临时密码） |
| 确定性报表 | 按 Report Spec 区间算数 → openpyxl 原地改模板 → 下载真实 xlsx |
| 文档输入 | Word / PDF 上传 → 真实文本提取 + 规则化摘要 |

### 1.2 未来计划（**未实现**，全部集中在第 7 节）

定时调度、关系数据库落库、服务端业务级权限控制、评估 Harness。
**这四个能力不在第 2~6 节的任何一张图里出现**——不在分层里、不在数据流里、
不在边界里。想找它们只有一个地方：第 7 节。

---

## 2. 总体分层

```
┌──────────────────────────────────────────────────────────────────┐
│ Web        web/  原生 HTML + JS 单页（无框架）                     │
│            9 个业务页 + 登录层（登录/注册/游客）+ 帮助/隐私两个信息面板│
└───────────────────────────────┬──────────────────────────────────┘
                                │ REST / JSON（共 31 条端点路径）
┌───────────────────────────────▼──────────────────────────────────┐
│ API        app/api.py（主）+ api_chat / api_datasets /            │
│            api_documents / api_auth（APIRouter）                  │
│            薄层：入参校验 → 调下层 → 落记录 → 出响应              │
└───────┬─────────────────┬──────────────────┬─────────────────────┘
        │                 │                  │
┌───────▼────────┐ ┌──────▼─────────┐ ┌──────▼─────────────┐
│ AI   app/ai/   │ │ Dataset        │ │ Account            │
│ 理解需求、组织  │ │ app/datasets/  │ │ app/accounts.py    │
│ 语言；不算数字  │ │ 取数唯一入口    │ │ app/captcha.py     │
└───────┬────────┘ └──────┬─────────┘ └──────┬─────────────┘
        │  只调用既有接口，不在本层重算        │
        └─────────┬───────┴──────────────────┘
┌─────────────────▼────────────────────────────────────────────────┐
│ Repository / State   app/repositories/ + app/state.py            │
│   抽象接口 base.py → 当前唯一实现 json_repo.py → 底座 json_store.py│
│   ★ 持久化 = state/*.json 文件（**当前不是关系数据库**，详见 5.4）  │
└─────────────────┬────────────────────────────────────────────────┘
┌─────────────────▼────────────────────────────────────────────────┐
│ Engine   app/engine/  loader · metrics · executor · renderer ·    │
│          docs   —— ★ 确定性计算内核（冻结资产，见 5.1）             │
└─────────────────┬────────────────────────────────────────────────┘
┌─────────────────▼────────────────────────────────────────────────┐
│ Data   data/Online Retail.xlsx（冻结快照，SHA256 核对）            │
│        data/uploads · data/documents · state/ · outputs/          │
└──────────────────────────────────────────────────────────────────┘
```

**★ 依赖方向是单向的：`Engine` 不依赖 `AI`。**

实测依据：`app/engine/**` 里没有任何一行 import `app/ai`（零命中）；
反向的 `app/ai/tools.py` 明确 `from app.engine import executor, loader, metrics`。
`app/ai/__init__.py` 把这条写成了硬约束：「本层的任何文件都不许被 `app/engine/**` import」。

意义：**"算数字"可以脱离 LLM 单独运行、单独测试**。LLM 不可用时服务照常出报表。
（想了解为什么定成单向，看 `docs/DECISIONS.md`。）

---

## 3. 目录与模块职责

> 下面每一行都对应磁盘上一个真实路径。**本表按现状列，不按"理想分层"列**——
> 只列真实存在的路径，不按教科书分层去补那些并不存在的目录。

| 路径 | 职责 | 当前状态 |
|---|---|---|
| `web/index.html` · `web/app.js` · `web/api.js` · `web/session.js` · `web/style.css` | 前端单页：9 个业务页 + 登录层；`session.js` 管身份、会话态、游客受限拦截 | ✅ 当前 |
| `app/api.py` | 主 API 层 + 静态挂载：6 个基础端点（upload / schema / execute / download / executions / health）+ 5 个任务端点 | ✅ 当前 |
| `app/api_chat.py` | 对话端点：`/api/chat`、`/api/chat/capabilities`、`/api/conversations*` | ✅ 当前 |
| `app/api_datasets.py` | 数据集与业务表端点：`/api/datasets*`、`/api/tables/{table}`（含 export） | ✅ 当前 |
| `app/api_documents.py` | 文档端点：`/api/documents/*`（文本提取 / 摘要 / 下载） | ✅ 当前 |
| `app/api_auth.py` | 账号端点：注册 / 登录 / 登出 / 查重 / 账号列表 / 审批 / 删除 / 验证码 | ✅ 当前 |
| `app/accounts.py` | 账号：PBKDF2 校验值存储、状态机、**模式开关（本机单用户 / 真实审批流）+ 启动迁移**、管理员审批、**内存会话**（含"临时密码登录 → 必须先改密"的会话标记与守卫） | ✅ 当前 |
| `app/captcha.py` | 图形验证码（本机生成，零新依赖） | ✅ 当前 |
| `app/challenge.py` | 「挑战」抽象层（FR-001A）：只认 `challenge_id`/`challenge_proof`，当前实现 = 图形验证码；FR-001B 的滑块接同一个口子 | ✅ 当前 |
| `app/recovery.py` | 密码恢复（FR-001A）：恢复码 / 一次性 reset_token / 一次性 ticket / 管理员临时密码，**全部只存哈希**；三层授权（challenge ≠ 所有权、票据只允许改密）都在这里 | ✅ 当前 |
| `app/state.py` | 运行状态读写：上传 / 执行 / 任务记录（无数据库，纯 JSON 文件） | ✅ 当前 |
| `app/prewarm.py` | 冷启动预热：服务启动时在后台线程把数据集读进进程缓存（不阻塞就绪） | ✅ 当前 |
| `app/ai/intent.py` | 大白话 → **Intent JSON**（调用 LLM） | ✅ 当前 |
| `app/ai/tools.py` | **白名单工具**：确定性计算，内部调用 `engine` | ✅ 当前 |
| `app/ai/service.py` | 编排：一句话 → 一整条可追溯链路 | ✅ 当前 |
| `app/ai/answer.py` | 分区回答 + **数字核对闸门**（LLM 说的数必须与算出来的数对得上） | ✅ 当前 |
| `app/ai/llm.py` | DeepSeek 客户端（OpenAI 兼容协议；无 key 则降级，不假装 LLM 在场） | ✅ 当前 |
| `app/ai/report.py` | 报告形态（周报 / 月报）——组合既有白名单工具，不新造口径 | ✅ 当前 |
| `app/ai/export_docs.py` | 报告导出 Word / Excel / Markdown | ✅ 当前 |
| `app/datasets/registry.py` | 数据源登记表：一个数据源 = 一个 Dataset（含 SHA256、行列数、时间范围） | ✅ 当前 |
| `app/datasets/queries.py` | **业务表唯一的查询入口**（前端不拼 SQL/不自己算） | ✅ 当前 |
| `app/datasets/export.py` | 把同一个查询结果写成 xlsx / csv | ✅ 当前 |
| `app/engine/loader.py` | 取数：Excel → DataFrame；含 `EXPECTED_SHA256` 数据快照核对 | ✅ 当前（冻结） |
| `app/engine/metrics.py` | 销售额口径清单（按 DECISIONS 的 D16 逐条实现） | ✅ 当前（冻结） |
| `app/engine/executor.py` | 计算入口：按口径算指定区间，**数字全部由代码算** | ✅ 当前（冻结） |
| `app/engine/renderer.py` | 渲染：把算好的数字写进 Excel 模板副本，样式保持不变 | ✅ 当前（冻结） |
| `app/engine/docs.py` | Word / PDF 真实文本提取 + 规则化结构化摘要 | ✅ 当前（冻结） |
| `app/repositories/base.py` | Repository **抽象接口** | ✅ 当前 |
| `app/repositories/json_repo.py` | Repository 的 **JSON 文件实现**（当前唯一实现） | ✅ 当前 |
| `app/repositories/json_store.py` | JSON 存储底座：路径解析 + 原子落盘 + 进程内锁 + schema_version | ✅ 当前 |
| `app/spec/models.py` | Report Spec（报表规格）——项目核心数据结构 | ✅ 当前 |
| `scripts/serve.py` | **唯一推荐的启动入口**（`workers=1` 写死在代码里） | ✅ 当前 |
| `scripts/*_e2e.py` · `scripts/*_smoke.py` · `scripts/*_cdp.mjs` · `scripts/session_check.mjs` · `scripts/hermes_gate.py` | 端到端验证与门禁脚本（真 HTTP / 真浏览器 CDP / 真杀进程重启） | ✅ 当前 |
| `scripts/auto_pipeline.py` | 无人值守的 TASK 流水线（Hermes 侧的自动化驱动器：派单 → 等完成 → 跑门禁 → 自动提交） | ✅ 当前 |
| `tests/` | pytest：17 个测试文件 + `conftest.py` + `test_cases.json`（50 条测试集） | ✅ 当前 |
| `templates/weekly_sales_template.xlsx` | 报表样式模板（openpyxl 在它的副本上原地改） | ✅ 当前 |
| `data/Online Retail.xlsx` | 冻结数据快照，541,909 行 × 8 列（SHA256 见 6.1） | ✅ 当前 |
| `outputs/` · `state/` · `data/uploads/` · `data/.cache/` | 运行时目录（产出 / JSON 状态 / 上传件 / 解析缓存）—— 均已在 `.gitignore` 中，不随仓库分发 | ✅ 当前 |
| `data/documents/` | 文档原文落盘目录。**注意**：该目录**当前没有**被 `.gitignore` 忽略，所以里面 3 个早先上传产生的 `.docx` / `.pdf` 被一并提交进了版本库（全仓无任何代码引用它们，属历史遗留）。当前状态是"运行时产物目录 + 已提交文件"混放 | ✅ 当前 |

---

## 4. 核心数据流

两条链路**互相独立**：第 1 条完全不经过 LLM 的算数环节，第 2 条里的数字也全部由引擎算。

### 4.1 确定性报表链路（数据 → 报表文件）

```
data/Online Retail.xlsx
   │
   ▼  engine/loader.py
      · 读成 pandas DataFrame
      · verify_data_source()：算文件 SHA256 与 EXPECTED_SHA256 核对
        （对不上就报错，不出一份口径不明的报表）
   │
   ▼  engine/metrics.py + engine/executor.py
      · 按既定口径过滤（排除取消单 / Quantity≤0 / UnitPrice≤0 等，见 DECISIONS D16）
      · pandas 按区间聚合求和 → **确定性结果**（同一个输入永远同一组数字）
   │
   ├──────────────► 确定性结果（数字）
   │                    │
   │                    ▼  engine/renderer.py
   │                       openpyxl 在模板副本上**原地改**单元格与样式
   │                       → outputs/*.xlsx（真实文件）
   │
   ▼  API 层（app/api.py）
      · 落执行记录到 state/executions.json（含数据 SHA256 / spec / 代码版本 / 耗时）
      · 返回 {execution_id, amount, …, download_url}
   │
   ▼  Web
      · 展示数字与区间；按 download_url 下载真实 xlsx
```

**关键点**：数字只出自 `executor`，Excel 只出自 `renderer`。API 层不取数、不算数、不写 Excel；
前端更不参与计算（它只显示后端给的值）。

### 4.2 自然语言分析链路（一句话 → 回答）

```
用户输入一句话（Web）
   │
   ▼  app/ai/service.py（编排）
      · 组装上下文 → 交给 intent
   │
   ▼  app/ai/intent.py  →（LLM）→  Intent JSON
      · 「想分析什么、哪个区间、哪个维度」被解析成结构化意图
   │
   ▼  app/ai/tools.py（**白名单工具**）
      · 意图只能命中白名单里的工具，工具内部调用 engine 做确定性计算
      · 不在白名单里的意图 → 明确不执行（不自由发挥）
   │
   ▼  确定性计算结果（数字，来自 engine）
   │
   ▼  app/ai/answer.py
      · 组织成分区回答
      · **数字核对闸门**：LLM 生成的文字里出现的数字，必须与工具算出的数字一致，
        对不上就拦下来（LLM 只负责"怎么讲"，不负责"算出多少"）
   │
   ▼  结构化结果 → API（app/api_chat.py）→ Web 渲染
```

若 LLM 不可用（无 key / 调用失败）：`llm.available()` 为 False，
链路降级为关键词匹配，并**明确告知"未接 LLM"**——不假装有 LLM，也不编答案。

（周报 / 月报是这条链路的延伸：`app/ai/report.py` 只做"组合既有工具"，一个口径都不新造。）

---

## 5. 关键边界

### 5.1 Engine / AI

- `engine/` 是**确定性计算核心**，是"数字从哪来"的唯一答案。
- 依赖方向单向：`engine` **不 import** `ai`；`ai` 单向依赖 `engine`。
- `engine/` 是**冻结资产**：上层 AI/API 应**通过既定接口使用它**，
  **而不是在 LLM 层重新实现业务计算**。
- 判据（一眼可查）：`app/engine/**` 出现 LLM / 提示词 / 网络调用即为越界。

### 5.2 LLM / 确定性计算

- LLM 的职责只有两件：**理解需求**（→ Intent）与**组织语言**（→ 回答/解读）。
- **任何让 LLM 直接算钱的实现都算错**。数字一律由 pandas 算出。
- 拦住"LLM 报错数"的机制是 `app/ai/answer.py` 的数字核对闸门（见 4.2），
  不是"相信模型"。
- 无 key 时降级且明说；两者都拿不到就说 `llm_empty_response`，**绝不编答案**。

### 5.3 API / Web

- API 层是**薄层**：入参校验 → 调下层 → 落执行记录 → 出响应。
  它不做取数/计算/渲染/Excel 读写。
- 前端**不渲染技术细节**（不把哈希、路径、内部状态码直接摊给用户看）。
- **权限提示（如游客的 ⚠ 记号）是前端行为**，见第 6 节——它不等于服务端强制。

### 5.4 Repository / Storage

- `app/repositories/base.py` 定义**抽象接口**；`json_repo.py` 是**当前唯一的实现**；
  `json_store.py` 是它底下的文件存储底座（路径、原子写、进程内锁、schema_version）。
- **当前持久化是 JSON 文件，放在 `state/` 目录下，不是关系数据库（不是 MySQL）**。
  文件由存储层在**首次写入时创建**（`state/` 已在 `.gitignore` 中、不随仓库分发，
  所以全新克隆里这个目录根本不存在，跑起来才出现）。存储层管理的文件一共这几个：

  | 文件 | 存什么 |
  |---|---|
  | `state/uploads.json` | 上传记录 |
  | `state/executions.json` | 执行记录（审计用：数据哈希 / 口径 / 代码版本 / 耗时） |
  | `state/tasks.json` | 任务（含冻结的 Spec 快照） |
  | `state/documents.json` | Word / PDF 文档记录 |
  | `state/conversations.json` | 对话记录 |
  | `state/datasets.json` | 数据集登记表 |
  | `state/accounts.json` | 账号（**只存校验值，不存明文密码**） |

- 这套存储**只在单进程下安全**（靠进程内锁串行化"读-改-写"）。
  这是 `scripts/serve.py` 把 `workers=1` 写死在代码里的原因，不是随手写的默认值。
- 因此：**看到 `Repository` 请不要推断它在连数据库**——它现在就是文件。

### 5.5 Account / 业务授权

- `app/accounts.py` 解决的是**身份 / 登录**相关问题：注册、登录校验、状态机
  （待批 / 通过 / 拒绝 / 停用）、管理员审批、会话建立与解析。
- **当前账号能力解决的是身份/登录相关问题，不等同于已完成服务端业务级 RBAC/权限控制。**
  两者不是一回事：一个是"你是谁"，一个是"你被允许做什么"，后者**未实现**（见第 6、7 节）。
- 游客身份在前端就没有服务端会话（`sessionId` 为空串），因此不存在"游客的服务端权限"这种东西。

---

## 6. 当前安全与权限边界

### 6.1 已实现

| 项 | 实现 |
|---|---|
| 密码存储 | PBKDF2-HMAC-SHA256 + **每账号独立随机盐**；只存校验值，不存明文 |
| 密码比对 | `hmac.compare_digest`（定长比较，不因"第几位开始不同"提前返回） |
| 账号状态机 | 待批 / 通过 / 拒绝 / 停用；四种"登不上"给四句不同的话（401 / 403 区分） |
| 账号模式 | `SRA_REQUIRE_APPROVAL`（默认 0 = 本机单用户，**注册即生效**；置 1 才走审批流）；启动时跑一次幂等迁移，解开"卡在待批、又没人能批"的死锁（D22） |
| 管理员端点 | 服务端真的校验：账号管理相关端点走 `accounts.require_admin()` |
| 图形验证码 | 本机生成，零新依赖（`app/captcha.py`） |
| 数据溯源 | 数据快照 SHA256 核对（`loader.EXPECTED_SHA256`，`GET /api/health` 也会回带） |
| 审计线索 | 执行记录绑定数据哈希 / Spec / 代码版本 / execution_id |

### 6.2 当前限制（不好看，但是真的）

- **游客限制主要由前端实现**：受限清单（导入 / 导出 / 改任务 / 账号管理 / 删除）写在
  `web/session.js` 的 guard 里，表现是 ⚠ 记号 + 弹窗拦截 + 不发请求。
  **服务端只拦了账号管理那几条**（`app/api_auth.py` 里 3 处 `require_admin`）。
- **session 机制不是 JWT / OAuth 那种完整认证授权体系**：它存在**内存**里
  （`app/accounts.py` 的模块级 dict）、**无签名**、**无权限范围**、**重启服务即全部失效**。
  它只回答"这台机器上这次登录的是谁"。
- **业务端点目前没有完整的服务端权限控制**：对话、数据集、业务表、文档、执行等端点
  不做登录校验——因为当前定位是单机自用；这也意味着**前端 guard 是绕得过去的**。

### 6.3 未实现

服务端业务级权限控制（RBAC / 权限范围）、会话持久化与续期、登录失败次数限制与锁定、
审计日志的独立存储与检索、多租户隔离。→ 见第 7 节。

---

## 7. 明确未实现 / 后续演进

> 本节是**唯一**允许出现"未来能力"的地方。上面的分层图与数据流图里不画它们，
> 也不会在旁边标一个 "planned" 虚线框——那会让人误以为它们已经在这一层里了。

| 能力 | 当前状态 | 归属 TASK |
|---|---|---|
| 关系数据库落库（MySQL） | ❌ **未实现**。现在持久化是 `state/*.json` 文件 + 进程内锁，仅单进程安全 | TASK-007（定时 + 审计） |
| 定时调度 | ❌ **未实现**。目前只能手动触发执行；代码里没有任何调度器，也没有 cron 配置 | TASK-007 |
| 服务端业务级权限控制（RBAC / 权限范围） | ❌ **未实现**。当前只有身份/登录 + 账号管理端点的管理员校验；游客限制是前端行为 | 未排期 |
| 评估 Harness（样例集 → 5 项子指标） | ❌ **未实现**。目录里**没有**这个脚本，只有 `tests/test_cases.json` 这 50 条测试集 | TASK-006 |
| `/api/parse` 与 `/api/spec/{id}` | ❌ **未实现**。Spec 目前由便捷路径或完整 Spec 直接提交，不经过 LLM 解析 | 待排期 |
| 会话持久化 / 续期 / 锁定策略 | ❌ **未实现**。会话在内存，重启即失效（见 6.2） | 未排期 |
| 多进程部署 | ❌ **未支持**（刻意的）。JSON 落盘只在单进程安全，`serve.py` 把 `workers=1` 写死 | 随 TASK-007 一起解决 |

---

*v1 创建：2026-09-21（描述的是设计设想）· v2 重写：2026-09-28（改为描述当前事实）*
