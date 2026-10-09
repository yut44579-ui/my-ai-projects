/** 验证：① 人工接管后 AI 完全沉默 ② 不再有"刷新"感。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9840, windowSize: '1420,1000',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const navs = []
  cdp.on('Page.frameNavigated', (p) => { if (!p.frame.parentId) navs.push('NAV') })

  // ── ① 人工接管后 AI 必须沉默 ──
  console.log('【①】人工接管后 AI 还答不答')
  const S = 'silent-demo-' + Math.floor(Math.random() * 10000)
  await fetch(BASE + '/api/web/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: S, text: '能报个价吗' }),
  })
  await wait(13000)
  let msgs = await jget('/api/web/poll?session=' + S + '&since=0')
  console.log('   转人工后收到:', msgs.messages.map((m) => m.kind + ':' + m.content.slice(0, 14)))

  const ho = await jget('/api/handover')
  const it = ho.items.find((x) => x.user_id === S)
  console.log('   人工接管…', JSON.stringify(await jpost(`/api/handover/${it.id}/take`, { by: '张工' })))
  await wait(2500)

  // 客户又发一句 —— AI 绝不该回
  const since = msgs.last
  await fetch(BASE + '/api/web/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: S, text: '666' }),
  })
  await wait(13000)
  const after = await jget(`/api/web/poll?session=${S}&since=${since}`)
  const aiSpoke = after.messages.filter((m) => m.kind === 'ai')
  console.log('   接管后客户发「666」，新增:', after.messages.map((m) => m.kind + ':' + m.content.slice(0, 16)))
  console.log('   ★ AI 有没有插话:', aiSpoke.length ? '❌ 插了：' + aiSpoke[0].content.slice(0, 30) : '✅ 完全沉默')

  // 客服回复
  console.log('   客服回复:', JSON.stringify(await jpost(`/api/handover/${it.id}/reply`,
    { text: '报价要工程师看过图纸才能定，你把零件类型和材料发我一下。', by: '张工', send: true })).slice(0, 60))
  await wait(4000)
  const final = await jget(`/api/web/poll?session=${S}&since=${after.last}`)
  console.log('   客服回复后:', final.messages.map((m) => m.kind + ':' + m.content.slice(0, 22)))

  // ── ② 挂件上验证视觉 ──
  console.log('\n【②】挂件上看起来怎样')
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1500)
  // 这个会话是刚才那个，改成看这个客户本身的会话没意义；直接看有没有"客服"标签
  const view = await cdp.eval(`(() => {
    const m = document.querySelector('#msgs2')
    m.dataset.probe = 'PROBE'
    return { nodes: m.children.length, hasHumanStyle: !!document.querySelector('.m2.human'),
             stick: m.dataset.stick }
  })()`)
  console.log('   挂件状态:', JSON.stringify(view))

  // ── ③ 客服台：发消息前后列表有没有被重建 ──
  console.log('\n【③】客服台发消息时列表会不会重建')
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(6500)
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return 1 })()`)
  await wait(3000)
  navs.length = 0
  const b = await cdp.eval(`(() => {
    const l = document.querySelector('#holist')
    l.dataset.probe = 'LIST'; l.firstElementChild.dataset.probe2 = 'ITEM'
    const w = document.querySelector('.tlwrap'); if (w) w.dataset.probe = 'WRAP'
    return { items: document.querySelectorAll('.hoitem').length, rows: document.querySelectorAll('.tl-row').length }
  })()`)
  await cdp.eval(`(() => { document.querySelector('#hoText').value='测试不刷新2'; return 1 })()`)
  await cdp.eval(`hoReply()`)
  await wait(4500)
  const a = await cdp.eval(`(() => ({
    listProbe: document.querySelector('#holist').dataset.probe || '(没了)',
    itemProbe: document.querySelector('#holist').firstElementChild ? (document.querySelector('#holist').firstElementChild.dataset.probe2 || '(没了)') : '?',
    wrapProbe: document.querySelector('.tlwrap') ? (document.querySelector('.tlwrap').dataset.probe || '(没了)') : '?',
    items: document.querySelectorAll('.hoitem').length, rows: document.querySelectorAll('.tl-row').length,
  }))()`)
  console.log('   发送前:', JSON.stringify(b))
  console.log('   发送后:', JSON.stringify(a))
  console.log('   ★ 列表区块保住了:', a.listProbe === 'LIST' ? '✅' : '❌ 被重建')
  console.log('   ★ 会话区块保住了:', a.wrapProbe === 'WRAP' ? '✅' : '❌ 被重建')
  console.log('   导航事件:', navs.length ? navs : '（无）')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\ho_norefresh2.png`, Buffer.from(shot.data, 'base64'))
  console.log('   截图 → ho_norefresh2.png')
} finally { await close() }
