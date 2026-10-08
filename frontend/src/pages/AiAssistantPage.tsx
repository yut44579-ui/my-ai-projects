import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Row, Space, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { AiAssistantBlock, DashboardSummaryResponse } from '../api/types'
import AiAssistant from '../components/AiAssistant'

const { Text, Title } = Typography

/**
 * AI助手（独立页，需求 §八 主导航项）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【为什么这一页必须存在，而不是跳回工作台】（TASK-036）
 * ═══════════════════════════════════════════════════════════════
 *   此前侧栏的「AI助手」被我做成了 `Navigate to="/"` ——
 *   点进去看到的是工作台，**菜单说有独立功能，点进去却回到原地**。
 *   这是真实的体验缺陷（用户反馈："AI助手与工作台是一个页面怎么回事"）。
 *
 *   §八 把「AI助手」列为主导航项，那它就得是一页。
 *   现在这一页与工作台右栏**共用 `<AiAssistant>` 组件**：
 *   实现只有一份，但这里是整页形态（更宽、带常见问题、可滚动看长回答）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【能力清单与边界从哪来】
 * ═══════════════════════════════════════════════════════════════
 *   复用 `/api/dashboard/summary` 的 `ai_assistant` 块 ——
 *   与答复口径**共用同一份来源**（后端 `dashboard._ai_assistant()`）。
 *   ★ 不自己再写一份"能答什么"的文案：两份必然对不上（D46/D48 的教训）。
 */
export default function AiAssistantPage() {
  const [assistant, setAssistant] = useState<AiAssistantBlock | undefined>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const summary = await apiGet<DashboardSummaryResponse>('/api/dashboard/summary')
      setAssistant(summary.ai_assistant)
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

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {error && <Alert type="error" showIcon message="加载失败" description={error} />}

      <Row justify="space-between" align="middle">
        <Col>
          <Title level={5} style={{ margin: 0 }}>
            AI助手
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            基于系统里的真实业务数据回答；答不了的问题会如实说明，不编
          </Text>
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => setReloadKey((k) => k + 1)}>
            刷新
          </Button>
        </Col>
      </Row>

      <Card variant="outlined" loading={loading && !assistant}>
        <AiAssistant assistant={assistant} fullPage />
      </Card>
    </Space>
  )
}
