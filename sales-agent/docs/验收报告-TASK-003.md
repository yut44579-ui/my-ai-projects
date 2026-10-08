# 验收报告 · TASK-003（Word / PDF 文档输入）

> 施工：Claude Code · 日期：2026-09-23 · 依据：`reviews/006-upgrade-gate.md` 的
> TASK-003 验收段（真实 docx / 真实 pdf → 文本提取 → 文本长度 > 0；原始文件与解析结果都不许前端伪造）
> + 会话交接指令

## Status

**PASS（自动化 + 真 HTTP 全绿；浏览器实拍待 Hermes CDP）**

---

## 📊 关键结果（数字）

| 项目 | 数字 |
|---|---|
| 真 .docx 提取 | **chars = 258**（> 0，且含写入的标题与正文原句） |
| 真 .pdf 提取 | **chars = 229**，**blocks = 2**（两页都读了，不是只读首页） |
| 结构化摘要 | 关键词 **7** 个（词频 ≥2）+ 关键事实 **8** 条（正则抽取） |
| `tests/test_documents.py` | **16 passed** |
| `scripts/docs_e2e.py`（真服务 + 真 HTTP + 真文件） | **51 项断言全部通过**，末行 `🎉 TASK-003 文档闭环全部通过` |
| 失败路径 | `.txt` → 400 `document_unsupported`；扫描件 PDF → 422 `document_no_text`（**不算成功、不留记录**） |
| 新旧管道并存 | `/api/upload` 拒 `.docx`（400）、`/api/documents` 拒 `.xlsx`（400）—— 各拒各的 |

---

## 本次实际做了什么（交接时的缺口）

接手时盘面：**后端已经完成，前端完全没有**。

| 部分 | 接手时状态 | 本次动作 |
|---|---|---|
| `app/engine/docs.py`（399 行） | ✅ 已在 | 未改（只读；`_key_facts` 的 `context` 原样切片那条断言已经把"改写上下文"这个坑钉死了） |
| `app/api_documents.py`（279 行，5 个端点） | ✅ 已在，已挂进 `api.py` | 未改 |
| `app/state.py` 的文档落盘函数 | ✅ 已在 | 未改（除了下面那条契约修复） |
| `tests/test_documents.py`（481 行 / 16 项） | ✅ 已在，全绿 | 改了 1 处断言（见"踩到的坑"） |
| **前端「数据管理」页的「文档资料」区** | ❌ **完全没有**（`grep 文档 web/` = 0 命中） | **本次新建** |
| **真 HTTP 的文档闭环冒烟** | ❌ 没有 | 新建 `scripts/docs_e2e.py` |
| **`/api/health` 被加了 `documents` 字段** | ❌ 契约违规 | 已修（改实现，不改断言） |

### 前端新增（复用既有模式，没有新造一套）

| 文件 | 加了什么 |
|---|---|
| `web/api.js` | 文档端点 6 个（上传/列表/详情/全文/摘要/全文 URL）+ 一个 `requestText()`（全文是 `text/plain`，不能按 JSON 解；**出错分支与 `request()` 逐字一致**，仍然是统一错误体） |
| `web/index.html` | 「数据管理」页末尾一张卡片：文件选择 + 上传按钮 + 行内状态 + 上传结果表 + 文档列表 + **empty state** + 正文查看器 + 摘要区 |
| `web/app.js` | `loadDocuments / renderDocuments / renderDocResult / openDocument / renderDocSummary / loadDocSummary / bindDocuments`；接入 `refreshAll()`、顶栏搜索、`init()` |
| `web/style.css` | 正文查看器**复用既有 `.code` 等宽排版**（只加一行 `max-height`）+ 摘要/关键词/关键事实的样式 |

**复用而非重写**：卡片/表格/空状态/三态/错误出口全部沿用 TASK-002 已有组件
（`.card` `.empty` `.table` `status()` `toggleEmpty()` `errorBanner()` `cell()` `escapeHtml()`），
没有引入任何新框架、新的状态管理、新的请求方式。

