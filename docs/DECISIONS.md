# 决策记录（已冻结，勿改）

D1 复用策略：同约定最小重写；禁止抽公共包；禁止跨项目 import（sales-report-agent 只作参考）
D2 技术栈：MySQL8 + FastAPI + SQLAlchemy 2.x + Alembic + React+TS+Vite+AntD（见上）
D3 V1 单租户：不做 tenants；但 Repository 层保留 scope 参数作为迁移口
D4 数据契约：所有业务数字必须返回 EvidenceValue = {value, state, source_type, evidence_ref}
   state 三态：VALID（value 可为 0）/ NO_DATA（value=null）/ ERROR —— 三者绝不能混
   前端禁止自己算业务数字（不许 customers.length 显示成"客户数"）
D5 证据锚点：风险证据用 message_id 作为锚点，trigger_text 只作摘要
D6 汇报用生成时快照，不用实时重算；时区固定 Asia/Shanghai
D7 AI 安全拦截必须在代码层（Prompt 只负责表达，不负责安全）
   必须人工：报价/折扣/合同承诺/大额订单/付款/退款/银行账户/赔偿/法律承诺/
             重大投诉/特殊资源承诺/高价值客户关键承诺/异常资金
   「建议」≠「执行」："建议给 8 折"可作 SUGGESTION；"已经给你申请 8 折" 必须人工
D8 人工接管状态机：AUTO → HUMAN_REQUIRED → HUMAN_ACTIVE（不做工作流引擎）
D9 禁止伪造客户消息：站内没有真实客户渠道时，不许把客户没发过的消息写成 CUSTOMER
D10 AI 调用失败必须如实显示"AI 暂时无法回复，请人工处理"，禁止编造 AI 回复
D11 V1 数据模型冻结 7 表：users / customers / customer_messages / customer_events /
    risk_events / import_batches / reports（本 TASK 先不建，TASK-001 起逐步落）
