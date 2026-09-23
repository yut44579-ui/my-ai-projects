# 验收报告 · TASK-002（企业级 UI 骨架 + Repository 抽象 + 测试提速）

> 施工：Claude Code · 日期：2026-09-23 · 依据：`docs/施工指令-TASK-002-UI骨架.md`
> + `D:\GPT_Project_Reviews\reviews\006-upgrade-gate.md`（AC-01..AC-11）

## Status

**PASS（待 Hermes CDP 走查确认 AC-02 / AC-07 的浏览器侧）**

自动化那一半全绿；剩下 AC-02（窗口尺寸变化的真实响应）与 AC-07（真实浏览器刷新恢复）
按 Gate 的分工属于 Hermes 的 CDP 走查项 —— 那两项目前**只有代码与结构层证据**，
浏览器实拍证据还没有，如实标出来（不拿结构测试冒充浏览器验收）。

---

## 📊 关键结果（数字）

| 项目 | 数字 |
|---|---|
| **全量测试（含冻结的 executor / renderer 独立 Oracle）** | **121 passed in 25.91s**（缓存就绪） |
| 定向测试（repositories + api + tasks + web） | **87 passed**（无缓存 340.08s → 缓存就绪 18.10s，见下节） |
| 前端契约/反造假测试 | **20 passed** |
| 未提交改动 | **9 改**（tracked）+ **14 新**（untracked），TASK-002 / 003 混在一起（交接时如此） |
| 冻结资产被改动 | `metrics.py` / `executor.py` / `renderer.py` / `loader.py` **零改动** |

### 测试提速前后（同 4 个文件 / 同 87 项，apples-to-apples）

| 运行 | 命令 | 结果 | 日志 |
|---|---|---|---|
| **A. 完全关掉缓存（真基线）** | `SRA_TEST_CACHE=0 pytest tests/test_repositories.py tests/test_api.py tests/test_tasks.py tests/test_web.py -q` | **`87 passed in 340.08s (0:05:40)`** | `outputs/_task002_tests_nocache.log` |
| **B. 缓存就绪（warm）** | 同上，缓存已建好 | **`87 passed in 18.10s`** | `outputs/_task002_tests_warm.log` |

**提速 ≈ 18.8×**（340.08s → 18.10s），**同样 87 项全绿、断言一个字没改**。

warm 那次的缓存统计：
```
[提速] read_excel 缓存统计：命中内存 1 次 / 命中磁盘 2 次 / 真正解析 0 次（平均 0.0s）/ 未插手 3 次
```
即：整轮跑完，那个 22MB 的大表**一次都没有重新解析**。

> 另有一份**交接前留下的**基线 `outputs/_pytest_baseline_full.log`：`105 passed in 481.15s`。
> 它是**另一个文件集（105 项）**，且用的是"parquet 落盘失效、只有内存缓存"的旧 conftest ——
> 所以它既不与本表同集、也不代表"完全无缓存"。本表 A/B 两行才是同集同项的严格对比。

### 三个数放在一起看（同一个 bug 的代价）

| 状态 | 同 4 文件 / 87 项 | 说明 |
|---|---|---|
| 关掉缓存 | 340.08s（87 passed） | 每个文件都真解析 |
| **交接时的 conftest（parquet 落盘静默失败）** | **205.10s**（`1 failed, 86 passed`） | 只有内存缓存；磁盘缓存 0 命中。**那 1 项红的就是 `/api/health` 契约违规那条**（改实现后已转绿） |
| **修好落盘格式后（warm）** | **18.10s**（87 passed） | 磁盘缓存 2 命中，0 次真解析 |

也就是说：**"提速项"在交接时实际只兑现了 340→205 那一部分（约 1.7×），
剩下 205→18 那一大截（约 11×）是被 parquet 那个 bug 吃掉的。**

### ⚠️ 提速项里发现并修掉了一个真缺陷（请重点看这段）

`tests/conftest.py` 原本把解析结果落成 **parquet**。实测**磁盘缓存一次都没命中过**，
每次都在走"真解析 90~150 秒 → 写缓存失败 → 下次再解析 90~150 秒"：

```
[提速] 真正解析一次大表：Online Retail.xlsx shape=(541909, 8) 用时 92.0s（其余调用将命中缓存）
[提速] parquet 缓存落盘失败（不影响正确性，退回原速）：ArrowInvalid(
        "Could not convert 'C536379' with type str: tried to convert to int64",
        'Conversion failed for column InvoiceNo with type object')
```

根因：`InvoiceNo` 列里既有 `C536379` 这种取消单号、又有纯数字 → pandas 读出来是 object 列，
而 pyarrow 写 parquet 要先把 object 列定成一个具体类型 → 直接抛错。后果是**只有内存缓存生效**
（进程内有效，跨进程等于没有），所以"提速"在真实使用时基本没兑现。

