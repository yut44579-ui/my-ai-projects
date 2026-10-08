import { App as AntdApp } from 'antd'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ThemeProvider } from './providers/ThemeProvider'
import './styles/global.css'

/**
 * ★ TASK-037：`ConfigProvider` 从 main.tsx **移进 ThemeProvider**。
 *   原因：主题偏好在用户资料里（后端存），要等拿到资料才知道用哪套；
 *   而 ConfigProvider 又必须在所有 AntD 组件之上。
 *   于是由一个 Provider 组件同时负责"拉偏好"和"把主题交给 ConfigProvider"。
 *   ★ locale 也一并挪进去（同一层配置，不拆两处）。
 *   ★ 放在 BrowserRouter 外层：主题不依赖路由，换页时不会重建。
 *
 * ★ TASK-044：`basename` 用 Vite 的 BASE_URL。
 *   部署在子路径（如 /demo/supply/）时，浏览器地址是
 *   `http://host:8081/demo/supply/reports`，
 *   但 React Router 只应看到 `/reports` —— 不设 basename 的话它会拿整段
 *   去匹配路由，结果所有页面都落到 404（通配路由跳回首页）。
 *   ★ `import.meta.env.BASE_URL` 由 vite 的 `base` 决定，
 *     本地开发时是 '/'，行为与改动前完全一致。
 */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AntdApp>
        <BrowserRouter basename={import.meta.env.BASE_URL}>
          <App />
        </BrowserRouter>
      </AntdApp>
    </ThemeProvider>
  </StrictMode>,
)
