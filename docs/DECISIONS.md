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
    risk_events / import_batches / reports（TASK-001 已落 customers + import_batches 两张）

—— 以下为 TASK-001 落地时的补充决策（追加，不改上面已冻结的内容）——

D12 去重口径（TASK-001 §二 与 AC3c 冲突的处置）：
    §二 写"0 条候选 → CLEAN"，AC3c 要求"email/phone 都空的两行各自 PENDING_REVIEW"。
    两者只在"有没有可比的键"上有分歧。取 AC 口径并细分：
      有键、查不到 → CLEAN（确实是新客户）；压根没有键可比 → PENDING_REVIEW（无法判重，交人工）。
    「空值绝不参与匹配」这条不动：空值根本不构造查询条件。

D13 文件级失败不落 import_batches 行（"零入库"的最严解释）：
    空文件/格式不支持/超限/解析失败等一律 400 + 错误码，不留半截批次记录，
    因此同一份文件修好后可以重传，不会被自己的幂等检查挡住。FAILED 枚举保留在类型里。

D14 TASK-001 只新增两个依赖，均有不可替代的理由（硬边界：不引重型依赖）：
    · openpyxl —— xlsx 是 zip+XML 容器，标准库无解析器；用成熟库不自己造轮子。
    · python-multipart —— FastAPI 接收 multipart 文件上传的必需依赖。
    ★ 刻意不引 pandas：CSV 用标准库 csv、单元格统一 str 化，
      从根上杜绝"自动类型推断把电话读成科学计数/丢前导零"。

D15 跳过原因的层级：missing_name / invalid_phone / invalid_email / empty_row 用于**行级**
    （skipped_reasons_json）；unmapped_column 用于**列级**（skipped_columns_json）。
    两者共用同一个冻结枚举，不新增自由文本。

D16 customers.first_seen_at / last_seen_at 在 MySQL 上用 DATETIME(6)：
    DATETIME 默认只到秒，"命中即刷新 last_seen_at"在同一秒内无法观测，语义会被吃掉。
