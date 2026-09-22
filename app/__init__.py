"""sales-report-agent 应用包。

分层约定（见 CLAUDE.md）：
    routers/       API 层（薄）
    services/      业务编排层
    repositories/  数据访问层（SQL 只在这里）
    ai/            LLM 调用（解析需求、写解读）
    engine/        ★ 取数 → 计算 → 渲染（纯代码、可单测）
    models/        数据模型

本 TASK（002A）只落地 engine/ 中的数据加载与口径计算部分。
"""
