# 施工指令 · TASK-002（企业级 UI 骨架 + Repository 抽象准备）

## 前置必读（先读再动手）
1. `D:\sales-report-agent\docs\现状检查报告-升级前.md`（10 项现状）
2. `D:\GPT_Project_Reviews\reviews\006-upgrade-gate.md`（升级 Gate 评审全文）
3. `D:\GPT_Project_Reviews\decisions\004-upgrade-gate.md`（锁定的 4 条 + 本 TASK 的 AC）

## 新定位（一句话）
企业销售分析 Agent：用户丢入数据与业务文档 → 自然语言提问 → 销售/客户/产品分析 + 异常 + 行动计划 + 周报。
**架构原则：LLM 不算数字；数字一律由程序算。** 本 TASK 只做 **UI 骨架 + Repository 抽象**，不做分析与 LLM。

## ★ Legacy Contract Freeze（最高约束，违反即 FAIL）
- **不得修改**既有 12 个端点的请求/响应语义：
  `/api/health` `/api/upload` `/api/schema` `/api/execute` `/api/download/{id}` `/api/executions`
  `/api/tasks` `/api/tasks/{id}` `/api/tasks/{id}/run` `/api/tasks/{id}/runs`（+ 静态挂载）
- **不得修改** `app/engine/{metrics,executor,renderer,loader}.py` 的核心逻辑
- **不得修改** D16 销售额口径
- 需要改既有语义 → **停下报告**，不要自行改（要单独开架构变更 Gate）

## Scope

### A. Repository 抽象（后端，最小改动）
- 新增 `app/repositories/__init__.py`、`base.py`（抽象接口）、`json_repo.py`（当前 JSON 实现）
  - `TaskRepository` / `ExecutionRepository` / `UploadRepository` 三个接口
  - `JsonTaskRepository` / `JsonExecutionRepository` / `JsonUploadRepository` 实现（**行为与现在完全一致**，原子写 + 锁保留）
- `app/state.py` 改为**通过 Repository 访问** → **对外行为、文件格式、路径全部不变**
- **不做**数据库迁移（SQLite 留到 TASK-009）
- 新增测试：Repository 接口一致性测试（读写往返 + 与旧 state 行为等价）

### B. 企业级深色 UI 骨架（前端重做布局，功能不丢）
参考：企业内部业务分析平台 / 大型互联网公司内部工具风格。**深色主题**（深海军蓝/深灰背景，蓝色主强调，少量紫/绿/橙做状态），信息密度较高，卡片式，圆角，清晰层级；**不要**大量空白、大量渐变、玻璃拟态、无意义动画。

布局：
```
┌──────────────────────────────────────────────────────────────┐
│ 顶部：项目名 | 数据源 | 自然语言分析入口（本 TASK 可先占位） | 历史 | 设置 │
├────────┬────────────────────────────────┬────────────────────┤
│ 左侧   │  中间：核心分析工作区           │ 右侧：AI 结论 /     │
│ 导航   │  （按导航切换页面）             │  行动计划 / 系统状态 │
│ 总览   │                                │                    │
│ 销售分析│                               │                    │
│ 客户   │                                │                    │
│ 产品   │                                │                    │
│ 异常   │                                │                    │
│ 周报   │                                │                    │
│ ─────  │                                │                    │
│ 数据管理│                               │                    │
│ 系统设置│                               │                    │
│ 底部：系统状态、当前用户、当前权限       │                    │
└────────┴────────────────────────────────┴────────────────────┘
```

**必须保留旧功能可访问**（可收到「数据管理」或「总览」下）：上传真实 Excel → 建任务 → 看 Spec → 执行 → 下载。

每个页面**必须有 empty state**（不能只有标题）；必须体现 **loading / empty / success / error / disabled** 五种状态。

## Acceptance Criteria（Gate 给定，逐条贴实际证据）
- **AC-01** `/` 正常加载新深色 UI，**不存在静态 demo 数据**
- **AC-02** 浏览器真实显示三栏结构（左导航 | 中央工作区 | AI 结论区），响应窗口尺寸变化
- **AC-03** 页面**真实调用** `/api/health`、`/api/tasks`、`/api/executions`（贴 Network/响应证据，禁 mock）
- **AC-04** 左侧导航可切换：总览 / 销售分析 / 客户 / 产品 / 异常 / 周报；**每页有明确 empty state**
- **AC-05** 实际验证五种 UI 状态（loading / empty / success / error / disabled）
- **AC-06** 旧业务**不回归**：通过浏览器真实完成 旧 Excel → 上传 → 建任务 → 看 Spec → 执行 → 下载
- **AC-07** **刷新恢复**：完成一次真实任务后刷新浏览器 → 页面重新请求后端 → 能恢复任务/执行记录（**不依赖 JS 内存**）
- **AC-08** **无伪造**：代码审查确认无 mock 数据 / 无 fake API / 无硬编码业务金额 / 无假任务 ID / 无假执行状态 / 无 setTimeout 模拟业务完成
- **AC-09** **前后端边界**：确认本 TASK **没有修改** `metrics.py` / `executor.py` / `renderer` 核心逻辑 / 既有 API 请求响应语义
- **AC-10** **回归**：现有定向测试通过（api / task / execution / web 相关）+ Hermes 侧 CDP 真实浏览器验收
- **AC-11** `git status --short` 只出现本 TASK 允许修改的文件

## 禁止
- ❌ mock 数据 / 假 API / 假下载 / 硬编码业务金额 / 假任务 ID / 假执行状态 / setTimeout 假装完成 / 占位 TODO
- ❌ 引入 Vue/React 或任何前端框架（继续原生 HTML/CSS/JS）
- ❌ 修改既有 12 端点的语义、修改 metrics/executor/renderer 核心、为 UI 重写后端
- ❌ 引入新依赖（除必要：如 python-docx/pypdf 是 TASK-003 的事，本 TASK 不装）
- ❌ 顺手重构、扩大范围

## 速度要求
- 不必跑全量 pytest；只跑与 api / task / execution / web 相关的定向测试
- 先出可运行骨架，再补细节；不要过度打磨
- 遇到"需要用户决定"的事：**记录下来继续做**，不要停下等

## 报告格式
Status 只能是 COMPLETED / FAIL / BLOCKED；每条 AC 贴实际证据（命令输出 / Network 记录 / 页面截图）。