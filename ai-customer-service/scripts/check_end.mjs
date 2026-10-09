/** 验证 taken 状态显示「结束接管」按钮 + 点击后 AI 恢复。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

// 先造一条并接管
const S = 'endbtn-' + Math.floor(Math.random() * 100000)
await fetch(BASE + '/api/web/chat', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: S, text: '能报个价吗' }),
})
await wait(14000)
const ho = await jget('/api/handover')
const it = ho.items.find((x) => x.user_id === S)
console.log('  造了一条:', it.id, it.question)

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_end', port: 10000, windowSize: '1420,1000',
})
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(8000)
  await cdp.eval(`switchTab('ho')`)
  await wait(3000)

  // 点「接管」
  const took = await cdp.eval(`(async () => {
    const items = [...document.querySelectorAll('.hoitem')];
    for (let i = 0; i < items.length; i++) {
      items[i].click();
      await new Promise(r => setTimeout(r, 2500));
      const btn = [...document.querySelectorAll('#hoDetail button')].find(b => b.textContent.trim() === '接管');
      if (btn) { btn.click(); return '点了接管：第 ' + (i+1) + ' 条'; }
    }
    return '没找到可接管的';
  })()`)
  console.log(' ', took)
  await wait(4500)

  const after = await cdp.eval(`(() => {
    const d = document.querySelector('#hoDetail');
    return {
      btns: d ? [...d.querySelectorAll('button')].map(b => b.textContent.trim()) : [],
      takenMark: d ? d.innerText.includes('你正在管这个会话') : false,
    };
  })()`)
  console.log('  接管后的按钮:', JSON.stringify(after.btns))
  console.log('  有「你正在管这个会话」标记:', after.takenMark ? '✅' : '❌')
  console.log('  有「结束接管」按钮:', after.btns.some((b) => b.includes('结束接管')) ? '✅' : '❌')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_console_taken.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_console_taken.png')

  // ★ 点「结束接管」（confirm 会被自动接受）
  const ended = await cdp.eval(`(async () => {
    window.confirm = () => true;
    const btn = [...document.querySelectorAll('#hoDetail button')].find(b => b.textContent.includes('结束接管'));
    if (!btn) return '没找到按钮';
    btn.click();
    await new Promise(r => setTimeout(r, 4000));
    return (document.querySelector('#hoMsg')||{}).textContent || '（没有提示）';
  })()`)
  console.log('  点结束接管 →', ended)

  // 验证 AI 恢复
  const since = (await jget(`/api/web/poll?session=${S}&since=0`)).last
  await fetch(BASE + '/api/web/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: S, text: '打样要多久' }),
  })
  await wait(15000)
  const back = await jget(`/api/web/poll?session=${S}&since=${since}`)
  console.log('  结束后客户再问，新增:', back.messages.map((m) => m.kind + ':' + m.content.slice(0, 26)).join(' | ') || '（无）')
  console.log('  ★ AI 恢复了吗:', back.messages.some((m) => m.kind === 'ai') ? '✅ 恢复了' : '❌ 没恢复')
} finally { await close() }
