import { Card, Tooltip, Typography } from 'antd'
import type { EvidenceValue } from '../api/types'
import EvidenceNumber from './EvidenceNumber'

const { Text } = Typography

type Props = {
  title: string
  /** 后端算好的业务数字（★ 前端不算，只显示） */
  evidence: EvidenceValue
  /** 这个数字的统计口径（静态说明文字，不是算出来的数字） */
  hint: string
  /** 后端给的筛选口径字符串，放进悬浮提示便于核对 */
  scope?: string
}

/**
 * 指标卡：顶部一行四个（客户总数 / 测试数据 / 待人工裁决 / 本周新增）。
 *
 * ★ 数字一律走 EvidenceNumber（D4 的唯一渲染口）：
 *   NO_DATA 显示「— 暂无数据」，VALID 才显示数字（真零就是 0）。
 */
export default function StatCard({ title, evidence, hint, scope }: Props) {
  const noData = evidence.state !== 'VALID' || evidence.value === null

  return (
    <Card variant="outlined" styles={{ body: { padding: '16px 20px' } }}>
      <Text type="secondary" style={{ fontSize: 13 }}>
        {title}
      </Text>
      <div style={{ fontSize: 26, lineHeight: '36px', marginTop: 4 }}>
        <EvidenceNumber evidence={evidence} noDataLabel="暂无数据" />
      </div>
      <Tooltip title={`统计口径：${hint}｜筛选条件：${scope ?? '全部'}`}>
        <Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
          {noData ? '该口径下没有数据' : hint}
        </Text>
      </Tooltip>
    </Card>
  )
}
