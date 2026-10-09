/** 最小诊断：人工页为什么发送没反应。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9790, windowSize: '1420,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (x) => errs.push('EXC ' + (x.exceptionDetails?.exception?.description || '').slice(0, 260)))
  cdp.on('Runtime.consoleAPICalled', (x) => { if (x.type === 'error') errs.push('ERR ' + JSON.stringify(x.args?.map((a) => a.value)).slice(0, 220)) })

  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await cdp.waitFor(`document.querySelector('.tlwrap')`, { timeout: 25000, label: '会话记录' })
  await wait(2500)

  const st = await cdp.eval(`(() => ({
    hasText: !!document.querySelector('#hoText'),
    hasMsg: !!document.querySelector('#hoMsg'),
    hoCurId: (typeof hoCur !== 'undefined' && hoCur) ? hoCur.id : null,
    curTab: typeof curTab !== 'undefined' ? curTab : '?',
  }))()`)
  console.log('  初始状态:', JSON.stringify(st))

  // 逐步走
  const step = await cdp.eval(`(() => {
    const el = document.querySelector('#hoText')
    if (!el) return { err: '没有 #hoText' }
    el.value = '探针内容ABC'
    const readBack = document.querySelector('#hoText').value
    const dollarRead = $('#hoText').value
    return { set: '探针内容ABC', readBack, dollarRead, same: readBack === dollarRead }
  })()`)
  console.log('  设值并回读:', JSON.stringify(step))

  const call = await cdp.eval(`(() => {
    try { const p = hoReply(); return { called: true, isPromise: !!(p && p.then) } }
    catch (e) { return { called: false, err: String(e).slice(0, 200) } }
  })()`)
  console.log('  调 hoReply:', JSON.stringify(call))
  await wait(4000)

  const after = await cdp.eval(`(() => ({
    draft: document.querySelector('#hoText') ? document.querySelector('#hoText').value : null,
    msg: document.querySelector('#hoMsg') ? document.querySelector('#hoMsg').textContent : null,
    rows: document.querySelectorAll('.tl-row').length,
  }))()`)
  console.log('  调用后:', JSON.stringify(after))

  console.log('\n  JS 错误:')
  for (const e of errs.slice(0, 8)) console.log('   ', e)
  if (!errs.length) console.log('    没有')
} finally { await close() }
