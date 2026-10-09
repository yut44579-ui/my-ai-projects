/** 抓"发一条就刷新一下"到底是什么在动。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9830, windowSize: '1420,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  const navs = []
  cdp.on('Page.frameNavigated', (p) => { if (!p.frame.parentId) navs.push('NAV ' + p.frame.url) })
  cdp.on('Page.loadEventFired', () => navs.push('LOAD'))
  cdp.on('Page.domContentEventFired', () => navs.push('DOMREADY'))

  // ── A. 客户挂件：客服回复时会不会重载 ──
  console.log('【A】客户挂件')
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(800)
  await cdp.eval(`(() => {
    const i=document.querySelector('#q2')
    const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    s.call(i,'能报个价吗'); i.dispatchEvent(new Event('input',{bubbles:true}))
    document.querySelector('#go2').click(); return 1 })()`)
  await wait(14000)
  navs.length = 0
  const beforeA = await cdp.eval(`(() => {
    const m = document.querySelector('#msgs2')
    m.dataset.probe = 'PROBE-A'       // ★ 打个标记：如果被重建，标记就没了
    return { nodes: m.children.length, spinning: !!document.querySelector('.spinner'), probe: m.dataset.probe }
  })()`)
  console.log('   客服回复前:', JSON.stringify(beforeA))

  // 客服接管 + 回复
  const ho = await (await fetch(BASE + '/api/handover')).json()
  const it = ho.items[0]
  await fetch(BASE + `/api/handover/${it.id}/take`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ by: '张工' }),
  })
  await wait(3000)
  const midA = await cdp.eval(`(() => {
    const m = document.querySelector('#msgs2')
    return { nodes: m.children.length, spinning: !!document.querySelector('.spinner'),
             probe: m.dataset.probe || '(没了!)', taken: !!document.querySelector('.m2.taken') }
  })()`)
  console.log('   接管后    :', JSON.stringify(midA))

  await fetch(BASE + `/api/handover/${it.id}/reply`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: '报价要工程师看过图纸才能定，你把零件类型和材料发我一下。', by: '张工', send: true }),
  })
  await wait(4000)
  const afterA = await cdp.eval(`(() => {
    const m = document.querySelector('#msgs2')
    return { nodes: m.children.length, spinning: !!document.querySelector('.spinner'),
             probe: m.dataset.probe || '(没了 — 说明被重建了!)',
             last: m.lastElementChild ? m.lastElementChild.innerText.replace(/\\s+/g,' ').slice(0,50) : '' }
  })()`)
  console.log('   客服回复后:', JSON.stringify(afterA))
  console.log('   期间发生的导航/加载事件:', navs.length ? navs : '（无）')

  // ── B. 客服台：发消息时会不会重载 ──
  console.log('\n【B】客服台人工页')
  navs.length = 0
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(6000)
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return 1 })()`)
  await wait(3500)
  navs.length = 0
  const beforeB = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap')
    if (w) w.dataset.probe = 'PROBE-B'
    return { rows: document.querySelectorAll('.tl-row').length,
             probe: w ? w.dataset.probe : '(没有会话区)' }
  })()`)
  console.log('   发送前:', JSON.stringify(beforeB))
  await cdp.eval(`(() => { const t=document.querySelector('#hoText'); t.value='测试不刷新'; return 1 })()`)
  await cdp.eval(`hoReply()`)
  await wait(4500)
  const afterB = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap')
    return { rows: document.querySelectorAll('.tl-row').length,
             probe: w ? (w.dataset.probe || '(没了 — 说明被重建了!)') : '(没有)',
             draft: document.querySelector('#hoText') ? document.querySelector('#hoText').value : null,
             msg: (document.querySelector('#hoMsg')||{}).textContent }
  })()`)
  console.log('   发送后:', JSON.stringify(afterB))
  console.log('   期间发生的导航/加载事件:', navs.length ? navs : '（无）')
} finally { await close() }
