import { RobotOutlined } from '@ant-design/icons'
import { Button, Space, Tooltip, Typography, message } from 'antd'
import { useState } from 'react'
import { apiPost } from '../api/client'

const { Text } = Typography

export type DraftResponse = {
  text: string
  purpose: string
  label: string
  note: string
  sources: { evidence_ref: string; document_title: string; seq: number }[]
}

export type AiDraftButtonProps = {
  /** 字段类型，取值见后端 `/api/ai/draft-fields` */
  purpose: string
  /** 已知信息（如 name / title / customer）；后端只用这些，缺的一律不提 */
  context: Record<string, unknown>
  /** 生成后回填到表单字段 */
  onGenerated: (text: string) => void
  /** 按钮文案，默认「AI 生成」 */
  label?: string
  /** 生成前的前置校验（如必填项没填就提示，省一次请求） */
  guard?: () => boolean
  disabled?: boolean
}

/**
 * AI 辅助填写按钮（TASK-042）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【只给「描述性文字」用，不给「事实性输入」用】
 * ═══════════════════════════════════════════════════════════════
 *   这个按钮**不应该**出现在这些字段旁边：
 *     · 线索研究的「公开资料原文」—— 它是证据来源。
 *       AI 只从真实原文里抽事实并逐字回验；原文若由 AI 写，验证就没意义了。
 *     · 客户需求 / 背景 —— 必须是客户真实说过的话，编了等于伪造需求
 *     · 金额 / 折扣 / 进度 / 概率 / 联系方式 —— 是事实或人的判断
 *   ★ 白名单由**后端** `/api/ai/draft-fields` 决定，前端不在各页各写一套判断。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【生成结果只是草稿】
 * ═══════════════════════════════════════════════════════════════
 *   点一下把文字**填进输入框**，不直接提交、不写库。
 *   用户看得见、改得动，按保存才生效 —— 这样「人确认过」这件事才成立。
 */
export default function AiDraftButton({
  purpose,
  context,
  onGenerated,
  label = 'AI 生成',
  guard,
  disabled,
}: AiDraftButtonProps) {
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (guard && !guard()) return
    setLoading(true)
    try {
      const res = await apiPost<DraftResponse>('/api/ai/draft', { purpose, context })
      onGenerated(res.text)
      // ★ 把后端那句「这只是草稿，请核对」原样显示 —— 不要让用户以为可以直接用
      message.success(res.note, 6)
    } catch (err) {
      message.error((err as { message?: string }).message ?? '生成失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space size={6} style={{ marginBottom: 6 }}>
      <Button
        size="small"
        type="link"
        icon={<RobotOutlined />}
        loading={loading}
        disabled={disabled}
        onClick={() => void run()}
        style={{ padding: 0, height: 'auto' }}
      >
        {label}
      </Button>
      <Tooltip title="生成结果只填入输入框，不直接保存；你可以改了再存">
        <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
          生成的是草稿
        </Text>
      </Tooltip>
    </Space>
  )
}

/**
 * 字段标签 + AI 按钮的组合，直接放进 `<Form.Item label={...}>`。
 *
 * ★ 为什么把「标签 + 按钮」封装起来而不是各页自己写：
 *   同一个字段在新建弹窗和编辑弹窗里各写一遍，很容易出现
 *   「新建有 AI、编辑没有」这种不一致（前端已经踩过类似的坑）。
 */
export function AiLabel({
  text,
  purpose,
  context,
  onGenerated,
  guard,
  excludedWhy,
}: {
  text: string
  purpose?: string
  context?: Record<string, unknown>
  onGenerated?: (text: string) => void
  guard?: () => boolean
  /**
   * 该字段**刻意不提供** AI 时，用一句话说明原因。
   * ★ 显示原因而不是默默没有按钮 —— 否则用户会问「为什么这里没有 AI」。
   */
  excludedWhy?: string
}) {
  return (
    <Space size={8} align="center">
      <span>{text}</span>
      {purpose && onGenerated ? (
        <AiDraftButton
          purpose={purpose}
          context={context ?? {}}
          onGenerated={onGenerated}
          guard={guard}
        />
      ) : null}
      {excludedWhy ? (
        <Tooltip title={excludedWhy}>
          <Text type="secondary" style={{ fontSize: 11, cursor: 'help' }}>
            无 AI（需真实内容）
          </Text>
        </Tooltip>
      ) : null}
    </Space>
  )
}
