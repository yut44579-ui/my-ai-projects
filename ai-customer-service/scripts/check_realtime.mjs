/** ★ 验证：客服能看到客户在接管期间发的新消息。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

// 造一条并接管
const S = 'rt-' + Math.floor(Math.random() * 100000)
await fetch(BASE + '/api/web/chat', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: S, text: '能报个价吗' }),
})
await wait(15000)
const ho = await jget('/api/handover')
const it = ho.items.find((x) => x.user_id === S)
console.log('  造了一条:', it.id, S)
await jpost(`/api/handover/${it.id}/take`, { by: '张工' })
await jpost(`/api/handover/${it.id}/reply`, { text: '我看下图纸', by: '张工', send: true })
await wait(3000)

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_rt', port: 10030, windowSize: '1420,1000',
})
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(8000)
  await cdp.eval(`switchTab('ho')`)
  await wait(3000)

  // 点开我们造的那条
  await cdp.eval(`(async () => {
    const items = [...document.querySelectorAll('.hoitem')];
    for (let i = 0; i < items.length; i++) {
      items[i].click();
      await new Promise(r => setTimeout(r, 2200));
      if ((document.querySelector('#hoDetail')||{innerText:''}).innerText.includes(${JSON.stringify(S)})) return 1;
    }
    return 0;
  })()`)
  await wait(3000)

  const before = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap');
    return { rows: w ? w.querySelectorAll('.tl-row').length : 0,
             text: (document.querySelector('#hoDetail')||{innerText:''}).slice(-70) };
  })()`)
  console.log('  接管后（客户还没发新消息）:', before.rows, '行')

  // ★ 客户现在发两句 —— 客服页**不动**，看它会不会自己刷出来
  await fetch(BASE + '/api/web/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: S, text: '什么意思' }),
  })
  await wait(15000)
  await fetch(BASE + '/api/web/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: S, text: '？' }),
  })
  await wait(15000)

  // 等服务端角标 + 等自动刷新（20 秒一轮）
  console.log('  等自动刷新（最多 30 秒）…')
  let found = false
  for (let i = 0; i < 12; i++) {
    await wait(3000)
    const now = await cdp.eval(`(() => {
      const w = document.querySelector('.tlwrap');
      const txt = (document.querySelector('#hoDetail')||{innerText:''});
      return { rows: w ? w.querySelectorAll('.tl-row').length : 0,
               has1: txt.includes('什么意思'),
               has2: txt.includes('？') || txt.includes('?') };
    })()`)
    if (now.has1) {
      console.log(`  ★ 第 ${(i+1)*3} 秒：客户的新消息出现了（${now.rows} 行）`)
      found = true
      break
    }
  }
  console.log('  ★ 客服能不能自动看到新消息:', found ? '✅ 能' : '❌ 不能')

  const after = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap');
    const d = document.querySelector('#hoDetail');
    return {
      rows: w ? w.querySelectorAll('.tl-row').length : 0,
      tail: (d||{innerText:''}).replace(/\\s+/g,' ').slice(-100),
      scrolledToBottom: w ? (w.scrollHeight - w.scrollTop - w.clientHeight < 40) : null,
      leftBadge: (document.querySelector('#holist')||{innerText:''}).includes('又说了'),
    };
  })()`)
  console.log('  最终:', JSON.stringify(after))

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_realtime.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_realtime.png')
} finally { await close() }
