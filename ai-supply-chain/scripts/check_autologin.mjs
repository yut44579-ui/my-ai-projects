/** 确认：带令牌访问根路径 → 直接进工作台（不是"登录页没渲染"）。 */
import { launchEdge } from './lib/cdp.mjs'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9373,
  windowSize: '390,844',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  })
  await cdp.send('Page.navigate', { url: 'http://43.134.58.30:8082/' })
  await new Promise((r) => setTimeout(r, 5000))
  const a = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      path: location.pathname,
      hasToken: !!localStorage.getItem('auth_token'),
      isWorkbench: t.includes('工作台') && t.includes('客户总数'),
      customer: (t.match(/客户总数\\s*(\\d+)/) || [])[1] || null,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      snippet: t.replace(/\\s+/g, ' ').slice(0, 170),
    }
  })()`)
  console.log('  地址        :', a.path)
  console.log('  本地有令牌  :', a.hasToken ? '是' : '否')
  console.log('  是工作台    :', a.isWorkbench ? '✅ 客户总数=' + a.customer : '❌')
  console.log('  横向溢出    :', a.overflow, 'px')
  console.log('  文案        :', a.snippet)
  await cdp.shot('final_workbench_mobile', 'D:\\biz-assistant-int\\docs\\screenshots')
} finally {
  await close()
}
