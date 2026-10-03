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
  /** 后端错误码，如 empty_file / unsupported_format / duplicate_import */
  code?: string
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
  })
  return parse<T>(resp)
}

/** multipart 提交（导入预览/提交）。后端失败时带 {"error": 错误码, "message": 说明}。 */
export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, { method: 'POST', body: form })
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
    const err: ApiError = { status: resp.status, message, code: body.error }
    throw err
  }
  return payload as T
}
