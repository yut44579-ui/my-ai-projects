import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { getToken } from '../api/client'

/**
 * 路由守卫（TASK-019）。规格见 docs/AUTH_SPEC.md §8。
 *
 * ★ 只判断"本地有没有令牌"，**不判断令牌是否有效** ——
 *   有效性只能由后端说了算。若令牌已失效，第一个业务请求会返回 401，
 *   由 api/client.ts 统一清令牌并跳回登录页。
 *   这样避免前端自己解析令牌（那等于把鉴权逻辑复制一份到前端，容易不一致）。
 */
export default function RequireAuth() {
  const location = useLocation()
  const token = getToken()

  if (!token) {
    // 记住原本要去的页面，登录后回去
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <Outlet />
}
