/** 检查「人工」标签在各屏幕宽度下能不能看到。 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9580, windowSize: '1280,900',
})

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  for (const [label, w, h] of [['桌面', 1280, 900], ['平板', 820, 1000], ['手机', 390, 844]]) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: w, height: h, deviceScaleFactor: w < 500 ? 2 : 1, mobile: w < 500,
    })
    await cdp.send('Page.navigate', { url: BASE + '/' })
    await wait(3000)
    const r = await cdp.eval(`(() => {
      const nav = document.querySelector('nav')
      const btns = [...nav.querySelectorAll('button')]
      const nr = nav.getBoundingClientRect()
      const out = btns.map(b => {
        const r = b.getBoundingClientRect()
        return { t: b.textContent, left: Math.round(r.left), right: Math.round(r.right),
                 visible: r.left >= nr.left - 1 && r.right <= nr.right + 1 }
      })
      return { navW: Math.round(nr.width), navScrollW: nav.scrollWidth,
               clientW: nav.clientWidth, scrollLeft: nav.scrollLeft,
               tabs: out, vw: window.innerWidth }
    })()`)
    console.log(`\n【${label} ${w}px】导航宽 ${r.navW}，内容宽 ${r.navScrollW}（超出 ${r.navScrollW - r.clientW}px）`)
    for (const t of r.tabs) {
      console.log(`   ${t.visible ? '可见' : '❌被挤出屏幕'}  ${t.t}  [${t.left}–${t.right}]`)
    }
  }

  // 人工标签里到底有没有东西
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(3000)
  await cdp.eval(`document.querySelector('#nav button[data-k=ho]').click()`)
  await wait(3000)
  const ho = await cdp.eval(`(() => ({
    count: document.querySelector('#hoCount').textContent,
    list: document.querySelector('#holist').innerText.replace(/\\s+/g,' ').slice(0,220),
  }))()`)
  console.log('\n【人工标签内容】待处理:', ho.count)
  console.log('  ', ho.list)
} finally { await close() }
