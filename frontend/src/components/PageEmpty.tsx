import { Card, Empty, Typography } from 'antd'

const { Title, Text } = Typography

type Props = {
  /** 卡片标题 */
  title: string
  /** 空态说明文案，默认「暂无数据」 */
  description?: string
}

/**
 * 页面空态卡片。
 *
 * 骨架阶段所有页面只用它渲染空态 —— 不出现任何假数字、假图表、假列表。
 */
export default function PageEmpty({ title, description = '暂无数据' }: Props) {
  return (
    <Card variant="outlined" styles={{ body: { padding: 48 } }}>
      <Title level={4} style={{ marginBottom: 4, fontWeight: 600 }}>
        {title}
      </Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 32 }}>
        该模块尚未接入数据
      </Text>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={<Text type="secondary">{description}</Text>}
      />
    </Card>
  )
}
