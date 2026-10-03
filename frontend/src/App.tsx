import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './layouts/AppLayout'
import CustomersPage from './pages/CustomersPage'
import HomePage from './pages/HomePage'

/**
 * 路由表：骨架阶段仅 / 与 /customers 两个占位页，
 * 未匹配路径回落到首页（避免点空链接出现白屏）。
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
