"""app/importer · FR-003：统一导入 / 物化 / 地区维度（一条管道，六种格式）。

════════════════════════════════════════════════════════════════════════
【包里各文件的分工（一次只读一个也能读懂）】
════════════════════════════════════════════════════════════════════════
    models.py        常量与形状：解析器版本、后缀→格式→落点、安全限额、错误码
    db.py            SQLite 的连接 / 表结构 / 事务（**没有一处聚合**）
    store.py         七种记录的读写（source_files / imports / datasets /
                     dataset_tables / dataset_columns / dataset_rows / documents）
    normalize.py     类型规范化（"100"/100/100.0/"£100.00" 收敛成同一个值）
    parsers.py       六种格式的解析（验身 → 分流 → 统一成 ParsedFile）
    regions.py       **地区维度识别**（字段名 + 非空率 + 唯一值 + 离散分类，四项一起判）
    pipeline.py      导入管道（幂等 → 验身 → 解析 → 物化 → 提交，一个事务）
    frames.py        把物化行取回来变成 DataFrame（计算层的入口，**不算数**）
    region_query.py  **按地区的确定性计算**（复用 engine/metrics 的 D16 口径）

【对外只用这几个入口】
    pipeline.import_bytes / import_file   导入
    pipeline.preview_file                 导入前预览
    region_query.sales_by_region          按地区算（无地区字段 → DIMENSION_UNAVAILABLE）
    db.init_schema                        启动时幂等建表
"""

from __future__ import annotations

__all__ = ["db", "frames", "models", "normalize", "parsers", "pipeline", "region_query", "regions", "store"]
