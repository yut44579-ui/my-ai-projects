/** 给作品集拍 AI客服 的一组截图（1600×1000，和现有项目规格一致）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\处理qa\\portfolio_shots'
fs.mkdirSync(OUT, { recursive: true })
const W = 1600, H = 1000
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())
const jget = (p) => fetch(BASE + p).then((r) => r.json())

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9870, windowSize: `${W},${H}`,
})

async function shot(name) {
  const r = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: W, height: H, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\${name}.png`, Buffer.from(r.data, 'base64'))
  console.log('  →', name + '.png')
}

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (x) => errs.push('EXC ' + (x.exceptionDetails?.exception?.description || '').slice(0,200)))
  cdp.on('Runtime.consoleAPICalled', (x) => { if (x.type==='error') errs.push('ERR ' + JSON.stringify(x.args?.map(a=>a.value)).slice(0,180)) })

  // ── 先造一批好看的会话 ──
  console.log('造会话…')
  const S1 = 'demo-show'
  for (const q of ['打样要多久', '有哪些资质认证', '最小起订量多少', '精度能做到多少']) {
    await jpost('/api/web/chat', { session: S1, text: q })
    await wait(12000)
  }
  const S2 = 'demo-price'
  await jpost('/api/web/chat', { session: S2, text: '能报个价吗' })
  await wait(12000)
  // 让 S1 也来一条转人工（这样人工页有东西看）
  await jpost('/api/web/chat', { session: S1, text: '你们能便宜点吗' })
  await wait(13000)

  // ── ① 模拟官网 + 挂件 ──
  console.log('① 模拟官网')
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(4000)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1500)
  await wait(500)
  await shot('kefu-site')

  // ── ② 转人工的转圈 ──
  console.log('② 转人工转圈')
  await cdp.eval(`(() => {
    const i = document.querySelector('#q2')
    const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    s.call(i,'能不能开专票'); i.dispatchEvent(new Event('input',{bubbles:true}))
    document.querySelector('#go2').click(); return 1 })()`)
  await wait(14000)
  await shot('kefu-waiting')

  // ── ③ 客服台 · 实时监控 ──
  console.log('③ 实时监控')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#live' })
  await wait(6000)
  await cdp.eval(`switchTab('live')`)
  await wait(3000)
  await shot('kefu-live')

  // ── ④ 人工工作台（完整会话记录）──
  console.log('④ 人工工作台')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(6500)
  await cdp.eval(`switchTab('ho')`)
  await wait(4000)
  const hstate = await cdp.eval(`(() => ({
    tab: typeof curTab !== 'undefined' ? curTab : '?',
    items: document.querySelectorAll('.hoitem').length,
    hoCur: (typeof hoCur !== 'undefined' && hoCur) ? hoCur.id : null,
    hasWrap: !!document.querySelector('.tlwrap'),
    detail: (document.querySelector('#hoDetail') || { innerText: '' }).innerText.slice(0, 60),
  }))()`)
  console.log('     状态:', JSON.stringify(hstate))
  if (!hstate.hasWrap) {
    // 兜底：手动选一条
    await cdp.eval(`(async () => { await loadHandover(); const f=document.querySelector('.hoitem'); if(f) f.click(); return 1 })()`)
    await wait(5000)
  }
  await shot('kefu-handover')

  // ── ⑤ 技术后台 · 问题处理台（先报一个问题）──
  console.log('⑤ 问题处理台')
  const ho = await jget('/api/handover')
  const it = ho.items[0]
  await jpost('/api/issues', {
    title: '这条答案和售后服务承诺对不上', kind: 'wrong_answer', severity: 'high',
    detail: '《售后服务承诺》表里写的是 2%，它答的是 3%', reporter: '小李',
    reporter_role: '客服', qa_log_id: 1, page: '试玩',
  })
  await cdp.send('Page.navigate', { url: BASE + '/dev.html#issue' })
  await cdp.waitFor(`document.querySelector('.isum')`, { timeout: 25000, label: '问题列表' })
  await wait(2500)
  await cdp.eval(`(() => { const f=document.querySelector('.isum'); if(f) f.click(); return 1 })()`)
  await cdp.waitFor(`document.querySelector('#idetail')`, { timeout: 15000, label: '详情' })
  await wait(2500)
  await shot('kefu-issue')

  // ── ⑥ 渠道配置 ──
  console.log('⑥ 渠道')
  await cdp.send('Page.navigate', { url: BASE + '/dev.html#ch' })
  await wait(6000)
  await shot('kefu-channels')

  // ── ⑦ 模型配置 ──
  console.log('⑦ 模型')
  await cdp.send('Page.navigate', { url: BASE + '/dev.html#model' })
  await wait(5500)
  await shot('kefu-model')

  // ── ⑧ 客服侧的"我的问题"（报的人看进度）──
  console.log('⑧ 我的问题')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#mytodo' })
  await wait(6000)
  await cdp.eval(`(() => { const e=document.querySelector('#mineWho2'); e.value='小李'; loadMyTodo(); return 1 })()`)
  await wait(3000)
  await shot('kefu-mytodo')

  console.log('完成')
} finally { await close() }
