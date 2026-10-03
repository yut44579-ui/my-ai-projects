import { RobotOutlined, SendOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Input, Segmented, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { AiReplyResponse, MessageItem, MessageTimelineResponse, MessageType } from '../api/types'
import { AI_STATUS_LABELS, SENDER_TYPE_LABELS } from '../api/types'

const { Text, Link } = Typography

/** 把后端时间戳显示成「YYYY-MM-DD HH:mm:ss」 */
function stamp(value: string): string {
  return value.replace('T', ' ').slice(0, 19)
}

/** 气泡配色：不同发送方一眼可辨（客户/AI 靠左，人工靠右，系统居中） */
const BUBBLE_STYLE: Record<string, { align: 'left' | 'right' | 'center'; bg: string; border: string }> = {
  CUSTOMER: { align: 'left', bg: '#f5f5f5', border: '#d9d9d9' },
  AI: { align: 'left', bg: '#e6f4ff', border: '#91caff' },
  HUMAN: { align: 'right', bg: '#f6ffed', border: '#b7eb8f' },
  SYSTEM: { align: 'center', bg: '#fafafa', border: '#f0f0f0' },
}

function Bubble({ message }: { message: MessageItem }) {
  const style = BUBBLE_STYLE[message.sender_type] ?? BUBBLE_STYLE.SYSTEM
  const isCenter = style.align === 'center'
  return (
    <div style={{ display: 'flex', justifyContent: isCenter ? 'center' : `flex-${style.align === 'left' ? 'start' : 'end'}` }}>
      <div
        style={{
          maxWidth: isCenter ? '100%' : '72%',
          background: style.bg,
          border: `1px solid ${style.border}`,
          borderRadius: 10,
          padding: '8px 12px',
        }}
      >
        <Space size={8} wrap style={{ marginBottom: 4 }}>
          <Tag color={message.sender_type === 'AI' ? 'blue' : message.sender_type === 'HUMAN' ? 'green' : 'default'}>
            {SENDER_TYPE_LABELS[message.sender_type]}
          </Tag>
          {message.message_type !== 'CHAT' && <Tag>{message.message_type}</Tag>}
          {message.ai_status !== 'NONE' && (
            <Tag color={message.ai_status === 'REPLIED' ? 'blue' : message.ai_status === 'FAILED' ? 'red' : 'gold'}>
              {AI_STATUS_LABELS[message.ai_status]}
            </Tag>
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {stamp(message.created_at)}
          </Text>
        </Space>
        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{message.content}</div>
        {/* 证据锚点：每一行都能点回接口自查（D5） */}
        <Tooltip title="证据锚点（后端生成，点击可在接口里查到这一条）">
          <Link href={`/api/customers/${message.customer_id}/messages`} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
            {message.evidence_ref}
          </Link>
        </Tooltip>
      </div>
    </div>
  )
}

/**
 * 客户详情页「沟通记录」区块：真时间线 + 底部人工回复输入框（TASK-003）。
 *
 * ★ 数字全部来自后端：条数只读 evidence.value，NO_DATA 时界面不显示任何数字（AC4）。
 * ★ 发送后**重新从后端拉一次**时间线，证明是落库的而不是前端态（AC2）。
 * ★ 人工发送固定 sender_type=HUMAN（写死，界面上没有"客户"这个选项 —— D9）。
 */
export default function MessageTimeline({
  customerId,
  onChanged,
}: {
  customerId: number
  /** 发送成功后通知详情页刷新（沟通记录条数要跟着后端变） */
  onChanged?: () => void
}) {
  const [data, setData] = useState<MessageTimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [kind, setKind] = useState<MessageType>('CHAT')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [aiBusy, setAiBusy] = useState(false)
  // AI 回复的提示：转人工（warning）/ 调用失败（error）—— 文案都来自后端
  const [aiNotice, setAiNotice] = useState<{ kind: 'warning' | 'error'; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const resp = await apiGet<MessageTimelineResponse>(`/api/customers/${customerId}/messages?page_size=100`)
      setData(resp)
      setError(null)
    } catch (err) {
      setError((err as { message?: string }).message ?? '沟通记录加载失败')
    } finally {
      setLoading(false)
    }
  }, [customerId])

  useEffect(() => {
    void load()
  }, [load])

  const send = async () => {
    const content = draft.trim()
    if (!content || sending) return
    setSending(true)
    setError(null)
    try {
      await apiPost<MessageItem>(`/api/customers/${customerId}/messages`, {
        content,
        sender_type: 'HUMAN', // ★ 写死：站内没有真实客户渠道，前端不发 CUSTOMER（D9）
        message_type: kind,
      })
      setDraft('')
      await load() // 重新向后端要数据，不是把刚发的塞进本地数组
      onChanged?.()
    } catch (err) {
      setError((err as { message?: string }).message ?? '发送失败')
    } finally {
      setSending(false)
    }
  }

  /** 把输入框里的内容当作"客户问题"交给 AI：敏感 → 后端转人工（不调 LLM）；失败 → 如实显示兜底文案 */
  const askAi = async () => {
    const question = draft.trim()
    if (!question || aiBusy) return
    setAiBusy(true)
    setError(null)
    setAiNotice(null)
    try {
      const resp = await apiPost<AiReplyResponse>(`/api/customers/${customerId}/ai-reply`, {
        content: question,
      })
      setDraft('')
      if (resp.ai_status === 'HUMAN_REQUIRED') {
        setAiNotice({ kind: 'warning', text: `已转人工处理：${resp.policy.reason}` })
      }
      await load()
      onChanged?.()
    } catch (err) {
      const e = err as { message?: string; status?: number }
      // ★ 后端 502 的 message 就是「AI 暂时无法回复，请人工处理」——原样显示，绝不自己编一句
      setAiNotice({
        kind: 'error',
        text: e.message ?? 'AI 暂时无法回复，请人工处理',
      })
      await load() // 失败时后端也落了一行 FAILED，刷新时间线把它显示出来
      onChanged?.()
    } finally {
      setAiBusy(false)
    }
  }

  const items = data?.items ?? []
  // 条数只认后端给的 EvidenceValue：NO_DATA 时显示「暂无沟通记录」，不显示 0
  const countText =
    data && data.total.state === 'VALID' ? `共 ${data.total.value} 条` : undefined

  return (
    <Card
      variant="outlined"
      title="沟通记录"
      extra={countText ? <Text type="secondary">{countText}</Text> : null}
      styles={{ body: { padding: 16 } }}
    >
      {loading ? (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<Text type="secondary">暂无沟通记录</Text>} />
      ) : (
        <Space orientation="vertical" size={10} style={{ width: '100%' }}>
          {items.map((item) => (
            <Bubble key={item.id} message={item} />
          ))}
        </Space>
      )}

      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
      {aiNotice && (
        <Alert type={aiNotice.kind} showIcon message={aiNotice.text} style={{ marginTop: 12 }} />
      )}

      {/* 底部输入框：人工回复（HUMAN）/ 交给 AI 回复（敏感问题后端会自动转人工，不调 LLM） */}
      <Space orientation="vertical" size={8} style={{ width: '100%', marginTop: 16 }}>
        <Input.TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="人工回复/记录；或把客户问题粘进来点「AI 回复」（以「人工」身份写入该客户的时间线）"
          autoSize={{ minRows: 2, maxRows: 6 }}
          maxLength={4000}
        />
        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
          <Segmented
            size="small"
            value={kind}
            onChange={(value) => setKind(value as MessageType)}
            options={[
              { label: '对话', value: 'CHAT' },
              { label: '备注', value: 'NOTE' },
            ]}
          />
          <Space>
            <Button
              icon={<RobotOutlined />}
              loading={aiBusy}
              disabled={!draft.trim() || sending}
              onClick={() => void askAi()}
            >
              AI 回复
            </Button>
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={sending}
              disabled={!draft.trim() || aiBusy}
              onClick={() => void send()}
            >
              发送
            </Button>
          </Space>
        </Space>
      </Space>
    </Card>
  )
}
