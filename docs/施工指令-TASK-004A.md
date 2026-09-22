# 施工指令 · TASK-004A（★ 真前端最小垂直切片）

## 前置
002A（计算）/ 002B（渲染）/ 002C（API）均已通过。先读 `docs/TASKS.md` 确认。

## 目标（用户原话）
「先做一个前端，有交互能够实现功能的，**注意一定不是 demo index 那种类型的**」

**一条真链路**：浏览器上传**真 Excel** → 建任务 → 看到 Spec → 执行 → **下载真 Excel**。

## Scope
新增：
- `web/index.html`   —— **单页五区块**（① 数据 ② 建任务 ③ Report Spec ④ 执行结果 ⑤ 执行记录）
- `web/api.js`       —— 接口层（**所有请求都走真实后端**，集中在此文件）
- `web/app.js`       —— 视图逻辑
- `web/style.css`
修改：
- `app/api.py`（只加静态文件挂载 `app.mount("/", StaticFiles(directory="web", html=True))` 或等价做法）
- `docs/TASKS.md`（只改状态）

## 禁止（违反即 FAIL）
- ❌ **禁止 mock 数据 / 假 API / 假下载 / `setTimeout` 假装成功 / 硬编码结果**
- ❌ 禁止占位 TODO、禁止"未来再实现"
- ❌ 不引入 Vue/React（原生 HTML/CSS/JS，评审已确认）
- ❌ 不接 LLM（005）｜不改 002A/002B 的计算与渲染逻辑
- ❌ 不写巨型单文件（必须分 app.js / api.js / style.css）

## Acceptance Criteria
- AC-01 启动 `uvicorn app.api:app --port 8500` 后，`curl -s http://127.0.0.1:8500/ | head` 返回页面 HTML（含五个区块标题）
- AC-02 页面里的上传是真上传：JS `fetch('/api/upload', FormData)`，
        用真实文件 `data/Online Retail.xlsx` 实测，页面显示文件名 + 行数 541909（贴实测证据，可用 CDP/Playwright 驱动）
- AC-03 点「生成报表」真调 `/api/execute`，页面显示真实金额（与直接 curl 同一个 API 的值一致）
- AC-04 下载按钮真下载：`GET /api/download/{execution_id}` 得到文件，
        **用 openpyxl 打开该文件并读出数字**（贴读回值，证明不是空文件/假文件）
- AC-05 失败态可见：上传一个 `.txt` → 页面显示可读错误信息（不是静默失败、不是 alert 空字符串）
- AC-06 浏览器控制台**无 JS 报错**（贴证明）
- AC-07 `grep -rn "mock\|fake\|TODO\|hardcode\|setTimeout" web/` → 无假实现痕迹
- AC-08 git status 只含 Scope 内文件

## 报告格式
Status 只能是 COMPLETED / FAIL / BLOCKED；每条 AC 必须贴**实际命令输出或页面实测证据**
