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
