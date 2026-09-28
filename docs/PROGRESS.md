# PROGRESS.md · sales-report-agent

## 2026-09-22 凌晨（用户休息，Hermes 自动执行）

### 已完成
| 时间 | 事项 | 结果 |
|---|---|---|
| 04:1x | ChatGPT 外部评审 R001/R002/R003 | 方向+拆解+施工方案全部通过（reviews/001-003） |
| 04:30 | TASK-002A 施工指令 v5（含 D16 口径） | 评审 PASS，可交 Claude |
| 05:0x | **TASK-002A 由 Claude Code 完成** | `app/engine/{loader,metrics,executor}.py` + `tests/test_executor.py` |
| 05:1x | **Hermes 独立门禁 + 独立核对** | GATE-1/2/6 + 数据 SHA256 全过；**自写 pandas 实现 vs executor 差值 0.0** |
| 05:2x | Git commit | `8fc01a2` |

### 关键数字（可复核）
```
区间 2011-11-21 ~ 2011-11-27：
  原始行数 19950 / 排除 296（C开头230 + Qty≤0 260 + 单价≤0 66，多规则命中 260）
  有效行 19654 / 销售额 £316,412.16 / 排除净额 -8,227.14
  数据 SHA256 43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d
```

### 进行中
- TASK-002B（Excel 渲染，openpyxl 原地改）
- 待办：002C（API）→ 004A（★ 前端，用户最关心）

### 流程（已固化）
```
用户提需求 → Hermes 出方案 → ChatGPT 评审 → Claude CLI 写代码 → Hermes 独立门禁验收 → git commit
```

## 2026-09-25 · STEP-C 账号审批 + 游客限制（TASK-STEP-C / D21）

| 项 | 结果 |
|---|---|
| 真服务重启后账号仍在 | `scripts/stepc_accounts_e2e.py` **34/34**（kill 掉真 uvicorn 再拉起，两个账号照旧能登） |
| 游客真的被拦住（真浏览器） | `scripts/stepc_guest_cdp.mjs` **55/55**（点受限入口前后 `window.fetch` 次数 10 → 10） |
| 游客被拦住（服务端） | 管理三条端点：游客 401 / 普通账号 403（不是靠前端不显示按钮） |
| 前端受限行为 | `scripts/session_check.mjs` **253/253**（`Session.guard()` 逐个动作要 false） |
| 单测 | 新增 `tests/test_auth_admin.py`（19 条）、`tests/test_web_guest.py`（10 条） |

产物：`account` 记录多了 `status / role / reviewed_at / reviewed_by`；
`DELETE /api/auth/accounts/{u}`；`web/session.js` 的 `guard()/mark()/applyGuards()`；
`index.html` 的 `#guest-bar` / `#guard-modal` / `#set-accounts-card`。

## 2026-09-25 · 排序语义 + 时间范围误导（用户报的两个问题 / GPT 冻结规格）

用户原话：「指标按销售额 或者订单的其他某个限制词时 他的顺序也没有对上 会有 127 的订单数排在
147 的前面」+「时间范围是 2026 年的某月某天到某个时间点 但是他会弹出 2010 的时间是对不上的」。

| 项 | 结果 |
|---|---|
| 全量单测 | `pytest` **537 passed**（新增 30 条后端回归 + 4 条前端静态，基线 503 全绿） |
| 真浏览器交互实测 | `scripts/sort_range_cdp.mjs` **42/42**（点表头 → 切指标 → 翻页 → 切维度，逐步量 DOM） |
| 截图证据 | `outputs/_cdp_shots/sort-01-default-day.png` / `sort-02-user-sort-kept.png` / `sort-03-out-of-range.png` |

### 五条规则（前端 `web/app.js`，状态只有一处：`panel.sort_source`）
```
默认排序   按日/按周 → dimension_value ASC（时间序列语义，★不许改成指标降序）
           按国家   → 当前指标 DESC；客户表/产品表 → sales_amount DESC
Rule 2 切指标   sort_source=default → 排序交回后端默认；=user → **原样保留**（不许覆盖）
Rule 3 切维度   → 清掉用户排序，回 default，用新维度默认排序
Rule 4 点表头   → sort=该列、order 翻转、sort_source=user
Rule 5 翻页     → dimension / metric / sort / order 完整保留（翻页不碰排序）
```
「当前排序：销售额 ↓」一行文字 + 表头 ▲▼ + 未排序列无标记 = 三层提示（只靠表头高亮用户看不见）。

