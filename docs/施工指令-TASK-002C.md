# 施工指令 · TASK-002C（最小 API 闭环）

## 前置
002A（计算）+ 002B（渲染）已通过。

## 目标
把能力暴露成 HTTP 接口，**真实可用**（前端马上要接）。

## Scope
新增：
- `app/api.py` —— FastAPI 应用 + 路由：
  - `POST /api/upload`       上传 xlsx/csv，存到 `data/uploads/`，返回 {file_id, filename, rows, columns}
  - `GET  /api/schema`       数据字典（列名/类型/非空率/样例值）
  - `POST /api/execute`      传 {file_id, start, end, metrics:[...]} → 执行计算+渲染 → 返回 {execution_id, amount, rows_in_range, excel_path, seconds}
  - `GET  /api/download/{execution_id}`  返回真实 xlsx 文件（FileResponse）
  - `GET  /api/executions`   执行记录列表
  - `GET  /api/health`       健康检查
- `app/state.py` —— 执行记录（SQLite 或 json 落盘到 `state/executions.json`，含 spec/数据哈希/代码版本/execution_id/校验结果）
- `tests/test_api.py` —— 用 fastapi TestClient
修改：
- `docs/TASKS.md`（只改状态）

## 禁止
不做前端（004A）｜不接 LLM（005）｜不改 002A/002B 的计算与渲染逻辑｜不加数据库（用文件/ SQLite 即可）｜不引新依赖（fastapi/uvicorn 已装）

## Acceptance Criteria
- AC-01 pytest 全绿（含 002A/002B 测试）
- AC-02 启动服务（`uvicorn app.api:app --port 8500`），逐个 curl：
        /api/health → 200；/api/upload 真上传 data/Online Retail.xlsx → 返回真实行数 541909；
        /api/schema → 返回 8 个字段；/api/execute → 返回真实金额；/api/download → 下载到真实 xlsx（贴 curl 输出与文件大小）
- AC-03 下载的文件能被 openpyxl 打开且数字正确（贴读回值）
- AC-04 错误路径可见：上传非 xlsx → 4xx 且带可读错误信息；execute 用不存在的 file_id → 404（贴响应）
- AC-05 执行记录落盘，含 execution_id / 数据 SHA256 / 计算值 / 耗时
- AC-06 git status 只含 Scope 内文件

## 报告格式
Status 只能是 COMPLETED / FAIL / BLOCKED；含 curl 实测输出
