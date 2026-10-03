import type { ThemeConfig } from 'antd'

/** 视觉基调：浅色企业级（白底卡片 / 极浅灰背景 / 主色 #2563EB / 圆角 8~12px） */
export const PRIMARY_COLOR = '#2563EB'
export const LAYOUT_BG = '#F5F7FA'
export const CARD_RADIUS = 12

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: PRIMARY_COLOR,
    colorBgLayout: LAYOUT_BG,
    colorLink: PRIMARY_COLOR,
    borderRadius: 8,
    fontSize: 14,
    colorText: '#1F2937',
    colorTextSecondary: '#6B7280',
    colorBorderSecondary: '#EEF0F4',
  },
  components: {
    Layout: {
      headerBg: '#FFFFFF',
      siderBg: '#FFFFFF',
      bodyBg: LAYOUT_BG,
      headerHeight: 56,
    },
    Menu: {
      itemBorderRadius: 8,
      itemSelectedBg: '#EFF6FF',
      itemSelectedColor: PRIMARY_COLOR,
      itemHeight: 42,
    },
    Card: {
      borderRadiusLG: CARD_RADIUS,
    },
    Empty: {
      colorTextDescription: '#8C93A0',
    },
  },
}
