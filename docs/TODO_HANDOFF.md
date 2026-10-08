# 任务需求与进度交接（TODO / HANDOFF）

> 最后更新：2026-10-09（移动端适配 + 已部署到腾讯云，公网可操作）
> 这份文件是**唯一的恢复入口**：下次继续时先读它，再读 `docs/DECISIONS.md`。

---

## 〇、★ 下一步（按优先级）

P5 已完成四批。**当前无在飞工作。** 剩余置灰仅 2 项（安全设置 / 系统设置）。

**① ✅ 导航已按需求正文修正**（D46 待办已由用户裁定，见 D47）
   · `/analytics` 「数据分析」→「**商业分析**」（需求 §八 用词）
   · 删除「**智能分析**」（`/ai/insight`）—— 我自己起的名，与商业分析重复
   · 补回「**AI助手**」→ 重定向到工作台（其功能是工作台右栏问答区）
   当前导航：**启用 18 项 / 置灰 4 项**

**② P5 剩余置灰页（4 项）**：

| 菜单 | key | 建议 |
|---|---|---|
| 数据同步 | `/data/sync` | 建议**并入数据连接页**（同步记录已在那里），不单独建页 —— 或直接删掉该菜单项 |
| 商业洞察 | `/ai/business` | 需市场/竞对数据，系统内没有 → 只能如实声明边界（照市场研究页做法） |
| 系统设置 | `/settings` | 配置走 `.env`，无可配置项 → 建议保持置灰并写明原因 |
| 安全设置 | `/settings/security` | V1 两级角色已够（AUTH_SPEC §1 明确不做权限矩阵）→ 建议保持置灰 |

★ 用户已明确说过 **智能分析不用做了**（已删除）。若要新增跨模块分析，
  建议直接增强「商业分析」页，而不是再开一个新菜单（避免又一次重复）。

**③ §二十一 缺失的附带表**（按实际需要再建）
   `customer_contacts` / `customer_sources` / `customer_products` / `customer_tasks` /
   `customer_notes` / `documents` / `products`
   （`tenants` 用户已裁定不做）

---

## 〇之二、恢复施工步骤

```powershell
# 后端
cd D:\biz-assistant-int\backend
$env:PYTHONPATH = "D:\biz-assistant-int\backend"
Start-Process -FilePath "D:\biz-assistant-int\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WindowStyle Hidden

# 前端
cd D:\biz-assistant-int\frontend ; npm run dev

# 登录：admin / Admin@2026!（测试弱口令，正式用请改）

# 提交前两件事（都应该跑，别只跑一个）
.\.venv\Scripts\python.exe scripts\check_user_text.py    # markdown 泄漏扫描（后端+前端）
node scripts\ui_check_page.mjs <url> scripts/checks/<页>.txt <名>   # 页面结构断言
```

---

## 一、原始需求在哪

| 文件 | 说明 |
|---|---|
| `D:\hermes\cache\原始需求-项目总控提示词.txt` | **总控提示词全文（1284 行，28 节）**，一切需求的依据。★ 项目内**没有副本**，只在 hermes 缓存里 |
| `D:\hermes\cache\ref_spec_workbench.md` | 参考图工作台规格（7KB） |
| `D:\hermes\cache\paidan_ui_spec.txt` | 派单 UI 规格（1KB） |

