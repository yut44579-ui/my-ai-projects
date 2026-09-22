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
