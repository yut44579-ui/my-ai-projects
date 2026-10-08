/**
 * 极简请求封装（V1 只用 React 本地 state + fetch，不引入任何状态管理库）。
 *
 * 业务接口自 TASK-001 起在此基础上扩展；业务数字一律以 EvidenceValue 传输，
 * 前端不得自行计算业务数字（见 docs/DECISIONS.md D4）。
 *
 * TASK-019 起：所有业务请求自动带上 Bearer 令牌；收到 401 清令牌并跳登录页。
 */

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

/** 令牌存储键。★ 用 localStorage：见 AUTH_SPEC §8 的取舍说明（含 XSS 风险与将来改 HttpOnly Cookie 的条件）。 */
export const TOKEN_KEY = 'auth_token'
/** 当前用户缓存键（仅用于顶栏显示，不作为鉴权依据——鉴权永远由后端判定） */
export const USER_KEY = 'auth_user'

export type ApiError = {
  status: number
  message: string
  /** 后端错误码，如 empty_file / unsupported_format / unauthorized */
  code?: string
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* 隐私模式下可能不可写；此时登录态无法保持，属于可接受降级 */
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* ignore */
  }
}

/** 统一附加鉴权头。★ 所有请求都走这里，避免漏加。 */
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken()
  const headers: Record<string, string> = { ...extra }
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

/**
 * 401 处理：清掉本地令牌并跳登录页。
 * ★ 不做无限重定向：已经在 /login 上就不再跳。
 */
function handleUnauthorized(): void {
  clearAuth()
  if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
    window.location.replace('/login')
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: authHeaders({ Accept: 'application/json' }),
  })
  return parse<T>(resp)
}

/** JSON 提交。后端失败时带 {"error": 错误码, "message": 说明}。 */
export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify(body),
  })
  return parse<T>(resp)
}

/** PATCH 提交（更新商机/项目/内容）。 */
export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'application/json' }),
    body: JSON.stringify(body),
  })
  return parse<T>(resp)
}

/** DELETE（移除项目-客户关联）。 */
export async function apiDelete<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: authHeaders({ Accept: 'application/json' }),
  })
  return parse<T>(resp)
}

/** multipart 提交（导入预览/提交）。 */
export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: authHeaders(), // ★ 不要设 Content-Type：让浏览器自动带 boundary
    body: form,
  })
  return parse<T>(resp)
}

async function parse<T>(resp: Response): Promise<T> {
  const text = await resp.text()
  let payload: unknown = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = null
  }

  if (!resp.ok) {
    const body = (payload ?? {}) as { error?: string; message?: string; detail?: string }
    const message =
      body.message ?? body.detail ?? body.error ?? `请求失败：${resp.status}`
    // ★ 登录态失效统一在这里处理：清令牌 + 跳登录页
    if (resp.status === 401) handleUnauthorized()
    const err: ApiError = { status: resp.status, message, code: body.error }
    throw err
  }
  return payload as T
}
