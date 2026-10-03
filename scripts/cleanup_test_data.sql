-- TASK-001 §六：一键清理测试导入数据（source_type='TEST'）
--
-- 用途：用 tests/fixtures/customers_sample_TEST_*.csv 跑通链路后，把测试数据清干净，
--       避免它们混进后续的汇报数字（汇报类查询默认过滤 source_type='REAL'，见 docs/IMPORT_RULES.md）。
--
-- 用法：
--   mysql -u root -p biz_assistant < scripts/cleanup_test_data.sql
--   或（等价的管理命令，无需 mysql 客户端）：
--   .venv/Scripts/python.exe scripts/cleanup_test_data.py
--
-- ★ 顺序不能反：先删客户再删批次（customers.batch_id 是指向 import_batches 的外键）。

-- 看一眼将被删掉什么（可选，先跑这两条确认）
-- SELECT COUNT(*) AS test_customers FROM customers WHERE source_type = 'TEST';
-- SELECT COUNT(*) AS test_batches   FROM import_batches WHERE source_type = 'TEST';

START TRANSACTION;

-- 1) 删掉 TEST 批次带进来的客户
DELETE FROM customers
 WHERE source_type = 'TEST'
    OR batch_id IN (SELECT id FROM import_batches WHERE source_type = 'TEST');

-- 2) 删掉 TEST 批次本身（批次文件 sha256 记录随之消失，同一文件可以重新导入）
DELETE FROM import_batches WHERE source_type = 'TEST';

COMMIT;

-- 3) 核对：下面两条都应返回 0
SELECT COUNT(*) AS remaining_test_customers FROM customers WHERE source_type = 'TEST';
SELECT COUNT(*) AS remaining_test_batches   FROM import_batches WHERE source_type = 'TEST';