### 时间范围
```
页面文案   删掉「更新于 2026-09-22」（数据源登记时间，误导）→「数据覆盖：2010-12-01 ~ 2011-12-09」
日期框     钉 min/max = data_range；敲进去就判，越界即时提示 + 「使用数据范围」一键填满
0 条        后端仍如实返回 0 条、notes 原话照旧（不截断、不修正、不假装）；前端另给快捷入口
后端回显    query.start/end = **用户填的那个区间**；query.window_start/end = 收紧后的统计窗口
```
口径与算法一个字没改（`resolve_window` 收紧统计窗口的老行为保留，桶完整性/说明都挂它上面）。

## 2026-09-28 · FR-001A 密码恢复闭环（忘记密码 → 真能重置）

用户原话：「忘记密码之后无法找回，加上具体功能即可以通过忘记密码来**新增新密码**，
但是**需要验证账户**」。上一版「忘记密码？」只弹一句"本机没法找回" —— 那不是能力，
是一句免责声明。本轮把它换成一条**真能走通**的链，并严格守住三层授权不许合并。

依据：`D:\GPT_Project_Reviews
eviews-fr001a-password-recovery-review.md`（有条件通过）
+ `decisions\8-fr001a-password-recovery.md`。

| 项 | 结果 |
|---|---|
| 全量单测 | `pytest` **597 passed**（新增 `tests/test_fr001a_recovery.py` 57 条；基线 540 全绿） |
| 真服务端到端 | `scripts/fr001a_e2e.py` **45/45**（真 uvicorn + 真 HTTP + **真杀进程重启**） |
| 前端行为检查 | `scripts/session_check.mjs` **253/253**（四步找回流程 + 改密那屏不改动既有 253 条） |

### 三层授权（★ 本 TASK 的灵魂，不许合并）
```
challenge（图形验证码）→ 只允许【进入恢复流程】   POST /api/auth/reset/request
recovery code（恢复码）→ 证明【账户所有权】       POST /api/auth/reset/verify
ticket（一次性票据）   → 只允许【修改密码】       POST /api/auth/reset/commit
```
绝对禁止：`username + captcha = ownership`；`滑块通过 = ownership`；
`reset/request 发现是老账号就直接签发恢复码`（= 直接账户接管）。
**老账号**（没有恢复码）不能自助补发 → 只能走管理员临时密码 → 登录后强制改密 → 自己生成恢复码。

### 6 个新端点（评审冻结，不许合并改名）
```
POST /api/auth/reset/request                    匿名；签发 reset_token（**不泄露账号是否存在**）
POST /api/auth/reset/verify                     匿名；恢复码 → 一次性 ticket（校验 challenge 交叉绑定）
POST /api/auth/reset/commit                     匿名；★ 只收 ticket + new_password
POST /api/auth/recovery-code                    已登录；生成/轮换自己的恢复码（明文只返回一次）
POST /api/auth/accounts/{u}/temp-password       仅 admin；只能发给 role=user（不给管理员发）
POST /api/auth/password/change                  已登录；临时密码登录后**唯一**的出路
```

### 冻结参数与并发
```
reset_token / ticket / session  ★ 全内存，重启全失效（fail-closed）；内存里只存 SHA-256
恢复码    20 位 Base32（4-4-4-4-4，≈100 bit）；secrets 生成；只存 SHA-256；只显示一次；用后即废
恢复码失败 ★ 独立计数（≠ 登录失败）；5 次 → 60s 冷却；★ 输错不销毁正确的码
临时密码  12 位易读字符；30 分钟；secrets；single-use（**首次成功登录立即消费**）；只存哈希
must_change_password ★ 服务端 guard（中间件）：只放行 password/change 与 logout，其余业务 API 403
改密成功  旧 session 全部作废 + 签发新 session（密码变了 = 认证状态重建）
```
六类并发/越权口子逐一堵死（都有对应测试）：
① ticket 内部绑定 account_id（commit 不收 username）② reset_token × challenge 交叉绑定
③ 恢复码轮换原子替换 ④ 恢复码"检查+消费"同一临界区 ⑤ ticket 并发重放（pop 式单次消费）
⑥ 临时密码并发登录只算一次。
**复用**：Repository 的进程内锁（`JsonCollection.update_first`）、既有 `captcha`（图形的部分一行没重写）、
既有 PBKDF2 摘要与失败计数写法 —— 没有另起一套。

