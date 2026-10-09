/** 截新单页控制台 + 模拟官网。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
fs.mkdirSync(OUT, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9570, windowSize: '1280,1000',
})

const wait = (ms) => new Promise((r) => setTimeout(r, ms))
async function shot(name, w, h) {
  const r = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: w, height: h, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\${name}.png`, Buffer.from(r.data, 'base64'))
  console.log('  →', name + '.png')
}

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // ── 单页控制台 ──
  console.log('① 单页控制台')
  await cdp.send('Page.navigate', { url: BASE + '/' })
  await wait(3500)
  const info = await cdp.eval(`(() => ({
    tabs: [...document.querySelectorAll('#nav button')].map(b=>b.textContent),
    stat: document.querySelector('#stat').innerText,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('   标签页:', info.tabs.join(' / '))
  console.log('   顶栏:', info.stat)
  console.log('   横向溢出:', info.overflow, 'px')

  // 问一句
  await cdp.eval(`(() => {
    const i = document.querySelector('#q')
    const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    s.call(i,'打样要多久'); i.dispatchEvent(new Event('input',{bubbles:true}))
    document.querySelector('#go').click(); return true })()`)
  await wait(20000)
  await shot('console_try', 1280, 1000)

  // 知识库页
  await cdp.eval(`document.querySelector('#nav button[data-k=kb]').click()`)
  await wait(2500)
  await cdp.eval(`document.querySelector('#pasteTitle').value='差旅住宿标准'
    ; document.querySelector('#pasteBody').value='一线城市住宿费每晚不超过500元，其他城市不超过350元。需提供发票。'
    ; true`)
  await wait(500)
  await shot('console_kb', 1280, 1000)

  // 模型页
  await cdp.eval(`document.querySelector('#nav button[data-k=model]').click()`)
  await wait(2000)
  await cdp.eval(`(async()=>{ await fetch('/api/settings/model/test',{method:'POST'}) ; return 1 })()`)
  await cdp.eval(`document.querySelector('button.ghost').click()`)
  await wait(6000)
  await shot('console_model', 1280, 780)

  // ── 模拟官网 ──
  console.log('② 模拟官网')
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  const s = await cdp.eval(`(() => ({
    title: document.title,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    h: document.body.scrollHeight,
    hasFab: !!document.querySelector('#fab'),
    sections: [...document.querySelectorAll('section')].length,
  }))()`)
  console.log('   标题:', s.title, '| 溢出:', s.overflow, '| 页高:', s.h, '| 挂件:', s.hasFab ? 'OK' : '缺')
  await shot('site_full', 1280, 2400)

  // 打开挂件问一句
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1200)
  await cdp.eval(`(() => {
    const i = document.querySelector('#q2')
    const st = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    st.call(i,'最小起订量多少'); i.dispatchEvent(new Event('input',{bubbles:true}))
    document.querySelector('#go2').click(); return true })()`)
  await wait(20000)
  const b = await cdp.eval(`[...document.querySelectorAll('#msgs2 .m2')].map(e=>e.className.replace('m2 ','')+': '+e.innerText.replace(/\\s+/g,' ').slice(0,60))`)
  for (const x of b) console.log('   ', x)
  await shot('site_chat', 1280, 1000)

  // 手机看官网
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1200)
  const m = await cdp.eval(`(() => {
    const p=document.querySelector('#panel2').getBoundingClientRect()
    return { overflow: document.documentElement.scrollWidth-document.documentElement.clientWidth,
             panel: {l:Math.round(p.left), r:Math.round(p.right)}, vw: window.innerWidth }
  })()`)
  console.log('   手机 溢出:', m.overflow, '| 面板', m.panel, '| 视口', m.vw)
  const r2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\site_mobile.png`, Buffer.from(r2.data, 'base64'))
  console.log('   → site_mobile.png')
} finally { await close() }
