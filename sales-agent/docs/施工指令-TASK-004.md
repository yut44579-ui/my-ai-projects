# 施工指令 · TASK-004（自然语言对话入口 —— 用户最关心的功能）

## 目标
用户能用**自然语言提问**，Agent 给出**基于真实数据**的回答。
页面上的「自然语言入口」（现在 disabled）→ **启用**。

## 链路（Gate 已定，必须严格遵守，不许自由发挥）
```
用户问题 → LLM Parser → Intent JSON（限定 schema）→ Schema 校验
        → 白名单 Tool Registry → 确定性计算（调既有 metrics/executor）
        → 事实结果 → LLM 只负责组织语言
```
**铁律：LLM 绝不负责算数字。** 所有数字必须来自 metrics/executor 的确定性计算，
LLM 只做「把用户的话解析成 Intent」和「把事实组织成人话」两件事。

## 第一版只做 3 个 Intent（不许加）
| Intent | 含义 | 计算方式 |
|---|---|---|
| `sales_summary` | 某时间段的销售额 / 订单数 / 客户数 | 调既有 `metrics` + `executor` |
| `sales_trend` | 按日或按周聚合的销售额趋势 | pandas groupby（程序算） |
| `top_products` | 产品销售排行 TOP N | 分组排序（程序算） |

## 回答格式（强制分区，前端要分区显示）
```
【发生了什么】  程序算出来的事实（数字必须来自确定性计算，逐位可核对）
【为什么】      标注为「推断」（LLM 可以推测，但必须标明是推断）
【建议行动】    LLM 生成
```

## LLM Provider
- **DeepSeek API**（用户已配好 key）
- key 从环境变量 `DEEPSEEK_API_KEY` 读（项目根目录已有 `.env`，用 `os.environ` 或 dotenv 读）
- **绝对不许把 key 写进代码或提交**
- LLM 超时/失败 → **明确报错**，不许编答案
- 无 key → 降级为「关键词匹配 + 明确告知用户未接 LLM」，**不许假装 LLM 在场**
- 参考：deepseek 模型是推理模型，`max_tokens` 要给足（≥2000），content 可能为空需读 `reasoning_content`

## 对话记录
- JSON 落盘 `state/conversations.json`（走已有 Repository 抽象，不要绕过）
- 前端刷新后能查到历史提问与回答

## 数据现实（务必诚实，这是 Gate 明确要求的）
- 数据是 UCI Online Retail：2010-12 ~ 2011-12，38 个国家，4372 客户，8 列
  （InvoiceNo / StockCode / Description / Quantity / InvoiceDate / UnitPrice / CustomerID / Country）
- **没有「区域」字段**（只有 Country / CustomerID）
  → 用户问「华南/华东/大区」时**必须明确告知数据不支持**，**不许用 Country 偷偷代替**
- 数据时间边界要如实说明（当前数据集末尾 2011-12-09）

## 前端（原生 JS，不引框架）
- 顶栏的「自然语言入口」输入框 → **启用**（现在 disabled）
- 提交后展示完整链路（可折叠）：
  用户问题 / 解析出的 Intent JSON / 实际调用的工具 / 事实结果表 / LLM 组织的回答
- 三态：loading（提交中）/ success / error（含「数据不支持」这类友好提示）
- 首页「AI 结论」卡（现在写着「TASK-004 接入」）→ 接入真实回答

## AC（我要逐条独立验收）
| # | 验收标准 |
|---|---|
| AC-01 | 真问真答：至少 3 个问题各命中一个 Intent，**数字与直接调用 metrics 的结果逐位一致** |
| AC-02 | Intent 解析失败 → 明确提示（不瞎猜、不乱算） |
| AC-03 | 问数据不支持的维度（如「华南区」）→ 明确告知不支持 |
| AC-04 | **LLM 不参与算数**（证据：回答里的数字 === metrics 输出） |
| AC-05 | 无 key / LLM 失败 → 降级且不编造 |
| AC-06 | 对话落盘 + 刷新后可查 |
| AC-07 | 前端可操作（Hermes 用本机 Edge CDP 实测） |
| AC-08 | 无 mock / 假数据 / 硬编码业务数字 / 假 ID |
| AC-09 | Legacy Contract：既有 12 端点语义 + metrics/executor/renderer/loader 零改动 |
| AC-10 | 全量 pytest 仍全绿（当前基准 121 passed） |

## 硬约束
- 不要动冻结资产（metrics/executor/renderer/loader）
- 前端保持原生 HTML/CSS/JS
- 不要顺手重构（发现重构点只记录）
- 遇到需要用户决策的事：写进报告「需决策」段，继续做别的，**不要停下等**

## 会话健康（重要，上次因此崩过）
- 大文件用 Grep + 定向 Read(offset/limit)，不要整篇读
- 长输出写进文件，不要贴在对话里
- 上下文控制在 1MB 以内

## 速度
- 先跑通再打磨；只跑相关测试文件
- 完成后按报告格式输出（Status / 做了什么 / 结果数字 / AC 逐条证据 / 遗留与需决策）
