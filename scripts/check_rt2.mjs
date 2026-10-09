/** 简化验证：客服能否自动看到接管期间的新消息。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

const S = 'rt2-' + Math.floor(Math.random() * 100000)
await fetch(BASE + '/api/web/chat', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: S, text: '能报个价吗' }),
})
await wait(15000)
const ho = await jget('/api/handover')
const it = ho.items.find((x) => x.user_id === S)
console.log('  会话', S, '#', it.id)
await jpost(`/api/handover/${it.id}/take`, { by: '张工' })
await jpost(`/api/handover/${it.id}/reply`, { text: '我看下图纸', by: '张工', send: true })
await wait(3000)

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_rt2', port: 10040, windowSize: '1420,1000',
})
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(9000)
  await cdp.eval(`switchTab('ho')`)
  await wait(4000)

  const st1 = await cdp.eval(`({ tab: (typeof curTab!=='undefined'?curTab:'?'),
    items: document.querySelectorAll('.hoitem').length,
    hasDetail: !!document.querySelector('#hoDetail'),
    txt: ((document.querySelector('#hoDetail')||{}).innerText||'').slice(0,80) })`)
  console.log('  页面状态:', JSON.stringify(st1))

  // 找到并打开我们的那条
  const opened = await cdp.eval(`(async () => {
    const items = Array.from(document.querySelectorAll('.hoitem'));
    for (let i = 0; i < items.length; i++) {
      items[i].click();
      await new Promise(function(r){ setTimeout(r, 2200); });
      var t = (document.querySelector('#hoDetail')||{}).innerText || '';
      if (t.indexOf(${JSON.stringify(S)}) >= 0) return 'ok:' + i;
    }
    return 'notfound';
  })()`)
  console.log('  打开目标会话:', opened)
  await wait(3000)

  const b = await cdp.eval(`(function(){ var w = document.querySelector('.tlwrap');
    return { rows: w ? w.querySelectorAll('.tl-row').length : -1 }; })()`)
  console.log('  打开时时间线行数:', JSON.stringify(b))

  // 客户发两句
  for (const text of ['什么意思', '？']) {
    await fetch(BASE + '/api/web/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: S, text }),
    })
    await wait(15000)
  }

  console.log('  等自动刷新（最多 36 秒，页面不动）…')
  let found = false
  for (let i = 0; i < 12; i++) {
    await wait(3000)
    const now = await cdp.eval(`(function(){
      var d = (document.querySelector('#hoDetail')||{}).innerText || '';
      var w = document.querySelector('.tlwrap');
      return { rows: w ? w.querySelectorAll('.tl-row').length : -1,
               hit: d.indexOf('什么意思') >= 0 }; })()`)
    if (now.hit) {
      console.log(`  ★ 第 ${(i + 1) * 3} 秒出现（${now.rows} 行）`)
      found = true
      break
    }
  }
  console.log('  ★ 客服自动看到新消息:', found ? '✅ 能' : '❌ 不能')

  const a = await cdp.eval(`(function(){ var w = document.querySelector('.tlwrap');
    var d = (document.querySelector('#hoDetail')||{}).innerText || '';
    return { rows: w ? w.querySelectorAll('.tl-row').length : -1,
             tail: d.replace(/\\s+/g,' ').slice(-90),
             atBottom: w ? (w.scrollHeight - w.scrollTop - w.clientHeight < 40) : null,
             listHasBadge: ((document.querySelector('#holist')||{}).innerText||'').indexOf('又说了') >= 0 }; })()`)
  console.log('  最终:', JSON.stringify(a))

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_realtime.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_realtime.png')
} finally { await close() }
