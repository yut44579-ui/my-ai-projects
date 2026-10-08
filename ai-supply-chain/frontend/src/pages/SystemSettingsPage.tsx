import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { SystemSettingsResponse } from '../api/types'

const { Text, Title } = Typography

/**
 * 系统设置（需求 §八 系统管理 → 系统设置）。
 *
 * ★ 定位：**运行信息只读展示**，不是配置表单。
 *   V1 的配置全部通过 `.env` 管理，页面上没有可编辑项。
 *   按 §二十六（不编造功能），**不做假的"保存"按钮** ——
 *   而是把真实运行值展示出来并标明来源，便于核对与排查。
 * ★ 所有密钥类配置只显示「是否已配置」，**不回显内容**。
 */
export default function SystemSettingsPage() {
  const [data, setData] = useState<SystemSettingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      setData(await apiGet<SystemSettingsResponse>('/api/settings/system'))
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, reloadKey])

  const missingDeps = (data?.dependencies ?? []).filter((d) => !d.configured)

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            系统设置
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前运行信息（只读）。配置通过 .env 管理，本页不提供在线修改
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
            刷新
          </Button>
        </Col>
      </Row>

      {data && <Alert type="info" showIcon message="为什么这一页不能改配置" description={data.note} />}

      {missingDeps.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`有 ${missingDeps.length} 项依赖未配置`}
          description={
            <Space orientation="vertical" size={4}>
              {missingDeps.map((d) => (
                <Text key={d.key} style={{ fontSize: 12.5 }}>
                  · {d.label}：{d.impact}
                </Text>
              ))}
            </Space>
          }
        />
      )}

      {/* 数据库 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <DatabaseOutlined />
            <span>数据库</span>
            {data?.database.connected ? (
              <Tag color="green">已连接</Tag>
            ) : (
              <Tag color="red">未连接</Tag>
            )}
          </Space>
        }
      >
        {data ? (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="类型">{data.database.dialect ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="版本">{data.database.version ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="主机">
              {data.database.host}:{data.database.port}
            </Descriptions.Item>
            <Descriptions.Item label="库名">{data.database.database}</Descriptions.Item>
            <Descriptions.Item label="账号">{data.database.user}</Descriptions.Item>
            <Descriptions.Item label="口令">
              {data.database.password_configured ? (
                <Tag color="green">已配置（不回显）</Tag>
              ) : (
                <Tag color="red">未配置</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="SQL 日志">{data.database.echo ? '开启' : '关闭'}</Descriptions.Item>
            <Descriptions.Item label="连接错误">
              {data.database.error ? (
                <Text type="danger" style={{ fontSize: 12 }}>
                  {data.database.error}
                </Text>
              ) : (
                '—'
              )}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Text type="secondary">{loading ? '加载中…' : '—'}</Text>
        )}
      </Card>

      {/* 应用信息 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <SettingOutlined />
            <span>应用</span>
          </Space>
        }
      >
        {data ? (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="名称">{data.app.name}</Descriptions.Item>
            <Descriptions.Item label="版本">{data.app.version}</Descriptions.Item>
            <Descriptions.Item label="运行环境">
              <Tag color={data.app.env === 'prod' ? 'green' : 'gold'}>{data.app.env}</Tag>
              {data.app.env !== 'prod' && (
                <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                  非生产环境
                </Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="时区">{data.app.timezone}</Descriptions.Item>
            <Descriptions.Item label="监听地址">
              {data.app.host}:{data.app.port}
            </Descriptions.Item>
            <Descriptions.Item label="接口前缀">{data.app.api_prefix}</Descriptions.Item>
          </Descriptions>
        ) : (
          <Text type="secondary">{loading ? '加载中…' : '—'}</Text>
        )}
      </Card>

      {/* 外部依赖 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <InfoCircleOutlined />
            <span>外部依赖</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              只显示是否已配置，不回显密钥内容
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.dependencies.length ?? 0) ? 0 : 24 } }}
      >
        {(data?.dependencies.length ?? 0) === 0 ? (
          <Text type="secondary">{loading ? '加载中…' : '—'}</Text>
        ) : (
          <Table
            rowKey="key"
            size="middle"
            pagination={false}
            dataSource={data?.dependencies ?? []}
            columns={[
              {
                title: '依赖',
                dataIndex: 'label',
                width: 200,
                render: (v: string) => <Text strong>{v}</Text>,
              },
              {
                title: '状态',
                dataIndex: 'configured',
                width: 110,
                render: (v: boolean) =>
                  v ? (
                    <Space size={4}>
                      <CheckCircleOutlined style={{ color: '#16A34A' }} />
                      <Text style={{ color: '#16A34A' }}>已配置</Text>
                    </Space>
                  ) : (
                    <Space size={4}>
                      <CloseCircleOutlined style={{ color: '#DC2626' }} />
                      <Text style={{ color: '#DC2626' }}>未配置</Text>
                    </Space>
                  ),
              },
              {
                title: '详情',
                dataIndex: 'detail',
                render: (v: string) => <Text style={{ fontSize: 12.5 }}>{v}</Text>,
              },
              {
                title: '未配置的影响',
                dataIndex: 'impact',
                render: (v: string) => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {v}
                  </Text>
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 数据量 */}
      <Card
        variant="outlined"
        title={
          <Space size={8}>
            <span>数据量</span>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>
              各表记录数（用于快速判断数据是否已导入）
            </Text>
          </Space>
        }
        styles={{ body: { padding: (data?.table_counts.length ?? 0) ? 12 : 24 } }}
      >
        {(data?.table_counts.length ?? 0) === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">加载中…</Text>} />
        ) : (
          <Row gutter={[16, 16]}>
            {data?.table_counts.map((t) => (
              <Col key={t.table} xs={12} sm={8} md={6} lg={4}>
                <Space orientation="vertical" size={2}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t.label}
                  </Text>
                  <Text strong style={{ fontSize: 18 }}>
                    {t.count === null ? '—' : t.count}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 10 }}>
                    {t.table}
                  </Text>
                </Space>
              </Col>
            ))}
          </Row>
        )}
      </Card>
    </Space>
  )
}
