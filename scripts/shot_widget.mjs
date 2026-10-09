/** 截网页客服挂件（真跑，真问真答）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const OUT = 'D:\\ai-kefu\\docs\\screenshots'
fs.mkdirSync(OUT, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9500,
  windowSize: '1200,900',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:8600/widget.html' })
  await new Promise((r) => setTimeout(r, 3000))

  // 打开挂件
  await cdp.eval(`(() => { document.querySelector('.fab').click(); return true })()`)
  await new Promise((r) => setTimeout(r, 1200))

  // 问一句资料里有的
  const ask = async (q, waitMs) => {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(i, ${JSON.stringify(q)})
      i.dispatchEvent(new Event('input', { bubbles: true }))
      document.querySelector('#go').click()
      return true
    })()`)
    await new Promise((r) => setTimeout(r, waitMs))
  }

  await ask('住宿费一晚能报多少', 16000)
  await ask('请假怎么走流程', 16000)

  const info = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    const bubbles = [...document.querySelectorAll('#msgs .m')].map((e) => e.className.replace('m ', '') + ': ' + e.innerText.replace(/\\s+/g, ' ').slice(0, 70))
    return { panelOpen: !!document.querySelector('#panel.on'), bubbles, text: t.replace(/\\s+/g, ' ').slice(0, 300) }
  })()`)
  console.log('  挂件打开 :', info.panelOpen ? '✅' : '❌')
  console.log('  气泡:')
  for (const b of info.bubbles) console.log('    ', b)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1200, height: 900, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\widget.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 →', `${OUT}\\widget.png`)
} finally {
  await close()
}
