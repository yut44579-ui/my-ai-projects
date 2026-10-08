import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// ★ TASK-044：支持「子路径部署」。
//   背景：原来每个系统占一个端口（8082、8083…），结果是
//   **每加一个项目就要去腾讯云开一个新端口**，非常麻烦；
//   而且手机网络对非标准端口的放行不一致 —— 实测用户手机能开 8081（作品集）
//   却打不开 8082（AI供应链，一直转圈）。根因至今没能从用户手机上复现，
//   但换成"一个端口 + 子路径"之后，这个问题**从结构上消失**。
//
//   目标形态：
//       8081/                → 作品集
//       8081/demo/supply/    → AI供应链
//       8081/demo/sales/     → 销售 Agent
//   以后再加项目只需要加一段 nginx 配置，**永不再开端口**。
//
//   ★ `base` 默认 '/'，本地开发与改动前完全一致（不改本地行为）。
const base = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发期把 /api 转发到 FastAPI，避免 CORS
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
