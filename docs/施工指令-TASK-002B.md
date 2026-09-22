# 施工指令 · TASK-002B（Report Spec + Excel 渲染）

## 前置
TASK-002A 已通过（数据加载 + 固定指标计算 + 独立 Oracle 校验）。先读 `docs/TASKS.md` 确认 002A 已完成。

## 目标
把「计算出来的数字」写进 Excel 报表，**保留参考模板的样式**（openpyxl 原地改）。

## Scope
新增：
- `app/spec/__init__.py`
- `app/spec/models.py` —— Report Spec（pydantic v2）：spec_id / name / version / data_source / time_range / metrics(受限 DSL) / group_by / output(template, sheet, cells) / created_at；支持 to_dict / from_dict / version 递增
- `app/engine/renderer.py` —— openpyxl 打开模板 → 只改数据单元格 → 另存到 outputs/
- `templates/weekly_sales_template.xlsx` —— 用代码生成一份基础模板（标题 / 表头 / 指标行 / 样式），不得用 pandas.to_excel 重建
- `tests/test_renderer.py`
修改：
- `docs/TASKS.md`（只改状态）

## 禁止
不做 API（002C）｜不做前端（004A）｜不接 LLM（005）｜不引新依赖｜不改 DECISIONS.md｜不重构 002A 的代码（如需扩展只加不改签名）

## Acceptance Criteria
- AC-01 pytest 全绿（含 002A 的测试，不得破坏）
- AC-02 生成的 xlsx 能被 openpyxl 重新读回，数字单元格 == executor 的计算值
- AC-03 模板样式保留：读回后检查字体/数字格式/列宽至少各一项与模板一致（贴断言与实测值）
- AC-04 同一 Spec 跑两次，输出文件的**数字部分完全一致**（可重复性）
- AC-05 文件落到 `outputs/`，打印路径 + 大小
- AC-06 git status 只含 Scope 内文件

## 报告格式
Status 只能是 COMPLETED / FAIL / BLOCKED；含 Changed Files / AC 逐条证据 / Gate Results / Out-of-Scope / Tests Modified
