# TASK-001 真实调用证据（由 scripts/collect_evidence.py 自动采集，未手工编辑）

采集时间：2026-10-03 21:21:06

### ① alembic upgrade head（在临时库上真跑一遍，证明迁移可建表）

```
$ DATABASE_URL=<临时库 biz_assistant_alembic_check> .venv/Scripts/alembic.exe upgrade head
INFO  [alembic.runtime.migration] Context impl MySQLImpl.
INFO  [alembic.runtime.migration] Will assume non-transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 978f01c47fe5, init baseline (no business tables)
INFO  [alembic.runtime.migration] Running upgrade 978f01c47fe5 -> 36e74cad14a0, add customers and import_batches

$ show tables
  alembic_version
  customers
  import_batches

$ show create table customers
CREATE TABLE `customers` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `company_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `phone` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '只存数字（规范化后），空值一律为 NULL 不存空串',
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '小写（规范化后），空值一律为 NULL 不存空串',
  `region` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `note` text COLLATE utf8mb4_unicode_ci,
  `batch_id` bigint DEFAULT NULL COMMENT '来源批次；MANUAL 录入为 NULL',
  `source_type` enum('REAL','TEST','MANUAL') COLLATE utf8mb4_unicode_ci NOT NULL,
  `evidence_ref` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '= import_batch:{batch_id}，代码生成',
  `dedupe_state` enum('CLEAN','PENDING_REVIEW','MERGED') COLLATE utf8mb4_unicode_ci NOT NULL,
  `first_seen_at` datetime(6) NOT NULL DEFAULT (now()),
  `last_seen_at` datetime(6) NOT NULL DEFAULT (now()) COMMENT '命中即刷新：同一客户被导入命中就更新它',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_customers_batch_id` (`batch_id`),
  CONSTRAINT `customers_ibfk_1` FOREIGN KEY (`batch_id`) REFERENCES `import_batches` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci

$ show create table import_batches
CREATE TABLE `import_batches` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '原始文件名',
  `file_sha256` char(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '文件内容 sha256，用于 preview/commit 一致性与幂等',
  `status` enum('SUCCESS','PARTIAL','FAILED') COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_type` enum('REAL','TEST','MANUAL') COLLATE utf8mb4_unicode_ci NOT NULL,
  `rows_total` bigint NOT NULL,
  `rows_created` bigint NOT NULL,
  `rows_deduplicated` bigint NOT NULL,
  `rows_skipped` bigint NOT NULL,
  `skipped_reasons_json` json DEFAULT NULL,
  `skipped_columns_json` json DEFAULT NULL,
  `imported_at` datetime NOT NULL DEFAULT (now()),
  `evidence_ref` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '证据锚点，由代码生成为 import_batch:{id}',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
