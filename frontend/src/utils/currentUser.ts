/**
 * 当前登录用户的**本地缓存读取**（TASK-019）。
 *
 * ★ 重要：这里读到的只是**用于界面显示**的缓存，
 *   **绝不作为鉴权依据** —— 是否登录、能不能访问，一律由后端说了算。
 *   令牌失效时后端返回 401，api/client.ts 会清缓存并跳登录页。
 *
 * 抽成独立模块的原因：AppLayout（顶栏）与 HomePage（问候语）都要用，
 * 各写一遍 localStorage 解析必然出现两处不一致（实测：顶栏显示"未登录"
 * 而问候语硬编码"张三"）。
 */

import { USER_KEY } from '../api/client'
import type { UserItem } from '../api/types'

const ROLE_LABELS: Record<string, string> = {
  ADMIN: '管理员',
  MEMBER: '成员',
}

export function readCachedUser(): UserItem | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as UserItem
    return parsed && typeof parsed.display_name === 'string' ? parsed : null
  } catch {
    return null
  }
}

/** 显示名。取不到时给中性的兜底文案，**不编造具体人名**。 */
export function displayName(): string {
  return readCachedUser()?.display_name ?? '当前用户'
}

export function roleLabel(role: string | undefined | null): string {
  return ROLE_LABELS[role ?? ''] ?? '—'
}
