/** 手机窄屏适配检查：面板不能溢出屏幕。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9540,
  windowSize: '375,812',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375, height: 812, deviceScaleFactor: 2, mobile: true,
  })
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })
  await cdp.send('Page.navigate', { url: BASE + '/widget.html' })
  await new Promise((r) => setTimeout(r, 4500))
  await cdp.eval(`(() => { document.querySelector('.fab').click(); return true })()`)
  await new Promise((r) => setTimeout(r, 1200))

  const box = await cdp.eval(`(() => {
    const p = document.querySelector('#panel')
    const r = p.getBoundingClientRect()
    const f = document.querySelector('.fab').getBoundingClientRect()
    return {
      vw: window.innerWidth,
      panel: { left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width) },
      fab: { right: Math.round(window.innerWidth - f.right), bottom: Math.round(window.innerHeight - f.bottom) },
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }
  })()`)
  console.log('  视口宽度   :', box.vw)
  console.log('  面板 left  :', box.panel.left, box.panel.left < 0 ? '❌ 左边溢出' : '✅')
  console.log('  面板 right :', box.panel.right, box.panel.right > box.vw ? '❌ 右边溢出' : '✅')
  console.log('  面板宽度   :', box.panel.w)
  console.log('  横向溢出   :', box.overflow, 'px', box.overflow === 0 ? '✅' : '❌')

  // 问一句
  await cdp.eval(`(() => {
    const i = document.querySelector('#q')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(i, '住宿费一晚能报多少')
    i.dispatchEvent(new Event('input', { bubbles: true }))
    document.querySelector('#go').click()
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 18000))
  const bubbles = await cdp.eval(`[...document.querySelectorAll('#msgs .m')].map((e) => e.className.replace('m ','') + ': ' + e.innerText.replace(/\\s+/g,' ').slice(0,70))`)
  for (const b of bubbles) console.log('   ', b)

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\live_mobile.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 → live_mobile.png')
} finally {
  await close()
}
