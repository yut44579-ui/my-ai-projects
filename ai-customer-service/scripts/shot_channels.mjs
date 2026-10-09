/** 截「渠道」页 + 三档效果。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9620, windowSize: '1320,1050',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // 渠道页
  await cdp.send('Page.navigate', { url: BASE + '/#ch' })
  await wait(4500)
  const r = await cdp.eval(`(() => ({
    tabs: [...document.querySelectorAll('#nav button')].map(b=>b.textContent.replace(/\\s+/g,'')),
    cards: [...document.querySelectorAll('#chlist .card')].map(e => {
      const h = e.querySelector('h2'); const t = e.querySelector('.tag');
      const cb = e.querySelector('input[readonly]');
      return (h?h.textContent:'') + ' | ' + (t?t.textContent.trim():'') + ' | ' + (cb?cb.value.slice(0,58):'无回调');
    }),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('  标签页:', r.tabs.join(' / '))
  for (const c of r.cards) console.log('   ', c)
  console.log('  溢出:', r.overflow, 'px')

  const s = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1320, height: 1050, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_channels.png`, Buffer.from(s.data, 'base64'))
  console.log('  截图 → console_channels.png')

  // 三档效果页
  await cdp.send('Page.navigate', { url: BASE + '/#chat' })
  await wait(3500)
  for (const q of ['你是做什么的', '打样要多久', '你们能便宜点吗']) {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q')
      const st = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      st.call(i, ${JSON.stringify(q)}); i.dispatchEvent(new Event('input',{bubbles:true}))
      document.querySelector('#go').click(); return true })()`)
    await wait(17000)
  }
  const t = await cdp.eval(`(() => ({
    v: [...document.querySelectorAll('.verdict')].map(e=>e.innerText.replace(/\\s+/g,' ')),
    why: [...document.querySelectorAll('.why')].map(e=>e.innerText.replace(/\\s+/g,' ').slice(0,110)),
  }))()`)
  console.log('\n  三档判定:')
  for (let i = 0; i < t.v.length; i++) console.log('    ', t.v[i])
  console.log('  依据:')
  for (const w of t.why) console.log('    ', w)
  const s2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1320, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_tiers.png`, Buffer.from(s2.data, 'base64'))
  console.log('  截图 → console_tiers.png')
} finally { await close() }
