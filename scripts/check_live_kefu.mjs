/** 公网验证 AI客服：真问真答 + 手机视口。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
fs.mkdirSync(OUT, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9520,
  windowSize: '1200,900',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })

  console.log('① 公网打开挂件页')
  await cdp.send('Page.navigate', { url: BASE + '/widget.html' })
  await new Promise((r) => setTimeout(r, 5000))
  const ok = await cdp.eval(`(() => ({
    title: document.title,
    hasFab: !!document.querySelector('.fab'),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('   标题:', ok.title, '| 挂件按钮:', ok.hasFab ? '✅' : '❌', '| 溢出:', ok.overflow, 'px')

  console.log('\n② 真问一句（走公网 → nginx → 服务 → 模型）')
  await cdp.eval(`(() => { document.querySelector('.fab').click(); return true })()`)
  await new Promise((r) => setTimeout(r, 1000))
  const ask = async (q, ms) => {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(i, ${JSON.stringify(q)})
      i.dispatchEvent(new Event('input', { bubbles: true }))
      document.querySelector('#go').click()
      return true
    })()`)
    await new Promise((r) => setTimeout(r, ms))
  }
  await ask('上班时间是几点', 18000)
  await ask('你们能便宜点吗', 16000)

  const bubbles = await cdp.eval(`[...document.querySelectorAll('#msgs .m')].map((e) => e.className.replace('m ','') + ': ' + e.innerText.replace(/\\s+/g,' ').slice(0,72))`)
  for (const b of bubbles) console.log('   ', b)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1200, height: 850, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\live_desktop.png`, Buffer.from(shot.data, 'base64'))
  console.log('   截图 → live_desktop.png')

  // ── 手机视口 ──
  console.log('\n③ 手机视口（375×812）')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375, height: 812, deviceScaleFactor: 2, mobile: true,
  })
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })
  await cdp.send('Page.navigate', { url: BASE + '/widget.html' })
  await new Promise((r) => setTimeout(r, 5000))
  await cdp.eval(`(() => { document.querySelector('.fab').click(); return true })()`)
  await new Promise((r) => setTimeout(r, 1200))
  await ask('年假能休几天', 16000)
  const m = await cdp.eval(`(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panel: (() => { const p = document.querySelector('#panel'); const r = p.getBoundingClientRect();
      return { w: Math.round(r.width), right: Math.round(window.innerWidth - r.right), bottom: Math.round(window.innerHeight - r.bottom) } })(),
    bubbles: [...document.querySelectorAll('#msgs .m')].map((e) => e.className.replace('m ','') + ': ' + e.innerText.replace(/\\s+/g,' ').slice(0,60)),
  }))()`)
  console.log('   横向溢出:', m.overflow, 'px')
  console.log('   面板位置:', JSON.stringify(m.panel))
  for (const b of m.bubbles) console.log('   ', b)
  const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\live_mobile.png`, Buffer.from(shot2.data, 'base64'))
  console.log('   截图 → live_mobile.png')
} finally {
  await close()
}