### 三态落点（前端「文档资料」区）

| 状态 | 落点 |
|---|---|
| loading | 上传时 `status("doc-upload-state", …, "loading")`（转圈）；取全文/摘要时同样 |
| empty | `#docs-empty` 空状态块 + `toggleEmpty("docs-empty", rows.length > 0)`；**且区分"真没有"与"被搜索过滤掉了"**（空态标题会跟着变，否则用户会以为文档丢了） |
| success | 上传结果表 `#doc-result-wrap` + 列表真行 + 绿色 success 文案 |
| error | 行内红字（带后端 `error.code`，如 `document_no_text` / `document_unsupported`）+ 全局 `errorBanner()` |

**列表不预读全文**：列表只带元信息；正文要用户真的点「看正文」才去
`GET /api/documents/{id}/text` 取，而且**取不回来就把查看器收起来并报错**，绝不显示半截内容。

---

## 验收证据

### ① 真实文本提取（Gate 的硬判据：文本长度 > 0）
`outputs/_docs_e2e.log`：
```
【1】真 Word（.docx）上传 → 文本提取
   ✅ 上传 .docx → 201
   ✅ **提取到真实文本（chars > 0）** | chars=258
   ✅ 提取结果里能读到文档标题（不是编的）
   ✅ 正文段落被完整提取（含金额那句）
【2】真 PDF 上传 → 文本提取（两页）
   ✅ **提取到真实文本（chars > 0）** | chars=229
   ✅ 块数 = 页数（两页都读了） | blocks=2
   ✅ 第 1 页正文提取到了
   ✅ 第 2 页正文提取到了（不是只读了首页）
```

### ② 解析结果不许伪造（Gate 原话："原始文件和解析结果都不能由前端伪造"）
- 文件字节 → 后端流式落盘 + 边写边算 SHA256（`shell` 上传响应中的 `sha256` 是**落盘那份字节**的哈希）。
- 全文端点 `GET /api/documents/{id}/text` 是**重新从原文文件提取**的（不是读前端传的、也不是读缓存的），
  并在响应头回 `X-Doc-Chars` / `X-Doc-Sha256`；E2E 断言"全文长度 == 记录里的 chars"、
  "写进去的每一段原文都能一字不差找回来"。
- 前端**没有**任何解析逻辑：它拿到的就是后端提取出来的字符串。

### ③ 摘要必须是抽取式（"数字不许编"这条铁律在文档能力上的落点）
E2E 断言：
```
✅ **摘要每一句都是原文原句（抽取式，没让任何模型改写）**
✅ 每个关键事实的值都是原文里的字面量
✅ 摘要标明了它是针对哪一版原文生成（input_sha256）
✅ 再点一次 → 取回已缓存的那份（不重算，幂等）
```
本阶段**没有接 LLM**（符合 Gate："第一版文档处理非常克制，暂时不要做 RAG"）。

### ④ 失败要看得见，且不留半截数据
```
✅ 上传 .txt → 400 document_unsupported
✅ 扫描件（有页面无文字）→ 422 document_no_text，**不算成功**
✅ 两次失败都没留下记录（不留半截数据）
✅ 查不存在的 doc_id → 404 document_not_found
```
`document_no_text` 这一条是关键：**能打开但提不出文字**（扫描件/空文档）判定为失败，
不记一条 chars=0 的记录冒充成功。

### ⑤ 两条输入管道并存（没为文档能力动冻结资产）
```
✅ 老表格端点仍拒收 .docx（没为文档能力放宽） | status=400
✅ 新文档端点拒收 .xlsx（各拒各的） | status=400
```
`/api/upload` 只认表格、`/api/documents` 只认 `.docx/.pdf`，两条管道各管一种输入，谁也没改谁。