```

### ② preview：干净样本（只解析、不入库）

```
$ POST /api/imports/preview
HTTP 200
{
  "filename": "customers_sample_TEST_clean.csv",
  "file_sha256": "074de4b87f1b02690add5da775eaee571d51129a2886f65de75eae189c071809",
  "file_kind": "csv",
  "encoding": "utf-8",
  "encoding_note": null,
  "sheet_names": [],
  "sheet_used": null,
  "sheet_note": null,
  "header_row": 1,
  "ignored_leading_rows": 0,
  "headers": [
    "客户姓名",
    "联系电话",
    "邮箱",
    "公司名称",
    "地区",
    "备注"
  ],
  "rows_total": {
    "value": 8,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_preview:074de4b87f1b",
    "reason": null
  },
  "columns": [
    {
      "column": "客户姓名",
      "target": "name",
      "confidence": "auto"
    },
    {
      "column": "联系电话",
      "target": "phone",
      "confidence": "auto"
    },
    {
      "column": "邮箱",
      "target": "email",
      "confidence": "auto"
    },
    {
      "column": "公司名称",
      "target": "company_name",
      "confidence": "auto"
    },
    {
      "column": "地区",
      "target": "region",
      "confidence": "auto"
    },
    {
      "column": "备注",
      "target": "note",
      "confidence": "auto"
    }
  ],
  "unmapped_columns": [],
  "sample_rows": [
    {
      "客户姓名": "张伟",
      "联系电话": "13800138001",
      "邮箱": "zhangwei@example.com",
      "公司名称": "北京华信科技有限公司",
      "地区": "北京",
      "备注": "重点客户"
    },
    {
      "客户姓名": "李娜",
      "联系电话": "13800138002",
      "邮箱": "lina@example.com",
      "公司名称": "上海远洋贸易有限公司",
      "地区": "上海",
      "备注": "首次接触"
    },
    {
      "客户姓名": "王强",
      "联系电话": "13800138003",
      "邮箱": "wangqiang@example.com",
      "公司名称": "深圳创联电子有限公司",
      "地区": "深圳",
      "备注": "老客户转介绍"
    }
  ],
  "target_fields": [
    {
      "key": "name",
      "label": "客户姓名"
    },
    {
      "key": "company_name",
      "label": "公司名称"
    },
    {
      "key": "phone",
      "label": "联系电话"
    },
    {
      "key": "email",
      "label": "邮箱"
    },
    {
      "key": "region",
      "label": "地区"
    },
    {
      "key": "note",
      "label": "备注"
    }
  ]
}
```

### ③ 同一 sha256 重复提交 → 被拒绝（幂等）

```
$ POST /api/imports/commit
HTTP 409
{
  "error": "duplicate_import",
  "message": "该文件已导入过，batch_id=1",
  "batch_id": 1,
  "imported_at": "2026-10-03T21:20:45"
}
```

### ④ 提交的文件与预览不是同一份 → sha256 不一致被拒

```
$ POST /api/imports/commit
HTTP 400
{
  "error": "sha256_mismatch",
  "message": "上传文件与预览时的不是同一份（sha256 不一致），请重新预览后再提交",
  "expected": "074de4b87f1b02690add5da775eaee571d51129a2886f65de75eae189c071809",
  "actual": "3725698429eec4ed183b652b5b9a03cbe010109385ef3c48da0b58cec258e5f5"
}
```

### ⑤ 空文件 → empty_file

```
$ POST /api/imports/preview
HTTP 400
{
  "error": "empty_file",
  "message": "文件是空的（0 字节）"
}
```

### ⑥ 上传 .txt → unsupported_format

```
$ POST /api/imports/preview
HTTP 400
{
  "error": "unsupported_format",
  "message": "不支持的文件类型 .txt：本系统只接受 .csv / .xlsx"
}
```

### ⑦ 客户列表（分页裸值 + 业务数字走 EvidenceValue）

```
$ GET /api/customers?page=1&page_size=3
HTTP 200
{
  "items": [
    {
      "id": 12,
      "name": "周杰",
      "company_name": "武汉光谷软件有限公司",
      "phone": "13900139008",
      "email": "zhoujie2@example.com",
      "region": "武汉",
      "note": null,
      "batch_id": 2,
      "source_type": "TEST",
      "evidence_ref": "import_batch:2",
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T21:20:49.904453",
      "last_seen_at": "2026-10-03T21:20:49.904453",
      "created_at": "2026-10-03T21:20:49",
      "updated_at": "2026-10-03T21:20:49"
    },
    {
      "id": 11,
      "name": "王强",
      "company_name": "深圳创联电子有限公司",
      "phone": "13900139004",
      "email": null,
      "region": "深圳",
      "note": null,
      "batch_id": 2,
      "source_type": "TEST",
      "evidence_ref": "import_batch:2",
      "dedupe_state": "CLEAN",
      "first_seen_at": "2026-10-03T21:20:49.898415",
      "last_seen_at": "2026-10-03T21:20:49.898415",
      "created_at": "2026-10-03T21:20:49",
      "updated_at": "2026-10-03T21:20:49"
    },
    {
      "id": 10,
      "name": "李娜",
      "company_name": "上海远洋贸易有限公司",
      "phone": null,
      "email": null,
      "region": "上海",
      "note": null,
      "batch_id": 2,
      "source_type": "TEST",
      "evidence_ref": "import_batch:2",
      "dedupe_state": "PENDING_REVIEW",
      "first_seen_at": "2026-10-03T21:20:49.894368",
      "last_seen_at": "2026-10-03T21:20:49.894368",
      "created_at": "2026-10-03T21:20:49",
      "updated_at": "2026-10-03T21:20:49"
    }
  ],
  "page": 1,
  "page_size": 3,
  "total": {
    "value": 12,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count",
    "reason": null
  },
  "test_count": {
    "value": 12,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "customers:count|source_type=TEST",
    "reason": null
  }
}
```

### ⑧ 导入批次列表

```
$ GET /api/imports
HTTP 200
{
  "items": [
    {
      "batch_id": 2,
      "filename": "customers_sample_TEST_messy.csv",
      "file_sha256": "3725698429eec4ed183b652b5b9a03cbe010109385ef3c48da0b58cec258e5f5",
      "status": "PARTIAL",
      "source_type": "TEST",
      "evidence_ref": "import_batch:2",
      "rows_total": {
        "value": 9,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:2",
        "reason": null
      },
      "rows_created": {
        "value": 4,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:2",
        "reason": null
      },
      "rows_deduplicated": {
        "value": 1,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:2",
        "reason": null
      },
      "rows_skipped": {
        "value": 4,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:2",
        "reason": null
      },
      "imported_at": "2026-10-03T21:20:49"
    },
    {
      "batch_id": 1,
      "filename": "customers_sample_TEST_clean.csv",
      "file_sha256": "074de4b87f1b02690add5da775eaee571d51129a2886f65de75eae189c071809",
      "status": "SUCCESS",
      "source_type": "TEST",
      "evidence_ref": "import_batch:1",
      "rows_total": {
        "value": 8,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:1",
        "reason": null
      },
      "rows_created": {
        "value": 8,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:1",
        "reason": null
      },
      "rows_deduplicated": {
        "value": 0,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:1",
        "reason": null
      },
      "rows_skipped": {
        "value": 0,
        "state": "VALID",
        "source_type": "IMPORT",
        "evidence_ref": "import_batch:1",
        "reason": null
      },
      "imported_at": "2026-10-03T21:20:45"
    }
  ],
  "total": {
    "value": 2,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batches:count",
    "reason": null
  }
}
```

### ⑨ 批次详情（batch_id=2）

```
$ GET /api/imports/2
HTTP 200
{
  "batch_id": 2,
  "filename": "customers_sample_TEST_messy.csv",
  "file_sha256": "3725698429eec4ed183b652b5b9a03cbe010109385ef3c48da0b58cec258e5f5",
  "status": "PARTIAL",
  "source_type": "TEST",
  "evidence_ref": "import_batch:2",
  "rows_total": {
    "value": 9,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_created": {
    "value": 4,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_deduplicated": {
    "value": 1,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_skipped": {
    "value": 4,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "imported_at": "2026-10-03T21:20:49",
  "skipped_reasons": [
    {
      "row": 6,
      "reason": "missing_name",
      "raw": ""
    },
    {
      "row": 7,
      "reason": "invalid_phone",
      "raw": "abc-def"
    },
    {
      "row": 8,
      "reason": "invalid_email",
      "raw": "not-an-email"
    },
    {
      "row": 10,
      "reason": "empty_row",
      "raw": ""
    }
  ],
  "skipped_columns": [
    {
      "column": "备注信息XX",
      "reason": "unmapped_column"
    },
    {
      "column": "多余列A",
      "reason": "unmapped_column"
    }
  ],
  "customer_ids": [
    9,
    10,
    11,
    12
  ],
  "customers": [
    {
      "id": 9,
      "name": "张伟",
      "company_name": "北京华信科技有限公司",
      "phone": "13900139001",
      "email": "zhangwei2@example.com",
      "dedupe_state": "CLEAN",
      "source_type": "TEST",
      "evidence_ref": "import_batch:2"
    },
    {
      "id": 10,
      "name": "李娜",
      "company_name": "上海远洋贸易有限公司",
      "phone": null,
      "email": null,
      "dedupe_state": "PENDING_REVIEW",
      "source_type": "TEST",
      "evidence_ref": "import_batch:2"
    },
    {
      "id": 11,
      "name": "王强",
      "company_name": "深圳创联电子有限公司",
      "phone": "13900139004",
      "email": null,
      "dedupe_state": "CLEAN",
      "source_type": "TEST",
      "evidence_ref": "import_batch:2"
    },
    {
      "id": 12,
      "name": "周杰",
      "company_name": "武汉光谷软件有限公司",
      "phone": "13900139008",
      "email": "zhoujie2@example.com",
      "dedupe_state": "CLEAN",
      "source_type": "TEST",
      "evidence_ref": "import_batch:2"
    }
  ],
  "pending_review_customer_ids": [
    10
  ]
}
```


---

## 附：前端（真实浏览器）实际发出的调用

由 `scripts/ui_screenshots.mjs` 驱动真实 Edge 跑导入流程时，从浏览器网络层抓下的原始响应体（不是另写脚本伪造的）。

### POST /api/imports/preview → HTTP 200（customers_sample_TEST_clean.csv）

```json
{
  "filename": "customers_sample_TEST_clean.csv",
  "file_sha256": "074de4b87f1b02690add5da775eaee571d51129a2886f65de75eae189c071809",
  "file_kind": "csv",
  "encoding": "utf-8",
  "encoding_note": null,
  "sheet_names": [],
  "sheet_used": null,
  "sheet_note": null,
  "header_row": 1,
  "ignored_leading_rows": 0,
  "headers": [
    "客户姓名",
    "联系电话",
    "邮箱",
    "公司名称",
    "地区",
    "备注"
  ],
  "rows_total": {
    "value": 8,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_preview:074de4b87f1b",
    "reason": null
  },
  "columns": [
    {
      "column": "客户姓名",
      "target": "name",
      "confidence": "auto"
    },
    {
      "column": "联系电话",
      "target": "phone",
      "confidence": "auto"
    },
    {
      "column": "邮箱",
      "target": "email",
      "confidence": "auto"
    },
    {
      "column": "公司名称",
      "target": "company_name",
      "confidence": "auto"
    },
    {
      "column": "地区",
      "target": "region",
      "confidence": "auto"
    },
    {
      "column": "备注",
      "target": "note",
      "confidence": "auto"
    }
  ],
  "unmapped_columns": [],
  "sample_rows": [
    {
      "客户姓名": "张伟",
      "联系电话": "13800138001",
      "邮箱": "zhangwei@example.com",
      "公司名称": "北京华信科技有限公司",
      "地区": "北京",
      "备注": "重点客户"
    },
    {
      "客户姓名": "李娜",
      "联系电话": "13800138002",
      "邮箱": "lina@example.com",
      "公司名称": "上海远洋贸易有限公司",
      "地区": "上海",
      "备注": "首次接触"
    },
    {
      "客户姓名": "王强",
      "联系电话": "13800138003",
      "邮箱": "wangqiang@example.com",
      "公司名称": "深圳创联电子有限公司",
      "地区": "深圳",
      "备注": "老客户转介绍"
    }
  ],
  "target_fields": [
    {
      "key": "name",
      "label": "客户姓名"
    },
    {
      "key": "company_name",
      "label": "公司名称"
    },
    {
      "key": "phone",
      "label": "联系电话"
    },
    {
      "key": "email",
      "label": "邮箱"
    },
    {
      "key": "region",
      "label": "地区"
    },
    {
      "key": "note",
      "label": "备注"
    }
  ]
}
```

### POST /api/imports/commit → HTTP 200（customers_sample_TEST_clean.csv，批次 1，status=SUCCESS）

```json
{
  "batch_id": 1,
  "filename": "customers_sample_TEST_clean.csv",
  "file_sha256": "074de4b87f1b02690add5da775eaee571d51129a2886f65de75eae189c071809",
  "status": "SUCCESS",
  "source_type": "TEST",
  "evidence_ref": "import_batch:1",
  "rows_total": {
    "value": 8,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:1",
    "reason": null
  },
  "rows_created": {
    "value": 8,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:1",
    "reason": null
  },
  "rows_deduplicated": {
    "value": 0,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:1",
    "reason": null
  },
  "rows_skipped": {
    "value": 0,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:1",
    "reason": null
  },
  "skipped_reasons": [],
  "skipped_columns": [],
  "conflicts": [],
  "imported_at": "2026-10-03T21:20:45"
}
```

### POST /api/imports/preview → HTTP 200（customers_sample_TEST_messy.csv）

```json
{
  "filename": "customers_sample_TEST_messy.csv",
  "file_sha256": "3725698429eec4ed183b652b5b9a03cbe010109385ef3c48da0b58cec258e5f5",
  "file_kind": "csv",
  "encoding": "gb18030",
  "encoding_note": "按 GB18030/GBK 解码（文件不是 UTF-8，中文已正确还原）",
  "sheet_names": [],
  "sheet_used": null,
  "sheet_note": null,
  "header_row": 1,
  "ignored_leading_rows": 0,
  "headers": [
    "客户姓名",
    "联系电话",
    "邮箱",
    "公司名称",
    "地区",
    "备注信息XX",
    "多余列A"
  ],
  "rows_total": {
    "value": 9,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_preview:3725698429ee",
    "reason": null
  },
  "columns": [
    {
      "column": "客户姓名",
      "target": "name",
      "confidence": "auto"
    },
    {
      "column": "联系电话",
      "target": "phone",
      "confidence": "auto"
    },
    {
      "column": "邮箱",
      "target": "email",
      "confidence": "auto"
    },
    {
      "column": "公司名称",
      "target": "company_name",
      "confidence": "auto"
    },
    {
      "column": "地区",
      "target": "region",
      "confidence": "auto"
    },
    {
      "column": "备注信息XX",
      "target": null,
      "confidence": "unknown"
    },
    {
      "column": "多余列A",
      "target": null,
      "confidence": "unknown"
    }
  ],
  "unmapped_columns": [
    "备注信息XX",
    "多余列A"
  ],
  "sample_rows": [
    {
      "客户姓名": "张伟",
      "联系电话": "13900139001",
      "邮箱": "zhangwei2@example.com",
      "公司名称": "北京华信科技有限公司",
      "地区": "北京",
      "备注信息XX": "与下一行重复",
      "多余列A": "无用列1"
    },
    {
      "客户姓名": "张伟副本",
      "联系电话": "13900139001",
      "邮箱": "zhangwei2@example.com",
      "公司名称": "北京华信科技有限公司",
      "地区": "北京",
      "备注信息XX": "与上一行完全相同",
      "多余列A": "无用列2"
    },
    {
      "客户姓名": "李娜",
      "联系电话": "",
      "邮箱": "",
      "公司名称": "上海远洋贸易有限公司",
      "地区": "上海",
      "备注信息XX": "电话与邮箱都为空",
      "多余列A": "无用列3"
    }
  ],
  "target_fields": [
    {
      "key": "name",
      "label": "客户姓名"
    },
    {
      "key": "company_name",
      "label": "公司名称"
    },
    {
      "key": "phone",
      "label": "联系电话"
    },
    {
      "key": "email",
      "label": "邮箱"
    },
    {
      "key": "region",
      "label": "地区"
    },
    {
      "key": "note",
      "label": "备注"
    }
  ]
}
```

### POST /api/imports/commit → HTTP 200（customers_sample_TEST_messy.csv，批次 2，status=PARTIAL）

```json
{
  "batch_id": 2,
  "filename": "customers_sample_TEST_messy.csv",
  "file_sha256": "3725698429eec4ed183b652b5b9a03cbe010109385ef3c48da0b58cec258e5f5",
  "status": "PARTIAL",
  "source_type": "TEST",
  "evidence_ref": "import_batch:2",
  "rows_total": {
    "value": 9,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_created": {
    "value": 4,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_deduplicated": {
    "value": 1,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "rows_skipped": {
    "value": 4,
    "state": "VALID",
    "source_type": "IMPORT",
    "evidence_ref": "import_batch:2",
    "reason": null
  },
  "skipped_reasons": [
    {
      "row": 6,
      "reason": "missing_name",
      "raw": ""
    },
    {
      "row": 7,
      "reason": "invalid_phone",
      "raw": "abc-def"
    },
    {
      "row": 8,
      "reason": "invalid_email",
      "raw": "not-an-email"
    },
    {
      "row": 10,
      "reason": "empty_row",
      "raw": ""
    }
  ],
  "skipped_columns": [
    {
      "column": "备注信息XX",
      "reason": "unmapped_column"
    },
    {
      "column": "多余列A",
      "reason": "unmapped_column"
    }
  ],
  "conflicts": [
    {
      "customer_id": 10,
      "conflict_with": [],
      "kind": "no_match_key"
    }
  ],
  "imported_at": "2026-10-03T21:20:49"
}
```
