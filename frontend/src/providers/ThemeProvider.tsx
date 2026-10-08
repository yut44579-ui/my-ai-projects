import { ConfigProvider, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { apiGet, apiPost } from '../api/client'
import type { ProfileMe, ThemePreference } from '../api/types'
import { isDark, pickTheme } from '../theme'

const THEME_KEY = 'ui_theme'

type ThemeContextValue = {
  /** 主题偏好（来自后端，本地缓存一份用于首屏不闪） */
  preference: ThemePreference
  /** 实际是否深色（SYSTEM 时会跟随系统） */
  dark: boolean
  /** 修改主题：写后端 + 立即生效 */
  setPreference: (p: ThemePreference) => Promise<void>
  /** 当前登录者资料（个人中心与顶栏共用，避免各自请求一遍） */
  profile: ProfileMe | null
  /** 重新拉取资料 */
  reloadProfile: () => Promise<void>
  /** 用后端返回的最新资料覆盖（改完资料/头像后调用，省一次请求） */
  applyProfile: (p: ProfileMe) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

/** 读本地缓存的主题（首屏立刻用，避免亮→暗闪烁） */
function readCachedTheme(): ThemePreference {
  const v = localStorage.getItem(THEME_KEY)
  return v === 'DARK' || v === 'LIGHT' || v === 'SYSTEM' ? v : 'SYSTEM'
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPref] = useState<ThemePreference>(readCachedTheme)
  const [profile, setProfile] = useState<ProfileMe | null>(null)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
  )

  // ★ 跟随系统：监听系统主题变化。
  //   只有偏好为 SYSTEM 时才影响实际主题，但监听始终挂着 ——
  //   用户从 DARK 切回 SYSTEM 时能立刻用上正确的值，不用等到下次变化。
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const dark = isDark(preference, systemDark)

  // ★ 给 <html> 打标记：AntD 的算法管不到我们自己写的内联样式，
  //   所以额外提供一个 data-theme 属性，配合 global.css 里的 CSS 变量使用。
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  }, [dark])

  const applyProfile = useCallback((p: ProfileMe) => {
    setProfile(p)
    setPref(p.theme)
    localStorage.setItem(THEME_KEY, p.theme)
  }, [])

  const reloadProfile = useCallback(async () => {
    try {
      applyProfile(await apiGet<ProfileMe>('/api/profile/me'))
    } catch {
      // ★ 未登录或接口不可用时静默：主题已有本地缓存，不该因为拿不到资料就白屏
    }
  }, [applyProfile])

  // 登录后拉一次真实偏好（本地缓存只是首屏占位）
  useEffect(() => {
    if (localStorage.getItem('auth_token')) void reloadProfile()
  }, [reloadProfile])

  const setPreference = useCallback(async (p: ThemePreference) => {
    // ★ 先本地生效再请求：切主题要"即时"，等网络往返会让人以为没点上
    setPref(p)
    localStorage.setItem(THEME_KEY, p)
    try {
      const updated = await apiPost<ProfileMe>('/api/profile/theme', { theme: p })
      setProfile(updated)
    } catch {
      // 后端失败不回滚本地 —— 用户看到的仍然是刚选的主题，
      // 只是换设备后不生效；回滚会造成"点了一下又跳回去"的更差体验
    }
  }, [])

  const value = useMemo(
    () => ({ preference, dark, setPreference, profile, reloadProfile, applyProfile }),
    [preference, dark, setPreference, profile, reloadProfile, applyProfile],
  )

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          ...pickTheme(preference, systemDark),
          // ★ 让 AntD 的弹出层（Dropdown/Modal/Tooltip）也跟随算法，
          //   否则深色下这些浮层还是白的
          algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        }}
      >
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme 必须在 ThemeProvider 内使用')
  return ctx
}
