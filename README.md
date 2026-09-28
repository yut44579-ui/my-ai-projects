# sales-report-agent

**企业销售分析 Agent** —— 通过自然语言进行销售、客户、产品、异常等分析，并提供真实数据表、周报、数据管理及账号能力。

用大白话提需求（"做个上周销售汇总"）→ AI 理解 → 出方案供人工确认 → 固化成任务 →
之后自动取数、算数、渲染成保留样式的 Excel 报表 + 一段文字解读。

---

## 项目简介

面向**单用户、不会写 SQL** 的场景。输入是大白话加一份参考报表，输出是样式照旧、
数字可追溯的报表文件。

三条设计底线（决定了后面所有技术选择，也决定了你该往哪看）：

1. **数字一律由代码算**（pandas）—— LLM 只负责"理解需求"和"写解读"，
   任何让模型直接算数的实现都算错。
2. **Excel 用 openpyxl 原地改**，不在参考物副本上重建 —— 否则丢样式。
3. 当前分析链路坚持确定性计算与 LLM 解耦；未来若实现定时执行，仍应复用同一确定性计算边界。

想了解"为什么这么设计、走过哪些弯路"，看 `docs/`（见文末[文档索引](#文档索引)）；
本文件只负责**把它跑起来**。

---

## 环境要求

| 项 | 要求 |
|---|---|
| Python | **3.11.x**（开发与验证基线是 3.11.15；未在 3.12+ 上验证过） |
| 操作系统 | Windows（开发机）已验证；macOS / Linux 理论可行，命令见下 |
| 网络 | 首次装依赖需要；运行时只有 LLM 问答需要外网 |
| LLM Key | 可选。不填仍能启动，只是问答降级为关键词匹配 |

为什么钉 3.11：`pandas 3.x` 与 `openpyxl 3.1.5` 在本项目只验证过这一条线，
换 Python 大版本属于另一次环境变更，不在当前基线内。

---

## 安装

```bash
# 1) 建虚拟环境（项目约定用仓库根目录下的 .venv）
python -m venv .venv          # 请确保这里的 python 是 3.11.x

# 2) 装依赖
#    Windows
.venv/Scripts/python.exe -m pip install -r requirements.txt
#    macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

依赖清单的写法（哪些包、为什么只列这些、为什么钉版本）写在
`requirements.txt` 顶部的注释里 —— **它是"项目声明的依赖"，不是某台机器 venv 的快照**，
所以不要用 `pip freeze` 去覆盖它。

装完可以自检：

```bash
.venv/Scripts/python.exe -c "import fastapi, pandas, openpyxl, docx, pypdf, multipart; print('ok')"
```

`multipart` 不是笔误 —— 上传接口（`/api/upload`）的运行时能力依赖，见 `requirements.txt`。

---

## 配置

```bash
# 3) 配置环境变量
cp .env.example .env          # Windows(cmd): copy .env.example .env
# 然后编辑 .env，至少填 DEEPSEEK_API_KEY（不填也能跑，问答会降级）
```

`.env` 已被 `.gitignore` 挡住，**不会进版本库**；`.env.example` 是模板，
里面每一项都标了「从哪行代码读的 + 默认值是多少」，照着填即可。
**别把真实 Key 写进 `.env.example`。**

所有变量都是可选的除了 `DEEPSEEK_API_KEY`，且留空即用代码默认值。

### 账号模式：默认「本机单用户，注册即生效」

```bash
SRA_REQUIRE_APPROVAL=        # 留空/不填/0 = 本机单用户（默认）；置 1 = 需要管理员审批
```

- **默认（不填）**：注册完**就能登录** —— 第一个注册的账号是管理员，其余是普通账号，
  谁都不需要批准。自己给自己开的机器，不该被"等别人批准"挡在门外
  （曾经的实测故障：库里有个够不着的旧管理员，新注册的账号永远卡在"等待批准"）。
- **置 `1`**：走真实审批流 —— 新账号是「等待批准」，管理员在
  「系统设置 → 账号管理」里批准后才能登录。
- 服务**每次启动**会跑一次账号迁移（`app/accounts.py::migrate_accounts`，幂等）：
  已经卡住的库会被自动收进本机单用户模式（写 `reviewed_by` 留痕），
  **不删账号、不改密码、不产生第二个管理员**。决策与理由见 `docs/DECISIONS.md` 的 D22。

---

## 运行

```bash
# Windows
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/serve.py

# macOS / Linux
PYTHONUTF8=1 .venv/bin/python scripts/serve.py

# 换端口
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/serve.py --port 9000
```

> 命令前面的 `PYTHONUTF8=1` 在**中文 Windows 上是必需的**（cmd 用 `set PYTHONUTF8=1 && ...`，
> PowerShell 用 `$env:PYTHONUTF8="1"; ...`）。原因是 `scripts/serve.py` 打印的启动横幅里
> 有一个 `⚠️` 字符：当 stdout 不能编码它时（**输出被重定向到文件/管道**、或控制台不是
> UTF-8 代码页），进程会在 uvicorn 起来之前就抛 `UnicodeEncodeError` 退出 ——
> 现象是"命令跑完什么都没发生"。加上这个变量后任何代码页都能启动（已实测）。
> **已实测会崩的情形**：把输出重定向到文件或接进管道。
> **未实测的情形**：直接在交互式终端里敲（那种情况下 Python 走 Windows 控制台 API、
> 不按 locale 编码，按机制推断不触发，但没实测过，所以别赌）。
> 根治办法是改 `scripts/serve.py` 横幅里的那个字符 —— 那属于代码改动，
> 不在「工程基线」这个 TASK 的范围内，此处只做记录。

起来之后：

- 界面：<http://127.0.0.1:8000>
- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

> **「存到桌面」这条能力有个前提：服务端与你的桌面必须同机、同一个 Windows 会话。**
>
> 界面上的「存到桌面 / 在文件夹中打开」是把导出文件**由服务端直接写到本机桌面**，
> 桌面目录走系统机制解析（`SHGetKnownFolderPath` → 注册表 → `SHGetFolderPath`，
> 见 `app/desktop.py`），因此 OneDrive 重定向、中文 Windows、改过桌面位置都能正确处理；
> 解析不到时会**明确报错**，不会悄悄写到别处。
> 这条能力**只在"本机自用"这个部署形态下成立**（进程跑在你自己机器上、127.0.0.1 访问）；
> 一旦部署到服务器 / 容器 / 多用户环境，"用户的桌面"就不存在了，必须停用它。
> 浏览器下载那条路**不受影响、照旧可用**（两条出口渲染的是同一份文件）。>

> **★ 请用 `scripts/serve.py` 启动，不要直接用 `uvicorn app.api:app`。**
>
> 原因不是"多一层脚本"，而是 `serve.py` 里把 `workers=1` **写死在代码里**：
> 本项目的状态落盘是 **JSON 文件 + 进程内锁**（`app/repositories/json_store.py`），
> 这套东西**只在单进程下安全** —— 两个 worker 同时"读-改-写" `tasks.json` /
> `executions.json` 会互相覆盖，现场表现为"执行记录凭空少了几条"，最难查的那种问题。
> 直接用 uvicorn 命令就绕过了这道保护。想改多进程，先去落库（见 `docs/DECISIONS.md`）。

---

## 测试

```bash
# Git Bash / macOS / Linux
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest        # Windows 用 Scripts，Unix 用 bin

# Windows cmd
set PYTHONUTF8=1 && .venv\Scripts\python.exe -m pytest

# PowerShell
$env:PYTHONUTF8="1"; .venv\Scripts\python.exe -m pytest
```

**当前基线：655 passed**（本机实测 10 分 27 秒；clean-room 复现链路见下节「可复现性说明」）。

> **`PYTHONUTF8=1` 为什么必须加**：这不是测试的问题，是**中文 Windows 的 locale 问题**。
> 有两个用例会真的起子进程（一个起 Python、一个起 node 跑 `web/session.js`），
> 子进程输出 UTF-8，而父进程按系统 locale（GBK）解码 → 解码炸掉，用例失败。
> 加上 `PYTHONUTF8=1` 后父子两端都用 UTF-8，与测试逻辑无关，纯粹是环境对齐。
> 注意这里**不能**用 `PYTHONIOENCODING=utf-8` 顶替：那个只管子进程**写**什么编码，
> 不管父进程**按什么编码读**子进程的输出，而炸的正是后者。必须是 `PYTHONUTF8=1`
> （和上面[运行](#运行)用同一个变量，记一个就够）。
> 首次跑会解析一遍 54 万行的 xlsx（约 1~2 分钟）并落盘缓存到 `data/.cache/`，之后是秒级；
> 缓存键是数据文件的 SHA256，数据没变就一直有效。想强制关掉：`SRA_TEST_CACHE=0`。

单独跑某一组：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_api.py -q
```

---

## 数据说明

### `data/Online Retail.xlsx` —— 项目唯一在用的数据集

| 项 | 值 |
|---|---|
| SHA256 | `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d` |
| 规模 | 541,909 行 × 8 列 |
| 时间覆盖 | 2010-12-01 08:26 ~ 2011-12-09 12:50（54 周） |
| 维度 | 38 个国家 / 4,372 个客户 |
| 字段 | `InvoiceNo, StockCode, Description, Quantity, InvoiceDate, UnitPrice, CustomerID, Country` |

代码读它的地方：`app/engine/loader.py`（`DEFAULT_DATA_PATH`）。测试也直接读这份文件
（`tests/test_api.py`、`tests/test_tasks.py`、`tests/test_executor.py` 的独立 Oracle）。

**已知脏数据特征**（会被当成边界用例跑）：`CustomerID` 有空值、`Quantity<0` 是退货、
`InvoiceNo` 以 `C` 开头是取消单。

### `data/online_retail.zip` —— 原始下载归档，**代码不读它**

仓库里同时有两个数据文件，容易被问"为什么"：

- `online_retail.zip` 是**最初从上游下载下来的归档包**（内含 `Online Retail.xlsx`，
  已核实）。它的作用是**留存出处**，方便核对"这份数据从哪来"。
- `Online Retail.xlsx` 是把上面那份解压出来、**代码和测试真正读取的工作副本**。

全仓库搜索可确认：**没有任何代码或测试引用 `online_retail.zip`**（`grep -rn online_retail.zip`
在 `app/ tests/ scripts/ web/ templates/` 下零命中），所以删掉它不影响运行，只是丢了溯源线索。

> ⚠️ **实测提醒**：当前这份 `online_retail.zip` **文件不完整** —— 缺少 zip 结尾的
> End-of-Central-Directory 记录，`zipfile` / 解压工具都会报 "not a zip file"。
> 它的开头确实是 `PK\x03\x04` 且第一个条目就叫 `Online Retail.xlsx`，说明是下载/拷贝
> 中途截断的。**需要归档内容时请重新下载，不要指望本仓库这份能解开。**
> 这不影响项目运行（运行只用 `.xlsx`），仅在此登记事实。

### 其他数据相关目录

| 路径 | 说明 |
|---|---|
| `templates/weekly_sales_template.xlsx` | 参考报表样式样本（openpyxl 在它的副本上原地改） |
| `data/uploads/` | 用户上传的表格落盘处（运行时生成，已 gitignore） |
| `data/documents/` | Word/PDF **原文**落盘处，与表格上传是两条独立管道（运行时生成） |
| `data/.cache/` | 测试用的解析结果缓存（pickle，已 gitignore，可由数据文件重新生成） |
| `state/` | 任务/执行记录/上传记录/账号 JSON（运行时生成，已 gitignore） |
| `outputs/` | 产出的报表文件（运行时生成，已 gitignore） |

---

## 项目结构

```
sales-report-agent/
├── app/                     后端（FastAPI）
│   ├── api*.py              各路由模块（api / api_auth / api_chat / api_datasets / api_documents / api_exports）
│   ├── accounts.py          本地账号：注册/登录/管理员审批（只存校验值，不存明文）
│   ├── desktop.py           导出直达本机桌面（桌面目录走系统机制解析 + 原子写 + 防重名）
│   ├── engine/              取数与计算（纯代码，可单测；数字的唯一来源）
│   ├── spec/                需求 → 固化方案的规格层
│   ├── datasets/            数据集登记与取数入口
│   ├── ai/                  LLM 调用（理解需求 / 写解读，不算数）
│   ├── repositories/        数据访问层（JSON 落盘与路径都在这里）
│   └── state.py             状态文件读写
├── web/                     前端（原生 HTML + JS 单页，不用框架）
├── scripts/                 启动与验证脚本（serve / e2e / smoke / gate）
├── tests/                   pytest（含独立 Oracle 对账）
├── templates/               参考报表样式样本
├── data/                    数据集与运行时数据（见上节）
├── docs/                    需求/架构/决策/任务/评审归档（见下节）
├── requirements.txt         依赖基线
├── .env.example             环境变量样例
└── README.md                本文件
```

`outputs/`、`state/`、`data/uploads/`、`data/documents/`、`data/.cache/` 都是**运行时生成**，
已被 gitignore；全新克隆的仓库里不存在这些目录，服务启动时会自己建。

---

## 可复现性说明

这个项目的产出是"报表"，所以"同一个输入必须得到同一组数字"是硬要求，靠四件事保证：

1. **数据固定**：`data/Online Retail.xlsx` 的 SHA256 写在上节，任何人拿到同一份文件
   就能算出同一组数字；测试里的 Oracle 也是读这份文件独立算一遍再对账。
2. **依赖固定**：`requirements.txt` 钉死直接依赖与关键传递依赖的版本（含
   `python-multipart` 这类不 import 但必须有能力依赖）。不升不降。
3. **计算确定**：所有指标由 pandas 代码算出，LLM 不参与任何数字的产生；
   提示词与模型只影响"怎么问、怎么讲"，不影响"算出多少"。
4. **独立对账**：`tests/test_executor.py` 里的 Oracle **刻意不复用被测代码**，
   自己重新 `read_excel` 一遍、自己算一遍，再跟引擎的输出比对 —— 同一个 bug
   要同时出现在两条独立实现里才会漏网。

**clean-room 验证**：本仓库的依赖基线不是"在这台机器上能跑"就算数，而是按下面这条链路
真跑过一遍（全新目录、全新 venv、**没有任何解析缓存**）：

```bash
git clone <本仓库> cr && cd cr          # 全新克隆（不含 .venv / state / outputs / data/.cache）
python -m venv .venv                    # 必须用 Python 3.11.x
.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env                    # 走一遍新人配置流程
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest       # → 655 passed（当前基线）
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/serve.py
# 另开一处访问：GET http://127.0.0.1:8000/api/health → 200
```

其中 `GET /api/health` 会回带数据快照的 `sha256`，可直接和上面表格里的值对一下 ——
**对不上的话后面所有数字都不必看了**。克隆后实测该哈希与原仓库一致（即数据本身可复现），
`pytest` 全绿，服务真的起来并且能通过 HTTP 访问。

---

## 文档索引

**本文件只讲"怎么跑起来"**；为什么这么设计、需求边界、每步验收结论都在 `docs/` 下。

| 想了解 | 去哪看 |
|---|---|
| 需求与成功指标 | `docs/requirements.md` |
| 项目规格 / 架构总览 | `docs/PROJECT_SPEC.md`、`docs/ARCHITECTURE.md` |
| 功能与技术栈全景 | `docs/项目全景说明-功能与技术栈.md` |
| **关键决策与取舍（D 编号）** | `docs/DECISIONS.md` |
| 任务清单与范围边界 | `docs/TASKS.md` |
| 开发进度 | `docs/PROGRESS.md` |
| 前端展示规范 / 深色设计规格 | `docs/前端展示规范.md`、`docs/设计规格-深色企业级.md` |
| 各 TASK 的施工指令（冻结的范围与验收条件） | `docs/施工指令-*.md` |
| 已完成 TASK 的验收报告（含真实数字） | `docs/验收报告-*.md` |
| UI 原型 | `docs/原型-*.html` |
| 长会话防崩规范（本项目的开发纪律） | `docs/防崩规范.md` |
| 外部评审原文归档 | `docs/_gpt_review*.txt` |
| 协作与开发约定（对 AI 协作者） | `CLAUDE.md` |
