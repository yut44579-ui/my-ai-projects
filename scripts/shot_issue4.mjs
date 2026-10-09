/** 问题处理台截图（用 waitFor 等元素就绪）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9740, windowSize: '1420,1100',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // ── 处理侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await cdp.waitFor(`document.querySelector('.isum')`, { timeout: 20000, label: '问题列表' })
  await cdp.eval(`(() => { document.querySelector('.isum').click(); return 1 })()`)
  await cdp.waitFor(`document.querySelector('#idetail')`, { timeout: 15000, label: '问题详情' })
  await wait(2000)
  const det = await cdp.eval(`(() => {
    const d = document.querySelector('#idetail')
    return d ? d.innerText.replace(/\\s+/g,' ').slice(0, 340) : '没打开'
  })()`)
  console.log('  ★ 技术看到的详情:')
  console.log('   ', det)
  const s1 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1100, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_issue.png`, Buffer.from(s1.data, 'base64'))
  console.log('  → console_issue.png')

  // ── 报事侧弹窗 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await cdp.waitFor(`document.querySelector('#reportbtn')`, { timeout: 20000, label: '报错按钮' })
  await cdp.eval(`(() => { document.querySelector('#reportbtn').click(); return 1 })()`)
  await cdp.waitFor(`document.querySelector('#reportModal.on')`, { timeout: 10000, label: '上报弹窗' })
  await wait(800)
  const m = await cdp.eval(`(() => ({
    kinds: document.querySelectorAll('#kindList .kind').length,
    sevs: document.querySelectorAll('#sevList .sev').length,
    title: document.querySelector('#isTitle').value,
    attach: document.querySelector('#isAttach').innerText.replace(/\\s+/g,' ').slice(0,96),
  }))()`)
  console.log('\n  报事弹窗：类型', m.kinds, '个，影响', m.sevs, '档，预填:', m.title)
  console.log('  提示:', m.attach)
  const s2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_report.png`, Buffer.from(s2.data, 'base64'))
  console.log('  → console_report.png')

  // ── 手机 ──
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await cdp.waitFor(`document.querySelector('.isum')`, { timeout: 20000, label: '手机问题列表' })
  await wait(1500)
  const mo = await cdp.eval(`document.documentElement.scrollWidth - document.documentElement.clientWidth`)
  console.log('  手机溢出:', mo, 'px')
  const s3 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\console_issue_mobile.png`, Buffer.from(s3.data, 'base64'))
  console.log('  → console_issue_mobile.png')
} finally { await close() }
