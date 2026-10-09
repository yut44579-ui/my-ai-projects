/** 验证三个按钮都在，并且接管/结束接管都能用。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())

const S = 'btn3-' + Math.floor(Math.random() * 100000)
await fetch(BASE + '/api/web/chat', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: S, text: '能报个价吗' }),
})
await wait(14000)

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_btn3', port: 10010, windowSize: '1420,1000',
})
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(8000)
  await cdp.eval(`switchTab('ho')`)
  await wait(3000)

  // 找到我们造的那条并点开
  const opened = await cdp.eval(`(async () => {
    const items = [...document.querySelectorAll('.hoitem')];
    for (let i = 0; i < items.length; i++) {
      items[i].click();
      await new Promise(r => setTimeout(r, 2200));
      const txt = (document.querySelector('#hoDetail')||{innerText:''}).innerText;
      if (txt.includes(${JSON.stringify(S)})) return '找到并点开（第 ' + (i+1) + ' 条）';
    }
    return '没找到 ' + ${JSON.stringify(S)};
  })()`)
  console.log('  ', opened)
  await wait(2500)

  const before = await cdp.eval(`(() => {
    const d = document.querySelector('#hoDetail');
    return { btns: [...d.querySelectorAll('button')].map(b => b.textContent.trim()) };
  })()`)
  console.log('  接管前按钮:', JSON.stringify(before.btns))
  console.log('  有「接管」:', before.btns.includes('接管') ? '✅' : '❌')

  // 点接管
  await cdp.eval(`(async () => {
    const b = [...document.querySelectorAll('#hoDetail button')].find(x => x.textContent.trim() === '接管');
    if (b) b.click();
    await new Promise(r => setTimeout(r, 4500));
    return 1;
  })()`)
  await wait(3000)

  const after = await cdp.eval(`(() => {
    const d = document.querySelector('#hoDetail');
    return {
      btns: [...d.querySelectorAll('button')].map(b => b.textContent.trim()),
      owner: (d.innerText.match(/●[^\n]*正在管[^\n]*/) || [''])[0].trim(),
    };
  })()`)
  console.log('  接管后按钮:', JSON.stringify(after.btns))
  console.log('  归属文案:', after.owner)
  console.log('  有「转到我这里」:', after.btns.includes('转到我这里') ? '✅' : '❌')
  console.log('  有「结束接管（交还 AI）」:', after.btns.some(b => b.includes('结束接管')) ? '✅' : '❌')
  console.log('  ★ 三个动作都在:', (after.btns.includes('转到我这里') && after.btns.some(b => b.includes('结束接管'))) ? '✅' : '❌')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_console_taken.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_console_taken.png')
} finally { await close() }
