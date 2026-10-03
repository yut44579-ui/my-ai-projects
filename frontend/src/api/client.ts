/**
 * 极简请求封装（V1 只用 React 本地 state + fetch，不引入任何状态管理库）。
 *
 * 业务接口自 TASK-001 起在此基础上扩展；业务数字一律以 EvidenceValue 传输，
 * 前端不得自行计算业务数字（见 docs/DECISIONS.md D4）。
 */

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

export type ApiError = {
  status: number
  message: string
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!resp.ok) {
    const err: ApiError = { status: resp.status, message: `请求失败：${resp.status}` }
    throw err
  }
  return (await resp.json()) as T
}
