import { Tooltip, Typography } from 'antd'
import type { EvidenceValue } from '../api/types'

type Props = {
  /** 后端返回的业务数字（D4 契约） */
  evidence: EvidenceValue
  suffix?: string
  /** 非 VALID 时额外显示的说明文字（指标卡用「暂无数据」），★ 仍然不显示任何数字 */
  noDataLabel?: string
}

/**
 * 业务数字的唯一渲染口（D4/AC6）。
 *
 * ★ NO_DATA / ERROR 时显示「—」并说明原因，绝不显示 0 ——
 *   0 是合法值，不能拿来冒充"没有数据"。前端也绝不自己算业务数字。
 */
export default function EvidenceNumber({ evidence, suffix = '', noDataLabel }: Props) {
  const { value, state, source_type, evidence_ref, reason } = evidence

  if (state !== 'VALID' || value === null) {
    const explain = reason ?? (state === 'ERROR' ? '取数失败' : '暂无数据')
    return (
      <Tooltip title={explain}>
        <Typography.Text type="secondary">
          —{noDataLabel ? <span style={{ fontSize: 12 }}> {noDataLabel}</span> : null}
        </Typography.Text>
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
