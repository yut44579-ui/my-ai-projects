/** 问题处理台截图（干净版）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9720, windowSize: '1420,1100',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // 处理侧：打开第一条
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(7000)
  await cdp.eval(`(async () => { await loadIssues(); const f=document.querySelector('.isum'); if(f) await openIssue(JSON.parse(f.getAttribute('onclick').match(/\\((\d+)\\)/)[1])); return 1 })()`)
  await wait(4000)
  const det = await cdp.eval(`(() => {
    const d = document.querySelector('#idetail')
    return d ? d.innerText.replace(/\\s+/g,' ').slice(0, 400) : '没打开'
  })()`)
  console.log('  详情:', det)
  const s1 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1100, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_issue.png`, Buffer.from(s1.data, 'base64'))
  console.log('  → console_issue.png')

  // 报事侧弹窗
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(7000)
  await cdp.eval(`(() => { const a=document.querySelector('.tur a'); if(a) a.click(); return 1 })()`)
  await wait(3000)
  const m = await cdp.eval(`(() => ({
    open: document.querySelector('#reportModal').classList.contains('on'),
    kinds: document.querySelectorAll('#kindList .kind').length,
    attach: document.querySelector('#isAttach').innerText.replace(/\\s+/g,' ').slice(0,90),
  }))()`)
  console.log('  弹窗:', m.open ? '打开' : '没开', '| 类型', m.kinds, '个')
  console.log('  提示:', m.attach)
  const s2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_report.png`, Buffer.from(s2.data, 'base64'))
  console.log('  → console_report.png')

  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(6000)
  const mo = await cdp.eval(`document.documentElement.scrollWidth - document.documentElement.clientWidth`)
  console.log('  手机溢出:', mo, 'px')
  const s3 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\console_issue_mobile.png`, Buffer.from(s3.data, 'base64'))
  console.log('  → console_issue_mobile.png')
} finally { await close() }
