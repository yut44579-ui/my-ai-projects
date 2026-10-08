import {
  BulbOutlined,
  CheckOutlined,
  CloseOutlined,
  FileSearchOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type {
  ProspectResearchDetail,
  ProspectResearchItem,
  ResearchCreateResponse,
  ResearchExtractResponse,
  ResearchListResponse,
  ResearchSummaryResponse,
} from '../api/types'
import { AiLabel } from '../components/AiDraftButton'
import EvidenceNumber from '../components/EvidenceNumber'

const { Text, Title } = Typography

const stamp = (v: string | null | undefined) =>
  v ? String(v).replace('T', ' ').slice(0, 19) : '—'

const SOURCE_OPTIONS = [
  { value: 'COMPANY_SITE', label: '公司官网' },
  { value: 'NEWS', label: '新闻' },
  { value: 'JOB_POSTING', label: '招聘信息' },
  { value: 'INDUSTRY_SITE', label: '行业网站' },
  { value: 'PRODUCT_INFO', label: '公开产品信息' },
  { value: 'OTHER', label: '其他' },
]

const STATUS_COLORS: Record<string, string> = {
  NONE: 'default',
  GENERATED: 'gold',
  CONFIRMED: 'green',
  REJECTED: 'red',
}

export default function ResearchesPage() {
  const [list, setList] = useState<ResearchListResponse | null>(null)
  const [summary, setSummary] = useState<ResearchSummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [filter, setFilter] = useState('ALL')

  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const [detail, setDetail] = useState<ProspectResearchDetail | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [accepted, setAccepted] = useState<number[]>([0])

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (filter !== 'ALL') params.set('draft_status', filter)
      const [l, s] = await Promise.all([
        apiGet<ResearchListResponse>(`/api/researches?${params.toString()}`),
        apiGet<ResearchSummaryResponse>('/api/researches/summary'),
      ])
      setList(l)
      setSummary(s)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const openDetail = async (id: number) => {
    setDetailOpen(true)
    try {
      setDetail(await apiGet<ProspectResearchDetail>(`/api/researches/${id}`))
      setAccepted([0])
    } catch (err) {
      message.error((err as { message?: string }).message ?? '读取失败')
    }
  }

  const submitCreate = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      const created = await apiPost<ResearchCreateResponse>('/api/researches', {
        target_type: v.target_type,
        target_name: v.target_name,
        source_type: v.source_type,
        source_url: v.source_url ?? null,
        source_text: v.source_text,
        focus: v.focus || null,
      })
      message.success('已创建研究；请点「AI 抽取」从原文提取事实')
      setCreateOpen(false)
      form.resetFields()
      setReloadKey((k) => k + 1)
      await openDetail(created.id)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const runExtract = async () => {
    if (!detail) return
    setBusy(true)
    try {
      const res = await apiPost<ResearchExtractResponse>(
        `/api/researches/${detail.id}/extract`,
        {},
      )
      setDetail(res.research)
      message.success(
        `抽取完成：事实 ${res.facts_count} 条、推断 ${res.inferences_count} 条` +
          (res.rejected_count > 0 ? `；★ 剔除 ${res.rejected_count} 条引用无法核对的条目` : ''),
      )
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '抽取失败')
    } finally {
      setBusy(false)
    }
  }

  const runDrafts = async () => {
    if (!detail) return
    setBusy(true)
    try {
      const res = await apiPost<{ research: ProspectResearchDetail; drafts_count: number }>(
        `/api/researches/${detail.id}/drafts`,
        {},
      )
      setDetail(res.research)
      setAccepted([0])
      message.success(`已生成 ${res.drafts_count} 条话术候选，等待你确认`)
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setBusy(false)
    }
  }

  const runConfirm = async () => {
    if (!detail) return
    setBusy(true)
    try {
      const res = await apiPost<ProspectResearchDetail>(
        `/api/researches/${detail.id}/confirm`,
        { accepted_indexes: accepted },
      )
      setDetail(res)
      message.success('已确认；系统不会自动发送，请自行人工触达')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '确认失败')
    } finally {
      setBusy(false)
    }
  }

  const runReject = async () => {
    if (!detail) return
    setBusy(true)
    try {
      const res = await apiPost<ProspectResearchDetail>(
        `/api/researches/${detail.id}/reject`,
        { reason: '人工判定不可用' },
      )
      setDetail(res)
      message.info('已驳回')
      setReloadKey((k) => k + 1)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '驳回失败')
    } finally {
      setBusy(false)
    }
  }

  const cards = [
    { title: '研究总数', icon: <FileSearchOutlined />, color: '#2563EB', evidence: summary?.total },
    {
      title: '待人工确认',
      icon: <RobotOutlined />,
      color: '#F59E0B',
      evidence: summary?.pending_confirm_total,
    },
    {
      title: '已确认话术',
      icon: <CheckOutlined />,
      color: '#16A34A',
      evidence: summary?.confirmed_total,
    },
    {
      title: '已驳回',
      icon: <CloseOutlined />,
      color: '#DC2626',
      evidence: summary?.rejected_total,
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            获客研究
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            你提供公开资料原文 → AI 只从原文抽事实（引用逐条核对）→ 生成话术 → 你确认后自行触达
          </Text>
        </Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建研究
            </Button>
          </Space>
        </Col>
      </Row>

      {summary && (
        <Alert
          type="info"
          showIcon
          icon={<SafetyCertificateOutlined />}
          message="事实与推断是分开的，而且事实的引用会被逐条回验"
          description={summary.note}
        />
      )}

      <Row gutter={16}>
        {cards.map((c) => (
          <Col key={c.title} xs={24} sm={12} xl={6}>
            <Card variant="outlined" styles={{ body: { padding: '16px 18px' } }}>
              <Space size={10} style={{ marginBottom: 8 }}>
                <span
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: '50%',
                    background: `${c.color}14`,
                    color: c.color,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {c.icon}
                </span>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  {c.title}
                </Text>
              </Space>
              <div style={{ fontSize: 24, fontWeight: 600 }}>
                {c.evidence ? <EvidenceNumber evidence={c.evidence} /> : <Text type="secondary">—</Text>}
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      <Card
        variant="outlined"
        title={
          <Space size={10}>
            <span>研究清单</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              {list?.total.state === 'VALID' ? `${list.total.value} 条` : '暂无'}
            </Text>
          </Space>
        }
        extra={
          <Segmented
            size="small"
            options={[
              { label: '全部', value: 'ALL' },
              { label: '未生成', value: 'NONE' },
              { label: '待确认', value: 'GENERATED' },
              { label: '已确认', value: 'CONFIRMED' },
              { label: '已驳回', value: 'REJECTED' },
            ]}
            value={filter}
            onChange={(v) => setFilter(String(v))}
          />
        }
        styles={{ body: { padding: (list?.items.length ?? 0) ? 0 : 24 } }}
      >
        {(list?.items.length ?? 0) === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={4}>
                <Text>{loading ? '加载中…' : '还没有做任何线索研究'}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点「新建研究」粘贴一段公开资料原文（官网介绍 / 招聘 JD / 行业报道均可）
                </Text>
              </Space>
            }
          />
        ) : (
          <Table<ProspectResearchItem>
            rowKey="id"
            size="middle"
            loading={loading}
            pagination={false}
            dataSource={list?.items ?? []}
            columns={[
              {
                title: '研究对象',
                dataIndex: 'target_name',
                render: (v: string, r) => (
                  <Space orientation="vertical" size={0}>
                    <Text>{v}</Text>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {r.source_type_label}｜原文 {r.source_length} 字
                    </Text>
                  </Space>
                ),
              },
              {
                title: '事实 / 推断',
                width: 130,
                render: (_: unknown, r) => (
                  <Space size={4}>
                    <Tooltip title="公开事实（引用已逐字核对）">
                      <Tag color="blue">事实 {r.facts.length}</Tag>
                    </Tooltip>
                    <Tooltip title="AI 推断（已标注，不等于事实）">
                      <Tag color="purple">推断 {r.inferences.length}</Tag>
                    </Tooltip>
                  </Space>
                ),
              },
              {
                title: '话术状态',
                dataIndex: 'draft_status_label',
                width: 120,
                render: (v: string, r) => <Tag color={STATUS_COLORS[r.draft_status]}>{v}</Tag>,
              },
              {
                title: '确认人',
                dataIndex: 'confirmed_by',
                width: 90,
                render: (v: string | null) => v ?? '—',
              },
              {
                title: '创建时间',
                dataIndex: 'created_at',
                width: 150,
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {stamp(v)}
                  </Text>
                ),
              },
              {
                title: '操作',
                key: 'action',
                width: 90,
                render: (_: unknown, r) => (
                  <Button size="small" type="link" onClick={() => void openDetail(r.id)}>
                    查看
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 新建研究 */}
      <Modal
        title="新建线索研究"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        width={680}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="target_type" label="对象类型" initialValue="PROSPECT">
                <Select
                  options={[
                    { value: 'PROSPECT', label: '潜在客户' },
                    { value: 'CUSTOMER', label: '已有客户' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={16}>
              <Form.Item
                name="target_name"
                label="研究对象名称"
                rules={[{ required: true, message: '填公司名或人名' }]}
              >
                <Input placeholder="如：深圳前海智算科技有限公司" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="source_type" label="资料类型" initialValue="COMPANY_SITE">
                <Select options={SOURCE_OPTIONS} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="source_url" label="来源链接（仅记录，系统不会访问它）">
                <Input placeholder="https://..." />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="focus"
            label={
              <AiLabel
                text="研究要点（选填）"
                purpose="RESEARCH_FOCUS"
                context={{ target_name: form.getFieldValue('target_name') }}
                onGenerated={(t) => form.setFieldValue('focus', t)}
                guard={() => {
                  if (!form.getFieldValue('target_name')) {
                    message.warning('先把「研究对象名称」填上，AI 需要知道研究谁')
                    return false
                  }
                  return true
                }}
              />
            }
            extra="这次想弄清什么问题。★ AI 只列「要查证的问题」，不下结论 —— 结论必须由下面的真实原文支撑"
          >
            <Input.TextArea
              rows={3}
              placeholder="可手写，也可点 AI 生成。如：主营业务；公开产品线；公开招聘方向"
            />
          </Form.Item>

          <Form.Item
            name="source_text"
            label={
              <AiLabel
                text="公开资料原文"
                excludedWhy="原文是这条链的证据来源：AI 只从你贴的真实原文里抽事实，且每条引用会被逐字回验。如果原文由 AI 生成，回验就变成自己验证自己编的东西 —— 所以这里刻意没有 AI 生成。"
              />
            }
            rules={[
              { required: true, message: '必须粘贴原文' },
              { min: 20, message: '原文至少 20 字，否则无法验证抽取出的事实' },
            ]}
            extra="★ 必须粘贴真实原文：AI 只能从原文抽事实，且每条事实的引用会被逐字回验（原文里找不到的一律剔除）"
          >
            <Input.TextArea rows={8} placeholder="把公司官网介绍 / 招聘 JD / 行业报道的原文粘贴到这里" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 研究详情 */}
      <Drawer
        title={detail ? `线索研究：${detail.target_name}` : '线索研究'}
        width={860}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detail && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="资料类型">{detail.source_type_label}</Descriptions.Item>
              <Descriptions.Item label="原文长度">{detail.source_length} 字</Descriptions.Item>
              <Descriptions.Item label="来源链接" span={2}>
                {detail.source_url ?? '—'}
                <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
                  （仅记录，系统不会访问）
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="话术状态" span={2}>
                <Tag color={STATUS_COLORS[detail.draft_status]}>{detail.draft_status_label}</Tag>
                {detail.confirmed_by && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    确认人 {detail.confirmed_by}｜{stamp(detail.confirmed_at)}
                  </Text>
                )}
                {detail.reject_reason && (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    驳回原因：{detail.reject_reason}
                  </Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            <Space>
              <Button type="primary" loading={busy} onClick={() => void runExtract()}>
                ① AI 抽取事实与推断
              </Button>
              <Button loading={busy} onClick={() => void runDrafts()} disabled={!detail.facts.length}>
                ② 生成话术候选
              </Button>
              {detail.draft_status === 'GENERATED' && (
                <>
                  <Button type="primary" ghost loading={busy} onClick={() => void runConfirm()}>
                    ③ 确认采纳
                  </Button>
                  <Button danger loading={busy} onClick={() => void runReject()}>
                    驳回
                  </Button>
                </>
              )}
            </Space>

            {detail.llm_error && (
              <Alert type="warning" showIcon message={`AI 不可用：${detail.llm_error}`} />
            )}

            {/* ★ 事实与推断分成两块展示，视觉上也不混 */}
            <Row gutter={16}>
              <Col span={12}>
                <Card
                  size="small"
                  variant="outlined"
                  title={
                    <Space size={6}>
                      <Tag color="blue">公开事实</Tag>
                      <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                        {detail.facts.length} 条 · 引用已逐字核对
                      </Text>
                    </Space>
                  }
                  styles={{ body: { padding: detail.facts.length ? 12 : 24, maxHeight: 380, overflow: 'auto' } }}
                >
                  {detail.facts.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      还没有抽取。点上面的「AI 抽取事实与推断」。
                    </Text>
                  ) : (
                    <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                      {detail.facts.map((f, i) => (
                        <div key={i}>
                          <Text style={{ fontSize: 13 }}>{f.statement}</Text>
                          <div>
                            <Tooltip title="这是原文里的逐字片段，服务端已回验它真实存在">
                              <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
                                原文：{f.quote}
                              </Text>
                            </Tooltip>
                          </div>
                        </div>
                      ))}
                    </Space>
                  )}
                </Card>
              </Col>
              <Col span={12}>
                <Card
                  size="small"
                  variant="outlined"
                  title={
                    <Space size={6}>
                      <Tag color="purple">AI 推断</Tag>
                      <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                        {detail.inferences.length} 条 · 不等于事实
                      </Text>
                    </Space>
                  }
                  styles={{ body: { padding: detail.inferences.length ? 12 : 24, maxHeight: 380, overflow: 'auto' } }}
                >
                  {detail.inferences.length === 0 ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      暂无推断。
                    </Text>
                  ) : (
                    <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                      {detail.inferences.map((it, i) => (
                        <div key={i}>
                          <Text style={{ fontSize: 13 }}>{it.statement}</Text>
                          <div>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              依据：{it.basis}
                            </Text>
                          </div>
                        </div>
                      ))}
                    </Space>
                  )}
                </Card>
              </Col>
            </Row>

            {detail.summary && (
              <Alert type="success" showIcon message="资料摘要" description={detail.summary} />
            )}

            {/* 话术候选 */}
            {detail.drafts.length > 0 && (
              <Card
                size="small"
                variant="outlined"
                title={
                  <Space size={6}>
                    <BulbOutlined />
                    <span>话术候选</span>
                    <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
                      {detail.draft_status === 'CONFIRMED'
                        ? '已确认（系统不会自动发送，请自行触达）'
                        : '请勾选要采纳的，再点「确认采纳」'}
                    </Text>
                  </Space>
                }
              >
                <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                  {detail.drafts.map((d, i) => (
                    <Card key={i} size="small" variant="outlined" styles={{ body: { padding: 12 } }}>
                      <Space size={8} style={{ marginBottom: 6 }}>
                        {detail.draft_status === 'GENERATED' && (
                          <input
                            type="checkbox"
                            checked={accepted.includes(i)}
                            onChange={(e) =>
                              setAccepted((prev) =>
                                e.target.checked ? [...prev, i] : prev.filter((x) => x !== i),
                              )
                            }
                          />
                        )}
                        <Tag color="cyan">{d.channel}</Tag>
                      </Space>
                      <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{d.content}</div>
                      {d.note && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          内部备注：{d.note}
                        </Text>
                      )}
                    </Card>
                  ))}
                </Space>
              </Card>
            )}

            {/* ★ TASK-042：研究要点单独一块，**不与原文混在一起** ——
                要点是"要查证的问题"，原文是"证据"。
                混在一起会让人把 AI 起草的提问当成已核实的资料。 */}
            {detail.focus && (
              <Card
                size="small"
                variant="outlined"
                title={
                  <Space size={6}>
                    <span>研究要点</span>
                    <Text type="secondary" style={{ fontWeight: 400, fontSize: 11 }}>
                      要查证的问题（可由 AI 起草，不是证据）
                    </Text>
                  </Space>
                }
              >
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, color: '#4B5563' }}>
                  {detail.focus}
                </div>
              </Card>
            )}

            <Collapse
              size="small"
              items={[
                {
                  key: 'src',
                  label: `查看原文（${detail.source_length} 字，事实引用的核对依据）`,
                  children: (
                    <div style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, color: '#4B5563' }}>
                      {detail.source_text}
                    </div>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </Space>
  )
}
