import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { CustomerItem, CustomerListResponse } from '../api/types'
import { DEDUPE_STATE_LABELS, SOURCE_TAG_COLORS } from '../api/types'
import EvidenceNumber from '../components/EvidenceNumber'
import ImportModal from '../components/ImportModal'

const { Text, Paragraph } = Typography

/** 来源 Tag：REAL / TEST / MANUAL 一眼可辨（TEST 必须是橙色，提醒这是测试数据） */
function SourceTag({ value }: { value: CustomerItem['source_type'] }) {
  return <Tag color={SOURCE_TAG_COLORS[value] ?? 'default'}>{value}</Tag>
}

export default function CustomersPage() {
  const [data, setData] = useState<CustomerListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [sourceType, setSourceType] = useState<string | undefined>(undefined)
  const [importOpen, setImportOpen] = useState(false)
  /** 导入完成后强制重新拉一次列表（改这个值就能让 useEffect 重跑） */
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (query) params.set('q', query)
      if (sourceType) params.set('source_type', sourceType)
      setData(await apiGet<CustomerListResponse>(`/api/customers?${params.toString()}`))
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, query, sourceType])

  // reloadKey 只是"导入完成后重新拉一次"的显式触发器
  useEffect(() => {
    void load()
  }, [load, reloadKey])

  /** 手动刷新：先亮 loading 再拉（走事件回调，不在 effect 里同步 setState） */
  const refresh = () => {
    setLoading(true)
    void load()
  }

  const items = data?.items ?? []
  const testCount = data?.test_count.value ?? 0
  const isEmpty = !loading && !error && items.length === 0

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {/* 顶部工具条：搜索 + 来源筛选 + 导入 */}
      <Card variant="outlined" styles={{ body: { padding: 16 } }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索姓名 / 公司 / 电话 / 邮箱"
              style={{ width: 300 }}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onSearch={(value) => {
                setQuery(value.trim())
                setPage(1)
              }}
            />
            <Select
              allowClear
              placeholder="全部来源"
              style={{ width: 140 }}
              value={sourceType}
              onChange={(value) => {
                setSourceType(value)
                setPage(1)
              }}
              options={[
                { value: 'REAL', label: 'REAL 真实' },
                { value: 'TEST', label: 'TEST 测试' },
                { value: 'MANUAL', label: 'MANUAL 手工' },
              ]}
            />
            <Button icon={<ReloadOutlined />} onClick={refresh}>
              刷新
            </Button>
          </Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
            导入 Excel/CSV
          </Button>
        </Space>
      </Card>

      {/* ★ 有 TEST 数据时的黄色提示（数字来自后端 EvidenceValue，前端不自己算） */}
      {testCount > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`当前包含 ${testCount} 条测试导入数据`}
          description="这些数据的来源是 TEST，不应计入业务汇报；确认不需要后可按 README 的一键清理脚本删除。"
        />
      )}

      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Card
        variant="outlined"
        styles={{ body: { padding: isEmpty ? 0 : 16 } }}
        title={
          isEmpty || !data ? null : (
            <Space size={8}>
              <span>客户列表</span>
              {/* 业务数字只走 EvidenceValue，前端不自己算（D4） */}
              <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                共 <EvidenceNumber evidence={data.total} /> 条
              </Text>
            </Space>
          )
        }
      >
        {/* ★ 空态：零数字、零假数据，只给引导 */}
        {isEmpty ? (
          <div style={{ padding: 48 }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={4}>
                  <Text>暂无客户数据</Text>
                  <Text type="secondary">导入 CSV / Excel 后，客户会显示在这里</Text>
                </Space>
              }
            >
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>
                导入 Excel/CSV
              </Button>
            </Empty>
          </div>
        ) : (
          <Table<CustomerItem>
            rowKey="id"
            size="middle"
            loading={loading}
            dataSource={items}
            scroll={{ x: 'max-content' }}
            pagination={{
              current: data?.page ?? page,
              pageSize: data?.page_size ?? pageSize,
              total: data?.total.value ?? 0,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
              onChange: (nextPage, nextSize) => {
                setPage(nextPage)
                setPageSize(nextSize)
              },
            }}
            columns={[
              { title: '客户姓名', dataIndex: 'name', fixed: 'left', width: 120 },
              { title: '公司名称', dataIndex: 'company_name', ellipsis: true },
              { title: '联系电话', dataIndex: 'phone', width: 140 },
              { title: '邮箱', dataIndex: 'email', ellipsis: true },
              { title: '地区', dataIndex: 'region', width: 90 },
              {
                title: '来源',
                dataIndex: 'source_type',
                width: 96,
                render: (value: CustomerItem['source_type']) => <SourceTag value={value} />,
              },
              {
                title: '去重状态',
                dataIndex: 'dedupe_state',
                width: 130,
                render: (value: CustomerItem['dedupe_state']) => (
                  <Tag color={value === 'CLEAN' ? 'default' : 'gold'}>
                    {DEDUPE_STATE_LABELS[value] ?? value}
                  </Tag>
                ),
              },
              {
                title: '来源批次',
                dataIndex: 'evidence_ref',
                width: 150,
                render: (ref: string | null, record) => (
                  <Tooltip title={ref ?? '无（手工录入）'}>
                    <Text type="secondary">{record.batch_id ? `#${record.batch_id}` : '—'}</Text>
                  </Tooltip>
                ),
              },
              {
                title: '最近命中',
                dataIndex: 'last_seen_at',
                width: 170,
                render: (value: string) => <Text type="secondary">{value.replace('T', ' ').slice(0, 19)}</Text>,
              },
              {
                title: '备注',
                dataIndex: 'note',
                width: 90,
                render: (note: string | null) =>
                  note ? <Tooltip title={note}>有</Tooltip> : <Text type="secondary">—</Text>,
              },
            ]}
          />
        )}
      </Card>

      {/* 导入入口的说明（放一行小字，避免用户以为导入会覆盖已有数据） */}
      {!isEmpty && (
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          <Badge status="processing" /> 导入只会新增或按 Email/电话命中去重，不会覆盖已有客户；
          同一份文件（sha256 相同）不允许重复导入。
        </Paragraph>
      )}

      <ImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => {
          setPage(1)
          setReloadKey((key) => key + 1)
        }}
      />
    </Space>
  )
}
