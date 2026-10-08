import type { ThemeConfig } from 'antd'
import { theme as antdTheme } from 'antd'

/**
 * 主题（TASK-037）。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【为什么浅色主题的 token 写死在常量里，深色用算法生成】
 * ═══════════════════════════════════════════════════════════════
 *   浅色是**原始设计基调**（对照参考图做的），所以逐项写明确值，保持稳定。
 *   深色不逐项写死 —— 那会变成两份需要同步维护的配色，
 *   改一处忘一处就会出现"深色下某处还是白底黑字"（§四/D46 的教训）。
 *   改用 AntD 的 `darkAlgorithm` 由主色推导整套中性色，
 *   只在**算法管不到的地方**（品牌色、侧栏深蓝）做覆盖。
 *
 * ═══════════════════════════════════════════════════════════════
 * 【侧栏为什么深色主题下仍然保持深蓝】
 * ═══════════════════════════════════════════════════════════════
 *   侧栏本来就是深蓝（#0F2544）。深色主题下如果让它跟着变浅，
 *   反而与"深色"的整体印象冲突。所以侧栏**两套主题下都是深蓝**，
 *   只微调边框与激活态，保证对比度。
 */

export const PRIMARY_COLOR = '#2563EB'
export const LAYOUT_BG = '#F5F7FA'
export const CARD_RADIUS = 12

/** 侧栏深蓝：两套主题共用 */
export const SIDER_BG = '#0F2544'

/** 浅色（原始基调） */
export const lightTheme: ThemeConfig = {
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
      siderBg: SIDER_BG,
      bodyBg: LAYOUT_BG,
      headerHeight: 56,
    },
    Menu: {
      itemBorderRadius: 8,
      itemSelectedBg: PRIMARY_COLOR,
      itemSelectedColor: '#FFFFFF',
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

/**
 * 深色。
 * ★ `algorithm: darkAlgorithm` 负责推导整套中性色（背景/边框/文字层级）。
 *   token 里只覆盖品牌色与少数需要保证对比度的值。
 */
export const darkTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: PRIMARY_COLOR,
    colorLink: '#60A5FA',
    borderRadius: 8,
    fontSize: 14,
  },
  components: {
    Layout: {
      // 侧栏两套主题都保持深蓝，避免"深色模式下侧栏反而变浅"的割裂感
      siderBg: SIDER_BG,
      headerHeight: 56,
    },
    Menu: {
      itemBorderRadius: 8,
      // ★ 深色下仍用主色做选中态：darkAlgorithm 推导出的选中底色对比度偏弱
      itemSelectedBg: PRIMARY_COLOR,
      itemSelectedColor: '#FFFFFF',
      itemHeight: 42,
    },
    Card: {
      borderRadiusLG: CARD_RADIUS,
    },
  },
}

/** 兼容旧引用（原来只导出 appTheme） */
export const appTheme = lightTheme

/**
 * 按偏好挑主题。
 * ★ SYSTEM 交给 AntD 的 `theme.defaultAlgorithm`/`darkAlgorithm` 无法自动完成 ——
 *   AntD 不读操作系统偏好，需要我们自己用 `matchMedia` 判断。
 */
export function pickTheme(
  preference: 'SYSTEM' | 'LIGHT' | 'DARK',
  systemPrefersDark: boolean,
): ThemeConfig {
  if (preference === 'DARK') return darkTheme
  if (preference === 'LIGHT') return lightTheme
  return systemPrefersDark ? darkTheme : lightTheme
}

/** 主题是否实际为深色（供自定义 CSS 变量用） */
export function isDark(
  preference: 'SYSTEM' | 'LIGHT' | 'DARK',
  systemPrefersDark: boolean,
): boolean {
  if (preference === 'DARK') return true
  if (preference === 'LIGHT') return false
  return systemPrefersDark
}
