import { Tooltip, Typography } from 'antd'
import type { EvidenceValue } from '../api/types'

type Props = {
  /** 后端返回的业务数字（D4 契约） */
  evidence: EvidenceValue
  suffix?: string
}

/**
 * 业务数字的唯一渲染口（D4/AC6）。
 *
 * ★ NO_DATA / ERROR 时显示「—」并说明原因，绝不显示 0 ——
 *   0 是合法值，不能拿来冒充"没有数据"。前端也绝不自己算业务数字。
 */
export default function EvidenceNumber({ evidence, suffix = '' }: Props) {
  const { value, state, source_type, evidence_ref, reason } = evidence

  if (state !== 'VALID' || value === null) {
    return (
      <Tooltip title={reason ?? (state === 'ERROR' ? '取数失败' : '暂无数据')}>
        <Typography.Text type="secondary">—</Typography.Text>
      </Tooltip>
    )
  }

  return (
    <Tooltip title={`来源 ${source_type}｜证据 ${evidence_ref ?? '—'}`}>
      <span style={{ fontWeight: 600 }}>
        {value}
        {suffix}
      </span>
    </Tooltip>
  )
}
