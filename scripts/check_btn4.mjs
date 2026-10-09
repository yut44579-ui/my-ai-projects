/** 简化版：只看接管后的按钮。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

const S = 'btn4-' + Math.floor(Math.random() * 100000)
await fetch(BASE + '/api/web/chat', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: S, text: '能报个价吗' }),
})
await wait(14000)

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_btn4', port: 10020, windowSize: '1420,1000',
})
try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(8000)
  await cdp.eval(`switchTab('ho')`)
  await wait(3000)

  // 点开第一条，点接管
  await cdp.eval(`(async () => {
    document.querySelector('.hoitem').click();
    await new Promise(r => setTimeout(r, 2500));
    const b = [...document.querySelectorAll('#hoDetail button')].find(x => x.textContent.trim() === '接管');
    if (b) b.click();
    return 1;
  })()`)
  await wait(6000)

  const after = await cdp.eval(`(() => {
    const d = document.querySelector('#hoDetail');
    if (!d) return { err: 'no detail' };
    const btns = [];
    const all = d.getElementsByTagName('button');
    for (let i = 0; i < all.length; i++) btns.push((all[i].textContent || '').trim());
    const txt = d.innerText || '';
    const line = txt.split('\\n').filter(function (s) { return s.indexOf('正在管') >= 0; })[0] || '(没有归属文案)';
    return { btns: btns, owner: line.trim() };
  })()`)
  console.log('  接管后:')
  console.log('    按钮:', JSON.stringify(after.btns))
  console.log('    归属:', after.owner)
  console.log('    有「转到我这里」:', (after.btns || []).some((b) => b.indexOf('转到我这里') >= 0) ? '✅' : '❌')
  console.log('    有「结束接管」:', (after.btns || []).some((b) => b.indexOf('结束接管') >= 0) ? '✅' : '❌')
  console.log('    有「发送」:', (after.btns || []).some((b) => b === '发送') ? '✅' : '❌')
  console.log('    有「报个问题」:', (after.btns || []).some((b) => b.indexOf('报个问题') >= 0) ? '✅' : '❌')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_console_taken.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_console_taken.png')
} finally { await close() }
