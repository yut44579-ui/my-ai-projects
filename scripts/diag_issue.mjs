/** 诊断报错弹窗为什么打不开。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9670, windowSize: '1400,900',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (p) => errs.push('EXC ' + (p.exceptionDetails?.exception?.description || '').slice(0, 260)))
  cdp.on('Runtime.consoleAPICalled', (p) => { if (p.type === 'error') errs.push('ERR ' + JSON.stringify(p.args?.map((a) => a.value)).slice(0, 200)) })
  const failed = []
  cdp.on('Network.loadingFailed', (p) => failed.push(p.errorText))
  const reqs = []
  cdp.on('Network.responseReceived', (p) => { if (p.response.status >= 400) reqs.push(p.response.status + ' ' + p.response.url) })

  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(6000)

  const pre = await cdp.eval(`(() => ({
    links: document.querySelectorAll('.tur a').length,
    fnType: typeof openReport,
    metaType: typeof issueMeta,
    modalExists: !!document.querySelector('#reportModal'),
    firstLinkHtml: document.querySelector('.tur a') ? document.querySelector('.tur a').outerHTML.slice(0,150) : '无',
  }))()`)
  console.log('  页面状态:', JSON.stringify(pre, null, 1))

  // 手动调 openReport
  const manual = await cdp.eval(`(async () => {
    try { await openReport(1, 'test', '手动测试'); return { ok: true, open: document.querySelector('#reportModal').classList.contains('on') } }
    catch (e) { return { ok: false, err: String(e).slice(0,300) } }
  })()`)
  console.log('  手动调 openReport:', JSON.stringify(manual))

  // 手动调接口
  const meta = await cdp.eval(`(async () => {
    try { const r = await fetch(API('/api/issues/meta')); return { status: r.status, body: (await r.text()).slice(0,60) } }
    catch (e) { return { err: String(e).slice(0,200) } }
  })()`)
  console.log('  手动调 meta 接口:', JSON.stringify(meta))

  console.log('\n  4xx/5xx 请求:', reqs.slice(0, 5))
  console.log('  加载失败:', failed.slice(0, 5))
  console.log('  JS 错误:')
  for (const e of errs.slice(0, 6)) console.log('   ', e)
} finally { await close() }