### ⑥ 落盘与刷新恢复
```
✅ state/documents.json 真的落盘了
✅ 两份文档都记在盘上 | 2 条
✅ 每条记录都带真实字符数与 SHA256（审计凭据）
✅ 原文文件也都在盘上（取全文要重读原件）
✅ **新连接重新 GET 能读回同样的两条（刷新恢复靠后端，不靠 JS 内存）**
```

### ⑦ 与冻结端点的边界（AC-09）
```
✅ /api/health 的 state 块键集与冻结版逐字一致（文档计数没有搭车塞进来）
   keys=['executions', 'readable', 'schema_version', 'state_dir', 'tasks', 'uploads']
✅ 文档上传没有污染表格上传计数（两条管道各有各的目录）
```
`app/api.py` 对本次改动的全部内容 = docstring 若干行 + 1 个 import + 1 行 `include_router`，
**没有任何 `def` 签名变化**；`metrics.py / executor.py / renderer.py / loader.py` 零改动。

---

## ⚠️ 踩到的坑（按交接指令记录下来）

**坑 1：测试断言抓到了实现真缺陷 —— 改实现，不改断言。**
`test_repositories.py::test_state_summary字段与旧版一致` 把 `/api/health` 的 `state` 块钉成
**键集逐字相等**。而 TASK-003 的实现往里面加了 `"documents": count_documents()` →
这条测试红了。这是**测试对、实现对**：`/api/health` 在 Gate 里是冻结的 Legacy Contract，
响应形状不许动。处理：`documents` 计数从 `state_summary()` 摘掉，改走 `/api/documents` 的列表 `total`；
同时在 `test_documents.py` 里补一条**反向断言**把 health 的键集钉死，防止以后再有人顺手加字段。

**坑 2（TASK-002 提速项，一并修了）：parquet 缓存落盘从未成功。**
详见 `docs/验收报告-TASK-002.md` 的"提速项里发现并修掉了一个真缺陷"一节 ——
`InvoiceNo` 是 object 列，pyarrow 写 parquet 直接抛 `ArrowInvalid`，
导致"磁盘缓存一次都没命中"，实际每轮都在重新解析 90~150 秒。改成 pickle 后恢复。

---

## ⚠️ 需决策 / 遗留问题

1. **【需决策】「一个上传入口还是两个」。** 现在实现成**两个平行入口**：表格走 `/api/upload`、
   文档走 `/api/documents`（新前缀）。理由是这样**一行都不用动冻结的旧端点**，最保守。
   如果产品上想要"一个上传框自动按后缀分流"，那需要改 `api.py` 的既有语义 → **属于架构变更，得单独开 Gate**。
   在此之前我按保守方案执行，并在页面上把两块并排显示、各自标清走的是哪条端点。
2. **浏览器实拍证据还没有**：本机不装 Playwright（项目铁律），前端契约有
   `tests/test_web.py`（20 项）+ 真 HTTP 的 `scripts/docs_e2e.py`，但"在真浏览器里点上传按钮看三态变化"
   这一条归 Hermes 的 CDP 走查，**本次不算通过**。
3. **不做 OCR**：扫描件如实报 `document_no_text`（符合 Gate"第一阶段只做文本提取"）。
   哪天要收扫描件，是**新能力**（要装 OCR 库），需要单独走流程。
4. `app/api_documents.py` 与 `app/api.py` 各有一份 8 行的 `_safe_filename()` —— 重复是**故意**的
   （`api.py` 是冻结件），等 `api.py` 因别的原因需要改时再一起提到公共模块。
5. `scripts/docs_e2e.py` 为了不重复造夹具，`import` 了 `tests/test_documents.py` 里的两个真文件构造器
   （`make_docx` / `build_minimal_pdf`）。若 Hermes 不接受"脚本依赖测试模块"，可把这两个构造器
   提到 `tests/_doc_fixtures.py` 再两边共用。
6. **`documents.json` 目前是单文件数组**（与 `uploads/tasks/executions` 一个模式）。
   并发写靠进程内锁 —— 单用户没问题，多用户要等 TASK-009 换 SQLite 时一并解决。
