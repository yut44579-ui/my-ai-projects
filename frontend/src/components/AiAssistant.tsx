import {
  AreaChartOutlined,
  LoadingOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons'
import { Alert, Avatar, Card, Input, Space, Tag, Tooltip, Typography } from 'antd'
import { useCallback, useState } from 'react'
import { apiPost } from '../api/client'
import type { AiAskResponse, AiAssistantBlock } from '../api/types'

const { Text: AntText } = Typography

export type AiAssistantProps = {
  /**
   * 助手配置（标题/徽标/问候语/能力清单/边界声明），来自 `/api/dashboard/summary`
   * 的 `ai_assistant` 块。
   * ★ 与问答接口共用同一份来源：能力清单里写了什么，问答就只答什么 ——
   *   两边各写一份必然对不上（D46/D48 的教训）。
   */
  assistant?: AiAssistantBlock
  /** 整页模式：卡片去掉标题栏与高度限制，问题区更宽，并显示常见问题 */
  fullPage?: boolean
}

/**
 * AI 助手（工作台右栏与「AI助手」独立页共用同一份实现）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【为什么要抽出来】（TASK-036）
 * ═══════════════════════════════════════════════════════════════
 *   此前 AI 助手只存在于工作台右栏，而侧栏的「AI助手」菜单
 *   被我做成了**重定向回工作台** —— 用户点进去发现"还是工作台那一页"，
 *   这是真实体验问题：菜单说有独立功能，点进去却回到原地。
 *
 *   现在抽成组件，**两处共享同一份实现**：
 *     · 工作台右栏（`fullPage=false`）：保留原有的窄栏形态
 *     · `/ai-assistant` 独立页（`fullPage=true`）：整页形态
 *   ★ 不复制两份代码：复制的话，将来改一处忘一处，
 *     就会出现"工作台能问的问题、AI助手页问不了"这种诡异差异。
 */
export default function AiAssistant({ assistant, fullPage = false }: AiAssistantProps) {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<AiAskResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ask = useCallback(
    async (override?: string) => {
      const q = (override ?? question).trim()
      if (!q || loading) return
      setLoading(true)
      setError(null)
      try {
        setResult(await apiPost<AiAskResponse>('/api/dashboard/ai-ask', { question: q }))
        setQuestion(q)
      } catch (err) {
        setError((err as { message?: string }).message ?? 'AI 助手暂时不可用')
      } finally {
        setLoading(false)
      }
    },
    [question, loading],
  )

  const capabilities = assistant?.capabilities ?? []
  const boundaries = assistant?.boundaries ?? []

  // ── 常见问题：只放"能力清单里明确支持的"，不放它答不了的 ──
  const suggestions = [
    '今天有哪些客户需要我处理？',
    '林芳什么情况？',
    '最近有哪些风险需要处理？',
    '这个月客户开发情况怎么样？',
  ]

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <Space size={8}>
        <Avatar size={24} style={{ background: '#2563EB' }} icon={<RobotOutlined />} />
        <AntText strong style={{ fontSize: 14 }}>
          {assistant?.title ?? 'AI 助手'}
        </AntText>
        <Tag color="blue" style={{ marginInlineEnd: 0 }}>
          {assistant?.badge ?? '企业版'}
        </Tag>
      </Space>
    </div>
  )

  const body = (
    <>
      <div style={{ padding: fullPage ? '4px 0 0' : '14px 18px' }}>
        <AntText style={{ fontSize: 13 }}>
          {assistant?.greeting ?? '你好！我是你的 AI 商业项目助理'}
        </AntText>

        {/* 能力清单 */}
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {capabilities.map((cap) => (
            <div key={cap.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <span
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  background: '#EFF6FF',
                  color: '#2563EB',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  flex: '0 0 auto',
                  marginTop: 2,
                }}
              >
                <AreaChartOutlined />
              </span>
              <AntText style={{ fontSize: 12.5, color: '#4B5563' }}>{cap.label}</AntText>
            </div>
          ))}
        </div>

        {/* 能力边界：如实声明答不了什么。
            ★ 只写"能做什么"会让用户误以为它什么都能答（§二十六）。 */}
        {(boundaries.length > 0 || assistant?.blocked_note) && (
          <div
            style={{
              marginTop: 14,
              padding: '10px 12px',
              background: '#FAFAFA',
              border: '1px solid #F0F0F0',
              borderRadius: 8,
            }}
          >
            <AntText type="secondary" style={{ fontSize: 11.5 }}>
              答不了：{boundaries.join('、')}
            </AntText>
            {assistant?.blocked_note && (
              <div style={{ marginTop: 6 }}>
                <AntText type="secondary" style={{ fontSize: 11.5 }}>
                  硬性转人工：{assistant.blocked_note}
                </AntText>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ padding: fullPage ? '16px 0 0' : '0 18px 16px' }}>
        {/* 常见问题（仅整页模式）：点一下直接问，降低"不知道能问什么"的门槛 */}
        {fullPage && (
          <div style={{ marginBottom: 12 }}>
            <AntText type="secondary" style={{ fontSize: 12 }}>
              可以这样问：
            </AntText>
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {suggestions.map((s) => (
                <Tag
                  key={s}
                  color="blue"
                  style={{ cursor: loading ? 'not-allowed' : 'pointer', marginInlineEnd: 0 }}
                  onClick={() => !loading && void ask(s)}
                >
                  {s}
                </Tag>
              ))}
            </div>
          </div>
        )}

        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onPressEnter={() => void ask()}
          disabled={loading}
          placeholder={assistant?.input_placeholder ?? '有什么问题，尽管问我...'}
          suffix={
            loading ? (
              <LoadingOutlined style={{ color: '#2563EB' }} />
            ) : (
              <SendOutlined
                onClick={() => void ask()}
                style={{ color: question.trim() ? '#2563EB' : '#C4C9D4', cursor: 'pointer' }}
              />
            )
          }
          style={{ borderRadius: 999, background: '#F7F8FA' }}
        />

        {error && <Alert type="error" showIcon style={{ marginTop: 10 }} message={error} />}

        {/* 回答区：★ 事实与回答分开显示（§三），并如实标注依据 */}
        {result && (
          <div
            style={{
              marginTop: 10,
              padding: '12px 14px',
              background: '#F7F9FC',
              border: '1px solid #EEF0F4',
              borderRadius: 10,
            }}
          >
            <AntText style={{ fontSize: 13, color: '#1F2937' }}>{result.answer}</AntText>

            {result.facts.length > 0 && (
              <div style={{ marginTop: 8, borderTop: '1px dashed #E5E7EB', paddingTop: 8 }}>
                <AntText type="secondary" style={{ fontSize: 11 }}>
                  事实（来自后端计算，可核对）
                </AntText>
                {result.facts.map((f) => (
                  <div
                    key={f.label}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 8,
                      marginTop: 4,
                    }}
                  >
                    <AntText style={{ fontSize: 12 }}>{f.label}</AntText>
                    <Tooltip title={f.evidence.evidence_ref ?? f.source_note}>
                      <AntText strong style={{ fontSize: 12, cursor: 'help' }}>
                        {f.evidence.value !== null && f.evidence.value !== undefined ? (
                          f.evidence.value
                        ) : (
                          <span style={{ color: '#6B7280', fontWeight: 400 }}>
                            {f.evidence.reason ?? '暂无数据'}
                          </span>
                        )}
                      </AntText>
                    </Tooltip>
                  </div>
                ))}
              </div>
            )}

            <div style={{ marginTop: 8 }}>
              <AntText type="secondary" style={{ fontSize: 11 }}>
                依据：
                {result.basis === 'policy_blocked'
                  ? '命中敏感策略，未调用模型、不给承诺'
                  : result.basis === 'no_data'
                    ? '没有真实数据支撑，未调用模型、不给推测'
                    : result.basis === 'llm_failed'
                      ? '模型不可用，以上为代码直接组织的事实'
                      : '事实 + 模型组织语言'}
              </AntText>
            </div>
            <div style={{ marginTop: 4 }}>
              <AntText type="secondary" style={{ fontSize: 11 }}>
                {result.note}
              </AntText>
            </div>
          </div>
        )}
      </div>
    </>
  )

  // ── 整页模式：不用 Card 包裹，由页面自己排版 ──
  if (fullPage) {
    return (
      <Space orientation="vertical" size={0} style={{ width: '100%' }}>
        {body}
      </Space>
    )
  }

  // ── 面板模式：工作台右栏用的窄卡片 ──
  return (
    <Card variant="outlined" style={{ marginBottom: 20 }} styles={{ body: { padding: 0 } }} title={header}>
      {body}
    </Card>
  )
}
