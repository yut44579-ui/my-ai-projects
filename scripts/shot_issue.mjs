/** 验证问题上报的完整闭环（报事侧 → 处理侧 → 确认）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9680, windowSize: '1400,1050',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

// 先清掉上次的测试数据，只留真实现场
try {
  const r = await fetch(BASE + '/api/issues?status=all')
  const d = await r.json()
  console.log('  已有问题:', d.items.length, '条')
} catch (e) { console.log('  (取列表失败)') }

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  // ── ① 先去官网问一句（制造真实现场）──
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
  console.log('  ① 官网问完了，现场已产生')

  // ── ② 报事侧：在实时页点「这条不对」──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(5500)
  const has = await cdp.eval(`document.querySelectorAll('.tur a').length`)
  console.log('  ② 实时页每条问答旁边的报错入口数:', has)
  // 点第一条「这条不对」
  await cdp.eval(`(() => { const a = document.querySelector('.tur a'); if (a) a.click(); return true })()`)
  await wait(2500)
  const modal = await cdp.eval(`(() => ({
    open: document.querySelector('#reportModal').classList.contains('on'),
    kinds: document.querySelectorAll('#kindList .kind').length,
    sevs: document.querySelectorAll('#sevList .sev').length,
    attach: document.querySelector('#isAttach').innerText.replace(/\\s+/g,' ').slice(0,100),
    title: document.querySelector('#isTitle').value,
  }))()`)
  console.log('  弹窗打开:', modal.open, '| 类型', modal.kinds, '个 | 影响', modal.sevs, '档')
  console.log('  预填的描述:', modal.title)
  console.log('  提示:', modal.attach)

  // 填表提交
  await cdp.eval(`(() => {
    document.querySelectorAll('#kindList .kind')[0].click()
    document.querySelectorAll('#sevList .sev')[2].click()
    document.querySelector('#isTitle').value = '这个答案和售后服务承诺对不上'
    document.querySelector('#isDetail').value = '《售后服务承诺》写的是 2%，它答的是 3%'
    document.querySelector('#isReporter').value = '小李'
    document.querySelector('#isRole').value = '客服'
    return true })()`)
  await wait(600)
  await cdp.eval(`document.querySelector('.mbox button').click()`)
  await wait(3500)
  const submitted = await cdp.eval(`document.querySelector('#isMsg').innerText.replace(/\\s+/g,' ')`)
  console.log('  提交结果:', submitted)

  // ── ③ 处理侧 ──
  await cdp.send('Page.navigate', { url: BASE + '/index.html#issue' })
  await wait(5000)
  const list = await cdp.eval(`(() => ({
    badge: (document.querySelector('#navBadge2')||{}).textContent,
    stats: document.querySelector('#istats').innerText.replace(/\\s+/g,' '),
    items: [...document.querySelectorAll('.isum')].slice(0,3).map(e=>e.innerText.replace(/\\s+/g,' ').slice(0,80)),
  }))()`)
  console.log('\n  ③ 处理侧角标:', list.badge)
  console.log('     ', list.stats)
  for (const x of list.items) console.log('      ●', x)

  // 点开详情，看自动附的现场
  await cdp.eval(`document.querySelector('.isum').click()`)
  await wait(3000)
  const det = await cdp.eval(`(() => {
    const d = document.querySelector('#idetail')
    return d ? d.innerText.replace(/\\s+/g,' ').slice(0, 700) : '没打开'
  })()`)
  console.log('\n  ④ 技术看到的详情:')
  console.log('     ', det)

  // 接手 → 提交方案
  await cdp.eval(`(() => { const b=[...document.querySelectorAll('#idetail button')].find(x=>x.textContent.includes('我来处理')); if(b) b.click(); return true })()`)
  await wait(3500)
  await cdp.eval(`document.querySelector('.isum').click()`)
  await wait(3000)
  await cdp.eval(`(() => {
    document.querySelector('#iRes').value = '已把《售后服务承诺》里的 3% 改成 2%，并重新同步了知识库'
    document.querySelector('#iResKind').value = 'kb_wrong'
    return true })()`)
  await cdp.eval(`(() => { const b=[...document.querySelectorAll('#idetail button')].find(x=>x.textContent.includes('提交解决方案')); if(b) b.click(); return true })()`)
  await wait(4000)
  const after = await cdp.eval(`document.querySelector('#istats').innerText.replace(/\\s+/g,' ')`)
  console.log('\n  ⑤ 技术提交后:', after)

  // ── ⑥ 报的人确认 ──
  await cdp.eval(`document.querySelector('.isum').click()`)
  await wait(3000)
  const ackbar = await cdp.eval(`(() => {
    const a = document.querySelector('.aibar')
    return a ? a.innerText.replace(/\\s+/g,' ').slice(0,160) : '没有确认栏'
  })()`)
  console.log('  ⑥ 报的人看到的确认栏:', ackbar)
  await cdp.eval(`(() => { const b=[...document.querySelectorAll('#idetail button')].find(x=>x.textContent.includes('还没好')); if(b) b.click(); return true })()`)
  await wait(1200)
  // prompt 会被 CDP 阻塞，用 Page.handleJavaScriptDialog
  await cdp.send('Page.handleJavaScriptDialog', { accept: true, promptText: '我遇到的是另一个地方也不对' })
  await wait(3500)
  const back = await cdp.eval(`document.querySelector('#istats').innerText.replace(/\\s+/g,' ')`)
  console.log('  ⑦ 说"还没好"之后:', back)

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1400, height: 1050, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_issue.png`, Buffer.from(shot.data, 'base64'))
  console.log('\n  截图 → console_issue.png')

  // 报事侧弹窗截图
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(5000)
  await cdp.eval(`(() => { const a = document.querySelector('.tur a'); if (a) a.click(); return true })()`)
  await wait(2500)
  const s2 = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1400, height: 900, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console_report.png`, Buffer.from(s2.data, 'base64'))
  console.log('  截图 → console_report.png')
} finally { await close() }
