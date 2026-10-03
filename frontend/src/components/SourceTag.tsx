import { Tag } from 'antd'
import type { CustomerSourceType } from '../api/types'
import { SOURCE_TAG_COLORS } from '../api/types'

/** 来源 Tag：REAL / TEST / MANUAL 一眼可辨（TEST 是橙色，提醒这是测试数据）。
 *  列表页与详情页共用同一个渲染，避免两处各写一套。 */
export default function SourceTag({ value }: { value: CustomerSourceType }) {
  return <Tag color={SOURCE_TAG_COLORS[value] ?? 'default'}>{value}</Tag>
}