**修法**：落盘格式 parquet → **pickle**（`frame.to_pickle` / `pd.read_pickle`）。
pickle 让 dtype 原样往返、不做任何类型推断 —— 正好满足 conftest 自己承诺的
"只改数据怎么进内存，不改数据是什么"。另外给读缓存加了一道形状比对（与 `meta.json` 的
`shape` 不符就当作没有缓存重新解析），避免"写了一半的缓存"悄悄换掉测试数据。

> 这条按项目铁律属于**范围内**（TASK-002 的提速项本身），不是顺手重构：
> 我只改了 `tests/conftest.py` 这一个文件里的缓存落盘格式（parquet→pkl），
> 被测代码一行未动、**断言逻辑一个字未改**。

---

## AC-01 ~ AC-11 逐条证据

### AC-01 真实页面入口 ✅
- `/` 返回 200 + `text/html`，内容是 `web/index.html` → `tests/test_web.py::test_root_serves_index_html`。
- 无静态 demo 数据：`test_frontend_has_no_fake_shortcuts` 扫 `app.js / api.js / index.html / style.css`
  里不含 `mock` / `TODO` / `FIXME` / `setTimeout` / `占位` / `已开启`；
  `test_frontend_has_no_hardcoded_business_numbers` 扫死写的 `316412.16` / `19950` → 都没有。

### AC-02 三栏结构 ⚠️（结构证据有，浏览器实拍待 CDP）
- `test_layout_is_three_column_and_responsive`：CSS 里有 `grid-template-areas`、三栏区域名、
  `@media` 断点，且宽度 token 与设计规格逐字一致（`--w-sidebar: 200px`、`--w-rightpanel: 355px`、`--h-topbar: 60px`）。
- `test_root_serves_index_html`：HTML 里 `class="topbar" / sidebar / workspace / rightpanel` 四个骨架块真实存在。
- **浏览器里拖窗口看真实响应** → Hermes CDP 项（未做）。

### AC-03 真实 API ✅
- `web/api.js` 是**唯一**发 HTTP 的地方，全部 `fetch`，无 mock / 无假响应。
- `test_web.py::REQUIRED_ENDPOINTS` 钉死前端必须真的调 `/api/upload`、`/api/tasks`、`/api/executions`、
  `/api/health`、`/run`、`/runs`、`/openapi.json`。
- `/api/health` / `/api/tasks` / `/api/executions` 三条真实 HTTP 往返 → 见 TASK-003 的
  `scripts/docs_e2e.py` 与 `scripts/web_e2e.py`（起真 uvicorn）。

### AC-04 真实导航 ✅
- 8 条路由（总览/销售/客户/产品/异常/周报/数据管理/系统设置），HTML 里 `page-{route}` + `nav-{route}` 都在。
- 每个页面都有真 empty state（不是只有标题）→ `test_every_page_has_an_empty_state` 逐个页面断言
  `.empty` + `.empty-title` + `.empty-hint` 三件套存在。

### AC-05 真实状态（5 种）✅
`test_five_ui_states_have_real_anchors` 逐项断言：
| 状态 | 落点 |
|---|---|
| loading | `.spinner`（CSS+JS 都有） |
| empty | `.empty` 块 |
| success | `.badge.success` + JS 里的 `"success"` 分支 |
| error | `.banner.error` + 统一出口 `errorBanner()` |
| disabled | `:disabled` 样式 + HTML 里真实 disabled 控件（且都写明"为什么还不能用"，`test_disabled_controls_explain_themselves`） |

### AC-06 旧业务不回归（旧 12 端点）✅
**真服务 + 真 HTTP + 真文件**跑通十步闭环（`scripts/web_e2e.py`，`outputs/_web_e2e.log`）：
**78 项断言全绿、0 失败**，末行 `🎉 004A 十步闭环全部通过`。
每一步都用**上一步响应里的值**（file_id → task_id → spec → execution_id → download_url），零硬编码：
```
✅ GET / 返回 200 + 响应是 HTML
✅ 8 条路由 page-*/nav-* 全在 + 数据管理页 ①②③④⑤ 老控件一个没少
✅ 静态资源可访问 /app.js(53404B) /api.js(7896B) /style.css(19858B)
✅ GET /api/download/x_83cb5e361f09 → 200
✅ 产出不是空文件 | 6068 字节
✅ openpyxl 读回 B4 = 316412.16（与执行响应同一个数） | B4=316412.16
✅ B5 = 19950
✅ B4 数字格式仍是 #,##0.00（D17-2）
✅ 执行记录里有刚才那次执行 / 记录里的金额也是 316412.16
```
最后三行的意义：**D16 口径的 316412.16 与 19950 是 openpyxl 从真产出的 xlsx 里读回来的**，
与执行响应是同一个数 —— 旧确定性内核一行没动、产出仍然可用。
- 旧端点语义测试：`tests/test_api.py` 18 项 + `tests/test_tasks.py` 24 项全绿。
- 静态挂载没吃掉 `/api/*`：`test_api_routes_still_win_over_static_mount`、
  未知 `/api/xxx` 仍返回统一错误体 `test_unknown_api_path_still_returns_unified_error_json`。

