import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './layouts/AppLayout'
import RequireAuth from './components/RequireAuth'
import AiAssistantPage from './pages/AiAssistantPage'
import AnalyticsPage from './pages/AnalyticsPage'
import BusinessInsightPage from './pages/BusinessInsightPage'
import ConnectionsPage from './pages/ConnectionsPage'
import ContentsPage from './pages/ContentsPage'
import CustomerDetailPage from './pages/CustomerDetailPage'
import CustomersPage from './pages/CustomersPage'
import FileAssetsPage from './pages/FileAssetsPage'
import HomePage from './pages/HomePage'
import KnowledgePage from './pages/KnowledgePage'
import LoginPage from './pages/LoginPage'
import OpportunitiesPage from './pages/OpportunitiesPage'
import ProfilePage from './pages/ProfilePage'
import ProposalsPage from './pages/ProposalsPage'
import ProjectsPage from './pages/ProjectsPage'
import ReportsPage from './pages/ReportsPage'
import ResearchesPage from './pages/ResearchesPage'
import ResearchPage from './pages/ResearchPage'
import RiskCenterPage from './pages/RiskCenterPage'
import SecuritySettingsPage from './pages/SecuritySettingsPage'
import SystemSettingsPage from './pages/SystemSettingsPage'
import UsersPage from './pages/UsersPage'
import VisitsPage from './pages/VisitsPage'

/**
 * 路由表（TASK-019 起加了路由守卫）。
 *
 *   公开：/login
 *   受保护（RequireAuth 包着 AppLayout）：其余全部
 *
 * ★ 未匹配路径回落到工作台；未登录访问任何业务路由会被守卫送到 /login，
 *   登录后自动回到原来要去的页面。
 */
export default function App() {
  return (
    <Routes>
      {/* 登录页：不需要登录，也不套主布局（避免登录页上出现侧栏） */}
      <Route path="/login" element={<LoginPage />} />

      {/* 其余全部需要登录 */}
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/customers/:id" element={<CustomerDetailPage />} />
          <Route path="/opportunities" element={<OpportunitiesPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/visits" element={<VisitsPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          {/*
            ★ 「AI助手」（需求 §八 主导航项）现在是**独立页面**（TASK-036）。
              此前这里写成 `<Navigate to="/" replace />` —— 点菜单会回到工作台，
              用户看到的和点「工作台」一样，等于菜单撒了谎。
              现在渲染 `AiAssistantPage`，它与工作台右栏**共用同一个组件**，
              实现只有一份，但这里是整页形态。
          */}
          <Route path="/ai-assistant" element={<AiAssistantPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/plans" element={<ProposalsPage />} />
          <Route path="/contents" element={<ContentsPage />} />
          <Route path="/marketing" element={<ContentsPage />} />
          <Route path="/connections" element={<ConnectionsPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/ai/business" element={<BusinessInsightPage />} />
          <Route path="/settings/security" element={<SecuritySettingsPage />} />
          <Route path="/settings" element={<SystemSettingsPage />} />
          <Route path="/files" element={<FileAssetsPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/settings/users" element={<UsersPage />} />
          <Route path="/researches" element={<ResearchesPage />} />
          <Route path="/risk" element={<RiskCenterPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}