### 编号为 52 条的验收（A1–A5 / B1–B8 / C1–C5 / D1–D8 / E1–E6 / F1–F20）
逐条对着评审清单写在 `tests/test_fr001a_recovery.py`，测试名前缀就是清单编号；
另补了评审末尾点名的 5 个最高优先级 Gate（原子消费 / ticket 不带 username /
`accounts/exists` 枚举旁路节流 / 临时密码首次登录立即消费 / pending·rejected 不被恢复流程绕过）。

### 既有测试的三处改动（都是被本轮冻结规格**取代**的旧断言，逐条说明）
```
tests/test_chat.py        端点总清单：申报新增这 6 条（项目规定新增端点必须在这里登记）
tests/test_web_login.py   ① 「忘记密码」那条：旧断言要求它"弹一句找不回"，本轮规格要求它"进四步流程"
                          ② 禁用词表里的 "token"：本轮前端**必须**转手递回两个不透明一次性凭证
                             （reset_token / ticket），改成**白名单**式检查（只许这几种写法 +
                             仍然禁止 sessionStorage/cookie/localStorage 存它们）
                          ③ 帮助文案里的"短信"：改成"手机验证码"（同一个意思，不出现禁用词）
docs/                      ARCHITECTURE 能力表与模块表加两行（app/challenge.py、app/recovery.py）
```

## 2026-09-29 · FR-002B 导出直达桌面 + 「期间」列日期序列号缺陷

用户原话：「在提问如『帮我生成一星期以内的周报』的时候，虽然能够生成 word / excel /
markdown 等数据，但是**还是那个问题，无法打开**。**你帮我弄到桌面**。」

实测真因两条（不是猜，是量出来的）：
```
① 用户平时用的 Edge 配置里下载目录被设成了 D:\  → 文件全掉在 D 盘根目录，
   用户在桌面上找不到 → 体感就是"打不开"（文件本身是好的：Excel 能正常加载）
② ★ 真缺陷：销售数据-销售表-*.xlsx 的「期间」列导成 40875（Excel 日期序列号），
   而且 number_format = "@"。真因：那一列**声明的是文本格式**，单元格里却是日期值 ——
   Excel 拿到"日期值 + 文本格式"这对组合就把内部序列号原样显示出来。
```

| 项 | 结果 |
|---|---|
| 全量单测 | `pytest` **655 passed**（新增 `tests/test_fr002b_desktop.py` 39 条；基线 616 全绿） |
| 真服务端到端 | 真 uvicorn（`scripts/serve.py --port 8011`）+ 真 HTTP：销售表 / 周报 docx·xlsx·md 全部落到**真实桌面**，字节数与响应一致，openpyxl 读回是真日期 |
| 桌面上的真实产物 | `销售数据-销售表-2011-11-25_2011-11-30-202609290041.xlsx`（5686B）/ `销售周报-2011-11-28至2011-12-04 (1).xlsx`（9849B）/ `… (2).docx`（39832B）/ `….md`（4966B） |

### 缺陷修复前后（同一个文件字段，读回实测）
```
修复前（9-28 22:13 的旧导出）：A2 value=40875  type=int       number_format='@'
修复后（本次真实导出）      ：A2 value=datetime(2011,11,25)  number_format='yyyy-mm-dd'
```
改动落在两处，都在 `app/datasets/`（**engine 一行未动**）：
```
queries.py  SALES_AXIS_FORMATS：维度轴格式跟着维度走（day/week → date；country → text）
export.py   _excel_value(value, fmt)：只在列声明为 date/datetime 时才转真日期，
            **不再"看着像日期就转"**（上一版正是这样把日期塞进了文本格式的列）
```

### 新增两条出口（老出口一个字没改）
```
POST /api/exports/desktop   把一次导出直接写到本机桌面（source=table|report）
POST /api/exports/reveal    在资源管理器里定位**刚刚生成的那个文件**
```
* 桌面目录**走系统机制**解析：`SHGetKnownFolderPath`（shell32）→ 注册表 `User Shell Folders`
  → `SHGetFolderPathW`，逐个 `is_dir()` 验证；三个都拿不到 → 明确报错，**不静默写到别处**。
  **不用** `Path.home()/"Desktop"`，**不硬编码**某人的桌面路径（前者在 OneDrive 重定向下会翻车）。