### AC-07 刷新恢复 ⚠️（代码 + 后端证据充分，浏览器实拍待 CDP）
- 路由用 `location.hash`（`currentRoute/renderRoute`），刷新后停在原页面；
  `init()` 第一件事就是重新拉 `health / tasks / executions / documents`，不读任何 JS 内存快照。
- **后端侧实测**（`scripts/web_e2e.py` 第【6】步"模拟刷新"）：
```
✅ GET /api/tasks 里能查到这个任务（页面刷新后的恢复路径）
✅ 任务状态 created → has_run（刷新后从后端读到的）
✅ 再读一次执行记录，内容完全相同（刷新后读得到且一致）
```
  以及 `scripts/docs_e2e.py` 第【9】步：换一条**全新 HTTP 连接**重新 GET，读回同样的两条文档记录。
- **浏览器里真的按 F5** → Hermes CDP 项（未做）。

### AC-08 无伪造 ✅
- 无 mock / 无 fake API / 无硬编码业务金额 / 无假任务 ID / 无假执行状态 / 无 `setTimeout` 假装完成
  → 逐字扫描，见 AC-01 的两个测试。
- 失败必须看得见：`scripts/web_e2e.py` 的 `_failure_paths()` 造了 4 种真失败（上传非表格 / 建任务缺参 /
  执行不存在的任务 / 下载不存在的 execution），全部要求"正确状态码 + 可读错误 + 统一错误体"。
- 真失败也留痕：`_failed_run_evidence()` 造一份不同源的数据 → 能建任务（带 warnings）→ run 被 422 拒 →
  落一条 `status=failed` 的记录，且**没有** `download_url` / 产出路径（不产出假文件）。

### AC-09 前后端边界 ✅
`git diff` 逐文件核对：
| 冻结资产 | 是否被改 |
|---|---|
| `app/engine/metrics.py` | **未出现在 diff 里（零改动）** |
| `app/engine/executor.py` | **未出现在 diff 里（零改动）** |
| `app/engine/renderer.py` | **未出现在 diff 里（零改动）** |
| `app/engine/loader.py` | **未出现在 diff 里（零改动）** |
| `app/api.py` | 只加了 3 类**纯增量**内容：docstring 若干行、1 个 import、1 行 `include_router`；**没有任何 `def` 签名变化**（`git diff app/api.py \| grep "^[-+]def "` 为空） |
| `app/api.py` 既有 11 端点语义 | 请求/响应语义未动（`tests/test_api.py` 全绿） |

**本次实际抓到并修掉的一处契约违规（重要）**：
TASK-003 的文档计数曾被塞进 `/api/health` 的 `state` 块（`"documents": count_documents()`）。
`/api/health` 在 Gate 里属于**冻结的 Legacy Contract**，`tests/test_repositories.py::test_state_summary字段与旧版一致`
把它钉成了**键集逐字相等**——于是这条测试红了。**处理方式是改实现、不改断言**：
把 `documents` 从 `state_summary()` 摘掉，文档计数改走它自己的 `/api/documents` 列表 `total`。
现在 `tests/test_documents.py` 里也补了一条反向断言，把"health 的 state 键集"钉死，
防止以后再有人顺手往里加字段。

### AC-10 回归 ✅（自动化 + 真 HTTP 部分）
| 测试 | 结果 |
|---|---|
| **全量 `pytest`（5 个测试文件，含冻结的 `test_executor.py` / `test_renderer.py`）** | **121 passed in 25.91s** |
| `tests/test_repositories.py tests/test_api.py tests/test_tasks.py tests/test_web.py` | **87 passed** |
| `tests/test_web.py`（前端契约 + 反造假） | **20 passed** |
| `tests/test_documents.py`（TASK-003） | **16 passed** |
| `scripts/web_e2e.py`（真服务真 HTTP：旧业务十步闭环） | **78 项断言，0 失败** |
| `scripts/docs_e2e.py`（真服务真 HTTP：文档闭环） | **51 项断言，0 失败** |
| 同 4 文件无缓存基线 | **87 passed in 340.08s** |

