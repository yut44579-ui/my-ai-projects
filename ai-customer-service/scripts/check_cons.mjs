/** 查客服台人工页有没有 JS 报错，以及"结束接管"按钮在不在。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_cons', port: 9990, windowSize: '1420,1000',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (x) =>
    errs.push((x.exceptionDetails?.exception?.description || x.exceptionDetails?.text || '').slice(0, 200)))

  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(8000)
  console.log('  页面加载后 JS 错误:', errs.length ? errs.slice(0, 3) : '（无）')

  const st1 = await cdp.eval(`(() => ({
    tab: typeof curTab !== 'undefined' ? curTab : '?',
    items: document.querySelectorAll('.hoitem').length,
    hasDetail: !!document.querySelector('#hoDetail'),
    text: (document.querySelector('#hoDetail')||{innerText:''}).innerText.slice(0, 60),
  }))()`)
  console.log('  状态:', JSON.stringify(st1))

  // 选一条 taken 的
  const picked = await cdp.eval(`(() => {
    const items = [...document.querySelectorAll('.hoitem')];
    // 找一条带"接管"痕迹的；没有就点第一条
    for (let i = 0; i < items.length; i++) { items[i].click(); }
    return { clicked: items.length };
  })()`)
  await wait(4000)

  const st2 = await cdp.eval(`(() => {
    const d = document.querySelector('#hoDetail');
    return {
      exists: !!d,
      btns: d ? [...d.querySelectorAll('button')].map(b => b.textContent.trim()) : [],
      inner: d ? d.innerText.replace(/\\s+/g,' ').slice(0, 150) : '',
    };
  })()`)
  console.log('  详情区:', JSON.stringify(st2.btns))
  console.log('  内容:', st2.inner)
  console.log('  JS 错误:', errs.length ? errs.slice(0, 3) : '（无）')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\hs_console.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → hs_console.png')
} finally { await close() }
