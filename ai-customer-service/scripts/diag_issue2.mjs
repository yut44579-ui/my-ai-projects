/** 查「问题」页为什么渲染不出来。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9700, windowSize: '1400,900',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (p) => errs.push('EXC ' + (p.exceptionDetails?.exception?.description || '').slice(0, 300)))
  cdp.on('Runtime.consoleAPICalled', (p) => { if (p.type === 'error') errs.push('ERR ' + JSON.stringify(p.args?.map((a) => a.value)).slice(0, 200)) })

  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(6500)

  const st = await cdp.eval(`(() => ({
    tabOn: document.querySelector('#p-issue').classList.contains('on'),
    curTab: typeof curTab !== 'undefined' ? curTab : '?',
    ilistHtml: (document.querySelector('#ilist')||{}).innerHTML?.slice(0,160),
    isumCount: document.querySelectorAll('.isum').length,
    istats: (document.querySelector('#istats')||{}).innerText,
  }))()`)
  console.log('  状态:', JSON.stringify(st, null, 1))

  const api = await cdp.eval(`(async () => {
    try { const r = await fetch(API('/api/issues?status=open')); const d = await r.json();
      return { status: r.status, items: d.items.length, counts: d.counts, first: d.items[0] ? d.items[0].title : '无' } }
    catch (e) { return { err: String(e).slice(0,200) } }
  })()`)
  console.log('  接口:', JSON.stringify(api))

  const manual = await cdp.eval(`(async () => {
    try { await loadIssues(); return { ok: true, n: document.querySelectorAll('.isum').length } }
    catch (e) { return { err: String(e).slice(0,300) } }
  })()`)
  console.log('  手动 loadIssues:', JSON.stringify(manual))

  const open = await cdp.eval(`(async () => {
    try {
      const first = document.querySelector('.isum')
      if (!first) return { err: '没有 .isum' }
      first.click()
      await new Promise(r => setTimeout(r, 2500))
      const d = document.querySelector('#idetail')
      return { ok: true, hasDetail: !!d, text: d ? d.innerText.replace(/\\s+/g,' ').slice(0,200) : '' }
    } catch (e) { return { err: String(e).slice(0,300) } }
  })()`)
  console.log('  点开详情:', JSON.stringify(open))

  console.log('\n  JS 错误:')
  for (const e of errs.slice(0, 8)) console.log('   ', e)
} finally { await close() }
