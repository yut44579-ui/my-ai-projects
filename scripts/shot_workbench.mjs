/** 截人工工作台。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const OUT = 'D:\\ai-kefu\\docs\\screenshots'
fs.mkdirSync(OUT, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9480,
  windowSize: '1500,1000',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:8600/workbench.html' })
  await new Promise((r) => setTimeout(r, 4000))
  // 点第一条
  await cdp.eval(`(() => { const q = document.querySelector('.qi'); if (q) q.click(); return true })()`)
  await new Promise((r) => setTimeout(r, 1500))

  const info = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      queue: document.querySelectorAll('.qi').length,
      hasDraft: (document.querySelector('#text') || {}).value ? true : false,
      draft: ((document.querySelector('#text') || {}).value || '').slice(0, 60),
      sideHasWhy: t.includes('为什么没答'),
      sideHasHits: t.includes('命中的资料'),
      text: t.replace(/\\s+/g, ' ').slice(0, 420),
    }
  })()`)
  console.log('  队列条数  :', info.queue)
  console.log('  有草稿    :', info.hasDraft, '|', info.draft)
  console.log('  侧栏有原因:', info.sideHasWhy ? '✅' : '❌')
  console.log('  侧栏有命中:', info.sideHasHits ? '✅' : '❌')
  console.log('  正文      :', info.text.slice(0, 300))

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1500, height: 900, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\workbench.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 →', `${OUT}\\workbench.png`)
} finally {
  await close()
}
