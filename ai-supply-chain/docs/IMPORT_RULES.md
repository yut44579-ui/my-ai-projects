# 数据接入规则（TASK-001 冻结版）

> 本文是 TASK-001 评审通过后落地的实现规则。**下游 TASK 必须遵守**，尤其是
> 「TEST 数据默认不进业务数字」这条（见文末 §6）。

## 1. 两步接口：先预览、后入库

| 接口 | 作用 | 是否写库 |
| --- | --- | --- |
| `POST /api/imports/preview` | 只解析：编码 / sheet / 表头行号 / 列名 / 自动映射 / 前 3 行样本 / sha256 | ❌ 绝不写 |
| `POST /api/imports/commit` | 带「用户确认后的映射表 + sha256」真正入库 | ✅ 写 |

- **sha256 必须校验**：commit 传的 sha256 与上传文件不一致 → `400 sha256_mismatch`（防预览与提交之间文件被换）。
- **幂等**：同一 sha256 已存在批次 → `409 duplicate_import`，提示「该文件已导入过，batch_id=N」。
- 追溯：`GET /api/imports`（列表）、`GET /api/imports/{id}`（跳过明细 + 该批次客户 id 列表）。V1 不做撤销导入。

## 2. 编码与解析（中文场景的坑）

- **编码探测**：UTF-8 BOM → UTF-8 → GB18030（GBK 的超集）。识别结果在预览里返回给用户确认；
  解不开就报 `parse_error`，**绝不乱码硬解**。
- **一律当字符串**：电话 `13800138000` 不能被当成数字（会变科学计数、丢前导零）。
  实现上 CSV 走标准库 `csv`、XLSX 走 `openpyxl` 并统一 `str` 化，**没有引入 pandas**。
- **xlsx 多 sheet**：只读第一个（或用户指定）时，响应里用 `sheet_note` 明确告知，不静默。
- **表头行**：默认第 1 行，预览可用 `header_row` 指定（1 起）；表头整行为空 → `parse_error`；
  表头重名 → `parse_error`（无法可靠映射，不许任选一列）。

## 3. 列映射

- 只做**别名精确匹配**（`services/mapping.py` 的别名表），匹配不上就是 `unknown`，交给用户在弹窗里选。
  **绝不做"列名里含'电话'就当地址/电话"的猜测** —— 这正是评审点名的"把备注当电话"风险。
- 6 个目标字段：`name / company_name / phone / email / region / note`。
- 未映射的列 → 进 `skipped_columns_json`：`[{"column": "备注信息XX", "reason": "unmapped_column"}]`，
  **内容绝不写进任何业务字段**（AC2 反向验证过）。
- 同一目标字段被两列命中（别名冲突）→ `400 ambiguous_mapping`，明确报错，不许任选一个。
- 用户在弹窗里取消某列（`target=null`）→ 同样进 `skipped_columns`。

## 4. 去重（本轮最危险的点）

规范化：`email` 去空格转小写；`phone` 只留数字；**NULL / 空串 / 全空白一律不参与匹配**。

| 情况 | 结果 |
| --- | --- |
| 有可比键、命中 0 条 | 新建，`dedupe_state=CLEAN` |
| 有可比键、命中 1 条 | **不新建**，刷新该客户 `last_seen_at`，计入 `rows_deduplicated` |
| 有可比键、命中 ≥2 条 | ★ 冲突：**不合并**，新建且 `dedupe_state=PENDING_REVIEW`，冲突的既有客户 id 记入响应 `conflicts` |
| email 与 phone 都空 | 新建且 `dedupe_state=PENDING_REVIEW`（无法判重，交人工裁决） |

★ 必须同时防 SQL 侧与 Python 侧：MySQL 里 `NULL = NULL` 是 NULL（不命中），
但 Python 里 `None == None` 是 `True`。本实现的防法是**空值根本不构造查询条件**，
候选一律由 SQL 产生，Python 侧从不做 `None == None` 的比较（见 `services/dedupe.py`）。

`last_seen_at` 语义写死：**同一客户被导入命中就刷新，不做别的**（不覆盖其它字段）。
时间列用 `DATETIME(6)`，保证同一秒内的两次导入也能分辨先后。

## 5. 失败语义与计数

**文件级失败**（零入库、返回 400 + 错误码，**不落 import_batches 行**）：

| 错误码 | 触发 |
| --- | --- |
| `empty_file` | 0 字节或没有任何内容 |
| `unsupported_format` | 非 .csv / .xlsx（.txt、老式 .xls、加密工作簿） |
| `too_large` | 单文件 > 10MB |
| `too_many_rows` | 数据行 > 50,000 |
| `parse_error` | 编码无法识别、表头整行空、表头重名、行结构与表头不一致、指定行号越界 |
| `ambiguous_mapping` | 多列命中同一字段 |
| `invalid_mapping` | 映射表引用了不存在的列 / 未知目标字段 |
| `sha256_mismatch` | 上传文件与预览时不是同一份 |
| `duplicate_import` | 同一 sha256 重复提交（409，带 batch_id） |

**行级失败** → 整行跳过（不半入库），原因只许用冻结枚举：
`missing_name | invalid_phone | invalid_email | empty_row`（行级）、`unmapped_column`（列级）。
`raw` 只作报错上下文保留，**绝不进业务字段**；`row` 是文件里的真实行号（含表头行）。

**计数恒等式**（代码里断言守住，破坏就报错，绝不编数据）：
`rows_total = rows_created + rows_deduplicated + rows_skipped`

状态：`SUCCESS`（无跳过行）/ `PARTIAL`（有跳过行）/ `FAILED`（文件级失败，V1 不落行）。

## 6. TEST 数据隔离（下游必须遵守）

- 测试样本文件 `tests/fixtures/customers_sample_TEST_*.csv` 导入时 `source_type` **必须是 TEST**。
- `POST /api/imports/commit` 的 `source_type` **缺省就是 TEST** —— 宁可把真实数据误标成测试，
  也不能让测试数据冒充业务数字。
- 客户列表页：有 TEST 数据时顶部显示黄色 banner「当前包含 N 条测试导入数据」，行内用 Tag 标出来源。
- 一键清理：`scripts/cleanup_test_data.sql` 或 `scripts/cleanup_test_data.py`（等价，无需 mysql 客户端）。
- ★★ **下游查询默认过滤 `source_type='REAL'`**：汇报类查询（TASK-007 起）算业务数字前必须加这个过滤，
  否则 TEST 数据会被算进业务数字。客户列表页 `GET /api/customers` 是**唯一例外**（它要显示 TEST 数据才能跑通链路）。

## 7. 业务数字与分页元数据（D4/AC6）

- 业务数字（客户总数、TEST 条数、`rows_total/created/deduplicated/skipped`）**一律走 `EvidenceValue`**
  （`{value, state, source_type, evidence_ref}`），前端不得自己算。
- 分页元数据（`page` / `page_size`）不是业务数字，正常返回裸值。
- 空态页面零数字：没有数据时只显示「暂无客户数据」+ 引导，不显示 0、不显示假图表。

## 8. 硬边界（本 TASK 不做）

只做 CSV/XLSX；不接 PDF/Word/PPT/企业微信/邮件/CRM；不做编辑/删除/导出客户；不做撤销导入。
新增依赖只有 `openpyxl`（xlsx 解析）与 `python-multipart`（文件上传），**不引 pandas / chardet / Redis / Celery / Docker**。