**项目的产出位置**：`D:\biz-assistant-int\`

**铁律（总控提示词里的，必须继续遵守）**
- §二 禁止制造业务假数据；没有真实数据就显示 0 / 空状态 / 暂无数据
- §三 FACT / INFERENCE / SUGGESTION / BUSINESS_CONCLUSION / VALIDATED 必须分离
- §四 禁止重复造轮子（已有能力不许做平行版本）
- §五 禁止过度设计（微服务/Kafka/K8s/多 Agent/向量库/RAG 一律不加）
- §六 每个 TASK 先评审再施工
- §二十六 20 条禁止事项（编造功能/测试结果/数据/接口/连接状态…）
- §二十七 输出格式：结论 / 现状 / 复用 / 最小改动 / 风险 / 是否需要外部评审 / 验收

---

## 二、需求 vs 实现：完整对照表

### ✅ 已完成（有真实接口 + 页面 + 断言验收）

| 需求节 | 内容 | 实现 | 验收 |
|---|---|---|---|
| §九 | 客户管理 | 客户列表/详情/来源追溯/时间线 | ✅ |
| §十 | 客户访问（可识别才记） | `customer_events.VISIT` 事件类型（**未新建表**，见 D20） | ✅ 28 项 |
| §十一 | 客户时间线完整 | 事件流含访问/已读回执 | ✅ |
| §十二 | 沟通记录 8 维度 | 补齐「是否人工确认/是否自动发送/是否触发业务事件/已读」 | ✅ P0 |
| §十三 | AI 回复像人 | 策略闸门 + DeepSeek | ✅ |
| §十四 | AI 权限边界 | 代码层 PolicyGate（13 类敏感词） | ✅ |
| §十五 | 客户风险机制 | `risk_events`（正常/需关注/高风险），证据锚点用 message_id | ✅ 16 项 |
| §十六 | AI + 人工接管 | AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE | ✅ |
| **§十七** | **获客与营销闭环** | `prospect_research`：原文 → 抽事实（引用逐条回验）→ 生成话术 → 人工确认 | ✅ **21/21 + 防幻觉 6/6** |
| **§十八** | **老板汇报助手** | 报告指标 2 → **15 个**，分 6 组，跨实体下钻，支持月报 | ✅ **13/13** |
| §十九 | 汇报可追溯 | 下钻条数 == 报告数字（同口径，已断言） | ✅ |
| **§二十** | **数据连接中心** | `data_connections` + `sync_records`；MySQL/PG 真测试；凭据加密 | ✅ **28/28** |
| §二十一 | 数据模型 | 15 张表（见下） | 部分 |
| **§二十二** | **AI 知识库** | `knowledge_documents` + `knowledge_chunks`；原文归档 + 切片（偏移精确）+ **关键词检索（不用向量库，见 D43）** + 结合业务数据分析（知识≠业务事实，结构性分离） | ✅ **后端 23/23 + 前端 16 项** |
| §二 | 数据来源枚举 | SourceType 3 → **8 种**（补 SYNC/WEB/TEST/DEMO） | ✅ P0 |
| §九 | 客户生命周期 | 7 → **11 种**（补 已联系·未读/已读·未回复/暂时沉默/明确拒绝） | ✅ P0 |
| §九 | 客户来源 10 种 | 新增 `customers.acquisition_channel`（**与 source_type 是两个维度**） | ✅ P0 |
| §一 | 产品定位与闭环 | 工作台 + 12 个功能页 | ✅ |

### ⏳ 未做（按优先级）

| 优先级 | 需求节 | 内容 | 备注 |
|---|---|---|---|
| **P5** | §八 | 侧栏剩余置灰页：智能分析 / 方案 / 商业洞察 / 文件资料 / 系统设置 / 安全设置 | 用户与权限页已完成 |
| 低 | §二十一 | 缺表：`customer_contacts` / `customer_sources` / `customer_products` / `customer_tasks` / `customer_notes` / `documents` / `products` | 多为附带表，按实际需要再建 |
| — | §二十一 | `tenants`（多租户） | **用户已裁定不做**：以实际需求出发，与 D3 单租户一致 |

---

## 三、当前系统规模

```
路由        97 条
数据库表    19 张
迁移        23 个（全部可逆）
前端页面    23 个
决策记录    60 条（D1~D60，docs/DECISIONS.md）
验收截图    48 张（docs/screenshots/）
断言文件    22 个（scripts/checks/*.txt，接口覆盖自动发现自 openapi.json）
```

**17 张表**：`users` `customers` `customer_messages` `customer_events`
`customer_handover_events` `risk_events` `import_batches` `reports`
`opportunities` `projects` `project_customers` `marketing_contents`
`data_connections` `sync_records` `prospect_research`
`knowledge_documents` `knowledge_chunks`

> D11 冻结 7 表 **7/7 全部落地**；D28 解冻后按需求新增其余表。

---

## 四、P4 知识库的设计要点（已实现，供收尾时参考）

- **为什么不用向量库**：§二十二 要知识库、§五 却禁向量数据库/RAG Pipeline。
  已按 A 方案用「关键词匹配 + 标签 + 生效期过滤」消解冲突（见 D43）。
  ★ 理由已写进 `/api/knowledge/summary` 的 `note`，**界面上能看到**。
- **知识≠业务事实是结构性的**：`/knowledge/analyze` 返回两个独立字段
  `knowledge_used`（背景知识）与 `business_data`（真实数字），不靠提示词。
- **检索可解释**：返回 `matched_terms`（命中哪些词）+ `score`，
  比向量相似度更便于人工核对"为什么选了这段"。
- **切片偏移必须精确**：`content == text[char_start:char_end]`，已断言（见 D43 ④）。

---

## 五、怎么继续（恢复步骤）

```powershell
# 1) 起后端（若已停）
cd D:\biz-assistant-int\backend
$env:PYTHONPATH = "D:\biz-assistant-int\backend"
Start-Process -FilePath "D:\biz-assistant-int\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WindowStyle Hidden

# 2) 起前端（若已停）
cd D:\biz-assistant-int\frontend ; npm run dev

# 3) 登录
#    地址 http://127.0.0.1:5173/
#    账号 admin / Admin@2026!     ← ★ 这是测试弱口令，正式用请改（见第六节）

# 4) 跑全站回归（13 个页面断言）
cd D:\biz-assistant-int
$body = @{ username="admin"; password="Admin@2026!" } | ConvertTo-Json
$env:DSH_TEST_TOKEN = (Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/auth/login" `
  -Method Post -Body $body -ContentType "application/json").access_token
foreach ($p in @(
  @{u="/"; s="scripts/checks/ai-assistant.txt"},
  @{u="/login"; s="scripts/checks/login.txt"},
  @{u="/opportunities"; s="scripts/checks/opportunities.txt"},
  @{u="/projects"; s="scripts/checks/projects.txt"},
  @{u="/marketing"; s="scripts/checks/contents.txt"},
  @{u="/researches"; s="scripts/checks/researches.txt"},
  @{u="/connections"; s="scripts/checks/connections.txt"},
  @{u="/visits"; s="scripts/checks/visits.txt"},
  @{u="/research"; s="scripts/checks/research.txt"},
  @{u="/analytics"; s="scripts/checks/analytics.txt"},
  @{u="/reports"; s="scripts/checks/reports.txt"},
  @{u="/risk"; s="scripts/checks/risk.txt"},
  @{u="/customers/1863"; s="scripts/checks/customer-detail.txt"}
)) { node scripts/ui_check_page.mjs "http://127.0.0.1:5173$($p.u)" $p.s "check-tmp" }
```

**改口令**：`$env:ADMIN_PASSWORD='新口令'; .venv\Scripts\python.exe scripts\create_admin.py --username admin --reset`

---

## 六、需要用户处理的事项

1. **改管理员口令**：`admin` / `Admin@2026!` 是测试弱口令，必须改（命令见上）
2. **`AUTH_SECRET_KEY`**：已写入 `.env`（64 位随机，已被 `.gitignore` 挡住）。
   ★ 换环境部署时必须**重新生成**，不要复用 —— 它同时用于**加密数据连接凭据**，
   密钥泄露 = 凭据泄露。
3. **`DEEPSEEK_API_KEY`**：已配（用户提供），AI 功能依赖它。
4. **数据连接凭据**：MySQL/PG 的"真测试"需要用户自己填连接信息；
   目前库里那条 MySQL 连接是**故意填错口令**的验收样本（状态显示"已配置未测试"）。

---

## 七、施工中踩过的坑（都已记进 DECISIONS，别再犯）

| 坑 | 教训 | 决策 |
|---|---|---|
| MySQL ENUM 是字面量，Python 加成员不改列定义 | 必须手写 `ALTER TABLE MODIFY COLUMN` | D20 |
| 同一 ENUM **类型名被多列共用**，只改一列会 1265 | 先查 `information_schema.COLUMNS` 找出**所有**引用列 | **D36** |
| `REPORT_TIMEZONE` 是字符串，传给 `datetime.now()` 报 TypeError | "名字像"不等于"类型对" | D39 |
| ORM 模型与 Pydantic schema **同名**，后者被覆盖 → 500 | 同模块同时用两者时给 ORM 侧加别名 | **D41** |
| 断言测错实体（用没数据的客户测访问时间线） | 断言必须对准**有对应数据**的实体 | D34 |
| 回归时把 `analytics.txt` 复用给风险中心页 | 每页必须有**专用**断言文件 | D34 |
| 面向用户文案里写了 markdown `**`，界面原样显示 | 界面不渲染 markdown，别写星号 | D27 |
| Pydantic `min_length` 拦成 422，与项目 400 约定不一致 | 长度校验放服务层，422 只用于"结构不对" | D37 |
| 等待条件 `innerText.length > 80` 把正常登录页判成没挂载 | 阈值要按页面实际内容定 | D31 |
| 修改时误删换行导致两行代码粘连 | 用 edit 工具而非整串替换脚本 | — |

---

## 八、用户的决策记录（原话要点）

| 决定 | 内容 |
|---|---|
| 解冻 D11？ | **授权解冻**，做商机/项目/营销三模块 |
| 做认证？ | **做完整登录**（我先定规格 `docs/AUTH_SPEC.md` 再实现） |
| `tenants`？ | **不做** —— "以实际需求出发"，与 D3 单租户一致 |
| 已读状态？ | **"就和钉钉那种一样，显示就可以了"** |
| 获客研究方案？ | 选 **A** —— 用户提供原文，不联网抓取 |
| 知识库方案？ | **未明确回复** → 按交接约定走 **A**（不引向量库，关键词检索），已实现并记 D43 |
| 施工节奏？ | **"按顺序继续做吧"** —— P0→P1→P2→P3→P4→P5 |
| 中途叫停？ | 两次说过「先这样 停 / 休息一会」—— 停下前要把在飞模块收尾到已验证状态，不留半成品 |

---

## 九、完工度自评（要诚实）

**对照总控提示词全文**：
- 已覆盖：§一 §二 §三 §四 §五 §六 §七 §九 §十 §十一 §十二 §十三 §十四 §十五 §十六 §十七 §十八 §十九 §二十 + §二十一（部分）
- 未覆盖：§二十二（知识库）、§八 的 7 个次要页面、§二十一 的 7 张附带表

**不要再说"100% 完成"** —— 之前就是这样误报的（拿自设目标当完成度）。
正确的说法是：**核心闭环已完成，知识库与次要页面待做。**