### AC-11 Git Scope ✅
`git status --short`（实测）：
```
 M .gitignore                  ← 加 data/.cache/（提速缓存的落点，不是源码）
 M app/api.py                  ← 纯增量：docstring + import + include_router
 M app/state.py                ← Repository 抽象重构（公开函数签名不变）
 M scripts/web_e2e.py          ← 十步闭环适配新 UI 的 id
 M tests/test_web.py           ← TASK-002 前端契约测试
 M web/api.js / app.js / index.html / style.css   ← 新 UI
?? app/repositories/  tests/test_repositories.py  tests/conftest.py   ← TASK-002
?? app/api_documents.py  app/engine/docs.py  tests/test_documents.py  scripts/docs_e2e.py  ← TASK-003
?? docs/（施工指令 3 份 + Gate 记录 2 份 + 验收报告 2 份）
```
未出现范围外的文件。**冻结资产一个都没进 diff**：
`app/engine/metrics.py`、`app/engine/executor.py`、`app/engine/renderer.py`、`app/engine/loader.py`
`git diff --stat` 里完全没有它们。

注：TASK-002 与 TASK-003 的改动都还没 commit（交接时就是这个状态），
所以这一份 scope 里同时含两个 TASK 的文件 —— 按文件后缀能分清归属
（`docs*` = 003；`repositories/conftest/web 骨架` = 002）。

---

## 改了什么文件

**TASK-002 范围内**
| 文件 | 改动 |
|---|---|
| `web/index.html` | 三栏骨架 + 8 个页面 + 每页 empty state（+ TASK-003 的文档资料区） |
| `web/app.js` | 页面逻辑：路由 / 渲染 / 五态 / 全部数字来自后端响应 |
| `web/api.js` | 接口层：所有 HTTP 集中在此 |
| `web/style.css` | 深色企业级 token + 三栏栅格 + 响应式断点 |
| `app/repositories/`（新） | Repository 抽象（base / json_repo / json_store）—— 为 TASK-009 换 SQLite 留的口子 |
| `app/state.py` | 改为走 Repository；**公开函数签名与返回形状不变** |
| `tests/test_repositories.py`（新） | 23 项：抽象层与旧 JSON 行为一致性 |
| `tests/conftest.py`（新） | 测试提速（**本次修掉了 parquet 落盘失效的真缺陷**） |
| `tests/test_web.py` | 前端契约 + 反造假扫描 |
| `scripts/web_e2e.py` | 真 HTTP 十步闭环适配新 UI |
| `.gitignore` | 加 `data/.cache/` |

**TASK-003 范围（本次一并收尾，详见另一份报告）**
`app/engine/docs.py`、`app/api_documents.py`、`tests/test_documents.py`、`scripts/docs_e2e.py`、
`web/{index.html,app.js,api.js,style.css}`（文档资料区）、`app/api.py`（+1 行挂载）。

---

## ⚠️ 需决策 / 遗留问题

1. **【需决策】`/api/documents` 这个前缀是否算"冻结的 12 端点之外"** —— 我按"新能力只加端点、
   旧端点语义一字不改"执行：文档能力全部走新前缀 `/api/documents*`，`/api/upload`（表格）**没有**被
   改造成"两种都收"。两条管道各拒各的（`/api/upload` 拒 .docx、`/api/documents` 拒 .xlsx），
   E2E 里两条都实测过。**如果 Hermes 期望的是"一个上传入口两种格式"，那是一次架构变更，需要单独 Gate
   —— 现在这样是更保守、不动冻结资产的做法。**
2. **AC-02 / AC-07 的浏览器实拍证据还没有**：结构层与后端层证据齐全，但"窗口尺寸变化的真实响应"和
   "真浏览器 F5 刷新恢复"按 Gate 分工归 Hermes CDP。这两项在本次报告里**不算通过**。
3. **`app/state.py` 的重构幅度较大**（186 增 / 186 删级别），虽然公开签名不变、且
   `test_repositories.py` 逐条比对了新旧行为，仍建议 Hermes 重点看一遍 diff。
4. `scripts/docs_e2e.py` 为了不重复造夹具，`import` 了 `tests/test_documents.py` 里的两个真文件构造器。
   代价是"脚本依赖测试模块"。若 Hermes 不接受，可把两个构造器提到 `tests/_doc_fixtures.py` 再两边共用。
5. `app/api.py` 与 `app/api_documents.py` 各有一份 `_safe_filename()`（8 行重复）。
   重复是**故意**的：`api.py` 是冻结件，为一个辅助函数去动它不划算。已记在此处，等 `api.py`
   因为别的原因需要改时再一起提到公共模块。