* 请求体里**没有路径字段**（`extra="forbid"`）→ `{"path": "C:\..."}` 直接被顶回 422；
  reveal 也只能带 `export_id` 做校验，真正传给 explorer 的路径由服务端从自己的记录里取。
* 同名不覆盖（`… (1).xlsx` / `… (2).xlsx`，`O_CREAT|O_EXCL` 抢名字）；
  原子写（同目录临时文件 → `os.replace`），失败清理临时文件与占位文件。
* **部署前提**：只在"服务端与用户桌面同机、同 Windows 会话"时成立（本机单用户自用）——
  响应里的 `deployment` 字段如实写着这句话，ARCHITECTURE 6.2 也记了这条限制。

### 一处**没做**的（如实记录）
周报 xlsx 的「趋势」表日期是**文本**（`number_format='General'`），显示正确、**不是** 40875
那类缺陷，D5 要求"确认"——已确认并加了防回归断言（读回必须仍是可读的 `YYYY-MM-DD`）。
把它也升级成真日期会打掉一条既有冻结断言（`str(cell.value) == 原字符串`），
属于"顺手扩"，本轮**不动**，如实上报。

## 2026-09-29 · FR-002C 记住账号（加固 + 补齐「清除已记住账号」）

| 项 | 结果 |
|---|---|
| 用户原话 | 「首先是**记住账号**之后，下次登录时就会默认有这个账号。」 |
| 事实核查 | **已经实现了**（`web/session.js` `sra.remembered-name` + 载入回填 + 登录成功保存）——所以本轮是"加固 + 补齐缺失的一小块"，不是重做 |
| 真浏览器实测 | `scripts/fr002c_remember_cdp.mjs` **34/34**（真 Edge + CDP，真注册 → 真登录 → **真 Page.reload** → 真鼠标点「清除」） |
| 全量 pytest | **664 passed**（基线 655；新增 `tests/test_fr002c_remember.py` 9 条） |
| 新增测试 | 9 条，逐条对上 B1–B8（B9/F1 由既有回归 + 上一条真机走查覆盖） |
| 改动范围 | 只碰登录卡片上的"记住账号"这一块：`web/{index.html,session.js,style.css}`（+ 两个测试文件、一个探针脚本） |
| engine / 端点 | `app/engine/**` 一行未动；**没新增 / 没改任何端点**；登录、注册、忘记密码、验证码、会话、权限逻辑一个字未改 |

### 本轮把"记住账号"这件事说死了（评审要求"极其克制"）

```
记住账号 ≠ 记住登录状态 ≠ 记住密码      ← 这句话写进了 web/session.js 的注释，
                                          tests/test_fr002c_remember.py 里有一条测试盯着它
```

* **只记最近 1 个账号名**：`localStorage.setItem(NAME_KEY, name)`，写进去的就是那个字符串本身
  （不 `JSON.stringify`、不编码），覆盖式写入 —— 换账号不会攒出一串。
* **本机存储逐键（真浏览器读出来的原文）**：
  ```
  sra.remembered-name = "13900000001"                       ← 就一个账号名
  sra.who             = {"name":...,"kind":"account","display_name":...,"role":...,"session_id":...}
                                                             ← 身份记录（FR-002A/STEP-C 既有），退出即清
  ```
  逐个键的 value 里搜密码（注册时那串真实明文）、`password`、`pwd`、`token` —— **0 命中**。
* **未勾选 → 不保存**，并且把上一次记住的也清掉；**把勾摘掉**的那一刻就清（不用等下次登录）。
* **「清除已记住账号」放在哪**：登录卡片里、勾选框的**正下方**，只在"已经记着"的时候出现 ——
  `已记住账号 13900000001 清除`（小字 + 一个品牌色文字链按钮）。点一下：记录真的删掉
  （重开页面账号框是空的、勾是未勾的）、账号框清空、那一行收起，并给一句回执
  「已清除记住的账号。下次登录要重新填写账号名。」

### 顺带回答一个容易被"顺手"做过头的地方

重开页面**照样要登录**（实测：reload 后 `login-gate.hidden === false`、顶栏「未登录」）。
记住账号省掉的只是"再敲一遍账号名"这四五个字 —— 没做自动登录、没做免登录、没碰 Token。

### 没做的（如实记录）
* 没有多账号下拉 / 账号列表 / 账号切换器（评审点名不许做）。
* 没有「记住密码」的任何形态：密码框每次都从空的开始，本机存储里也没有它的位置。
