/** 诊断公网挂件为什么没反应。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9530,
  windowSize: '1200,900',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  await cdp.send('Network.enable')
  const errors = []
  cdp.on('Runtime.exceptionThrown', (p) => {
    errors.push('EXC: ' + JSON.stringify(p.exceptionDetails?.exception?.description || p).slice(0, 300))
  })
  cdp.on('Runtime.consoleAPICalled', (p) => {
    if (p.type === 'error') errors.push('ERR: ' + JSON.stringify(p.args?.map((a) => a.value)).slice(0, 200))
  })
  const reqs = []
  cdp.on('Network.requestWillBeSent', (p) => reqs.push(p.request.url))
  const failed = []
  cdp.on('Network.loadingFailed', (p) => failed.push(p.errorText))

  await cdp.send('Page.navigate', { url: BASE + '/widget.html' })
  await new Promise((r) => setTimeout(r, 4000))

  const pre = await cdp.eval(`(() => ({
    base: window.__BASE__,
    hasAPI: typeof API,
    fab: !!document.querySelector('.fab'),
    q: !!document.querySelector('#q'),
    go: !!document.querySelector('#go'),
    href: location.href,
  }))()`)
  console.log('  页面状态:', JSON.stringify(pre))

  // 手动调一次，看真实错误
  const tryIt = await cdp.eval(`(async () => {
    try {
      const url = API('/api/web/chat')
      const r = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: 'diag-1', text: '上班时间是几点' }),
      })
      return { url, status: r.status, body: (await r.text()).slice(0, 200) }
    } catch (e) { return { error: String(e) } }
  })()`)
  console.log('  手动 fetch:', JSON.stringify(tryIt))

  console.log('\n  请求过的 URL:')
  for (const u of reqs.slice(-8)) console.log('   ', u)
  if (failed.length) console.log('  失败的:', failed)
  if (errors.length) {
    console.log('\n  JS 错误:')
    for (const e of errors.slice(0, 5)) console.log('   ', e)
  } else console.log('\n  没有 JS 错误')
} finally {
  await close()
}
