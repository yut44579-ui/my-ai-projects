/** 问题上报：完整闭环 + 截图（干净版）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9690, windowSize: '1400,1060',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(b || {}),
}).then((r) => r.json())

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // ── ① 制造真实现场：在官网问两句 ──
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(800)
  for (const q of ['打样要多久', '不良率超标怎么办']) {
    await cdp.eval(`(() => {
      const i = document.querySelector('#q2')
      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      s.call(i, ${JSON.stringify(q)}); i.dispatchEvent(new Event('input',{bubbles:true}))
      document.querySelector('#go2').click(); return true })()`)
    await wait(15000)
  }
  console.log('  ① 官网问了 2 句，现场已产生')

  // ── ② 报事侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(6000)
  const n = await cdp.eval(`document.querySelectorAll('.tur a').length`)
  console.log('  ② 实时页可报的问答数:', n)
  await cdp.eval(`(() => { const a = document.querySelector('.tur a'); if (a) a.click(); return true })()`)
  await wait(2500)
  const m = await cdp.eval(`(() => ({
    open: document.querySelector('#reportModal').classList.contains('on'),
    kinds: document.querySelectorAll('#kindList .kind').length,
    sevs: document.querySelectorAll('#sevList .sev').length,
    title: document.querySelector('#isTitle').value,
  }))()`)
  console.log('     弹窗:', m.open ? '打开' : '没开', '| 类型', m.kinds, '个 | 影响', m.sevs, '档 | 预填:', m.title)

  await cdp.eval(`(() => {
    document.querySelectorAll('#kindList .kind')[0].click()
    document.querySelectorAll('#sevList .sev')[2].click()
    document.querySelector('#isTitle').value = '这条答案和售后服务承诺对不上'
    document.querySelector('#isDetail').value = '《售后服务承诺》表里写的是 2%，它答的是 3%'
    document.querySelector('#isReporter').value = '小李'
    document.querySelector('#isRole').value = '客服'
    return true })()`)
  await wait(500)
  await cdp.eval(`document.querySelector('.mbox button').click()`)
  await wait(3500)
  console.log('     提交结果:', await cdp.eval(`document.querySelector('#isMsg').innerText.replace(/\\s+/g,' ')`))

  // ── ③ 处理侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(5000)
  const l = await cdp.eval(`(() => ({
    badge: (document.querySelector('#navBadge2')||{}).textContent,
    stats: document.querySelector('#istats').innerText.replace(/\\s+/g,' '),
    first: (document.querySelector('.isum')||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,90),
  }))()`)
  console.log('\n  ③ 处理侧角标:', l.badge)
  console.log('     ', l.stats)
  console.log('      第一条:', l.first)

  await cdp.eval(`document.querySelector('.isum').click()`)
  await wait(3000)
  const d = await cdp.eval(`(() => {
    const x = document.querySelector('#idetail')
    return x ? x.innerText.replace(/\\s+/g,' ').slice(0, 560) : '没打开'
  })()`)
  console.log('\n  ④ ★ 技术看到的（报的人一个字都没填这些）:')
  console.log('     ', d)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1400, height: 1060, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_issue.png`, Buffer.from(shot.data, 'base64'))
  console.log('\n  截图 → console_issue.png')

  // ── ⑤ 走 API 完成剩下的闭环（避开 prompt 弹窗）──
  const open = await jget('/api/issues?status=open')
  const id = open.items[0].id
  console.log('\n  ⑤ 闭环（走接口，避开浏览器 prompt）')
  console.log('     接手  :', JSON.stringify(await jpost(`/api/issues/${id}/take`, { by: '张工' })))
  const res = await jpost(`/api/issues/${id}/resolve`, {
    resolution: '已把《售后服务承诺》表格里的 3% 改成 2%，并重新同步了知识库',
    kind: 'kb_wrong', by: '张工',
  })
  console.log('     提交  :', JSON.stringify(res))
  const st1 = await jget('/api/issues')
  console.log('     状态  :', JSON.stringify(st1.counts))
  const no = await jpost(`/api/issues/${id}/ack`, { ok: false, note: '我遇到的是另一处也不对' })
  console.log('     说还没好:', JSON.stringify(no))
  const st2 = await jget('/api/issues')
  console.log('     状态  :', JSON.stringify(st2.counts))
  const yes = await jpost(`/api/issues/${id}/ack`, { ok: true, note: '好了谢谢' })
  console.log('     说好了  :', JSON.stringify(yes))
  const st3 = await jget('/api/issues')
  console.log('     最终  :', JSON.stringify(st3.counts))

  // 报的人视角截图（resolved 状态的确认栏）
  const me = await jget('/api/issues?mine=' + encodeURIComponent('小李'))
  console.log('\n  ⑥ 报的人（小李）能看到自己的问题:', me.items.length, '条，状态',
    me.items.map((x) => x.status_label).join('/'))

  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(4500)
  await cdp.eval(`(() => { document.querySelector('#mineWho').value='小李'; loadIssues(); return true })()`)
  await wait(3000)
  const mine = await cdp.eval(`document.querySelector('#imine').innerText.replace(/\\s+/g,' ').slice(0,200)`)
  console.log('     界面上:', mine)
  const s3 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1400, height: 1060, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_issue_done.png`, Buffer.from(s3.data, 'base64'))
  console.log('  截图 → console_issue_done.png')

  // 手机
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(5000)
  const mo = await cdp.eval(`document.documentElement.scrollWidth - document.documentElement.clientWidth`)
  console.log('  手机溢出:', mo, 'px')
} finally { await close() }
