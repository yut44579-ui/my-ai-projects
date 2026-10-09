/** 验证两个角色页面。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9820, windowSize: '1420,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // ── ① 客服侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html' })
  await wait(6000)
  const a = await cdp.eval(`(() => ({
    role: document.body.dataset.role,
    title: document.querySelector('h1').textContent,
    tabs: [...document.querySelectorAll('#nav button')].map(b=>b.textContent.replace(/\\s+/g,'')),
    switchText: (document.querySelector('#roleSwitch')||{}).textContent,
    switchHref: (document.querySelector('#roleSwitch')||{}).href,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('  ① 客服侧：')
  console.log('     角色:', a.role, '| 标题:', a.title)
  console.log('     标签:', a.tabs.join(' / '))
  console.log('     右上入口:', a.switchText, '→', (a.switchHref||'').split('/').pop())
  console.log('     溢出:', a.overflow, 'px')

  // 看「我的问题」
  await cdp.eval(`(() => { const b=[...document.querySelectorAll('#nav button')].find(x=>x.textContent.includes('我的问题')); if(b) b.click(); return 1 })()`)
  await wait(2500)
  const mt = await cdp.eval(`(() => ({
    on: document.querySelector('#p-mytodo').classList.contains('on'),
    hint: (document.querySelector('#mytodoList')||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,60),
    hasReportBtn: !!document.querySelector('#reportbtn'),
    hasDevStuff: !!document.querySelector('#p-model') && document.querySelector('#p-model').classList.contains('on'),
  }))()`)
  console.log('     我的问题页:', mt.on ? '能打开' : '打不开', '|', mt.hint)
  console.log('     报问题按钮在:', mt.hasReportBtn ? '在' : '不在')
  const s1 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\role_agent.png`, Buffer.from(s1.data, 'base64'))
  console.log('     截图 → role_agent.png')

  // ── ② 技术侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/dev.html' })
  await wait(6500)
  const d = await cdp.eval(`(() => ({
    role: document.body.dataset.role,
    title: document.querySelector('h1').textContent,
    tabs: [...document.querySelectorAll('#nav button')].map(b=>b.textContent.replace(/\\s+/g,'')),
    switchText: (document.querySelector('#roleSwitch')||{}).textContent,
    switchHref: (document.querySelector('#roleSwitch')||{}).href,
    onIssue: document.querySelector('#p-issue') ? document.querySelector('#p-issue').classList.contains('on') : false,
    stats: (document.querySelector('#istats')||{innerText:''}).innerText.replace(/\\s+/g,' '),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }))()`)
  console.log('\n  ② 技术侧：')
  console.log('     角色:', d.role, '| 标题:', d.title)
  console.log('     标签:', d.tabs.join(' / '))
  console.log('     右上入口:', d.switchText, '→', (d.switchHref||'').split('/').pop())
  console.log('     默认落在问题处理台:', d.onIssue ? '是' : '否')
  console.log('     统计:', d.stats)
  console.log('     溢出:', d.overflow, 'px')

  const s2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\role_dev.png`, Buffer.from(s2.data, 'base64'))
  console.log('     截图 → role_dev.png')

  // 手机
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/dev.html' })
  await wait(6000)
  const m = await cdp.eval(`(() => ({
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    tabsVisible: [...document.querySelectorAll('#nav button')].filter(b => {
      const r = b.getBoundingClientRect(); return r.width > 0 }).length,
  }))()`)
  console.log('\n  ③ 手机上的技术侧：溢出', m.overflow, 'px | 可见标签', m.tabsVisible, '个')
} finally { await close() }
