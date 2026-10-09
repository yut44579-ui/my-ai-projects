/** 真浏览器验证：人工接管后客户再发话，不能再转人工。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

const S = 'hs-demo-' + Math.floor(Math.random() * 100000)
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_hs', port: 9980, windowSize: '1420,1000',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // 打开挂件，用这个会话
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(4000)
  await cdp.eval(`(() => {
    // 把挂件的 session 换成我们指定的，方便脚本驱动
    try { localStorage.setItem('kefu_session', ${JSON.stringify(S)}); } catch (e) {}
    return 1 })()`)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1200)

  // ① 造一条转人工
  await jpost('/api/web/chat', { session: S, text: '能报个价吗' })
  await wait(14000)
  let msgs = await jget('/api/web/poll?session=' + S + '&since=0')
  console.log('  ① 转人工后:', msgs.messages.map((m) => m.kind + ':' + m.content.slice(0, 16)).join(' | '))

  // ② 人工接管
  const ho = await jget('/api/handover')
  const it = ho.items.find((x) => x.user_id === S)
  const take = await jpost(`/api/handover/${it.id}/take`, { by: '张工' })
  console.log('  ② 接管:', JSON.stringify(take))
  await wait(3000)

  // ③ 客服回一句
  await jpost(`/api/handover/${it.id}/reply`, { text: '报价要工程师看过图纸才能定，你把零件类型发我。', by: '张工', send: true })
  await wait(4000)

  // ④ ★ 客户再发两句 —— AI 必须一句都不回，也不能再转人工
  const sinceBefore = (await jget(`/api/web/poll?session=${S}&since=0`)).last
  await jpost('/api/web/chat', { session: S, text: '我的电话是13800000000' })
  await wait(14000)
  await jpost('/api/web/chat', { session: S, text: '你们几点上班' })
  await wait(14000)
  const after = await jget(`/api/web/poll?session=${S}&since=${sinceBefore}`)
  const kinds = after.messages.map((m) => m.kind)
  console.log('  ④ 接管后客户发了两句，新增:', after.messages.map((m) => m.kind + ':' + m.content.slice(0, 18)).join(' | ') || '（无）')
  console.log('     ★ 有没有 AI 回答:', kinds.includes('ai') ? '❌ 有' : '✅ 没有')
  console.log('     ★ 有没有又转人工:', kinds.includes('waiting') ? '❌ 又转了' : '✅ 没有')

  // ⑤ 看挂件上的状态
  await cdp.eval(`poll()`)
  await wait(2500)
  const view = await cdp.eval(`(() => ({
    status: (document.querySelector('#st2')||{}).textContent,
    spinning: !!document.querySelector('.spinner'),
    humanMode: typeof humanMode !== 'undefined' ? humanMode : null,
    humanBubbles: document.querySelectorAll('.m2.human').length,
    last: (document.querySelector('#msgs2').lastElementChild||{}).innerText?.replace(/\\s+/g,' ').slice(0,50),
  }))()`)
  console.log('  ⑤ 挂件状态:', JSON.stringify(view))
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 } })
  fs.writeFileSync(`${OUT}\\hs_customer.png`, Buffer.from(shot.data, 'base64'))
  console.log('     截图 → hs_customer.png')

  // ⑥ 客服台：应该有「结束接管」按钮
  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await wait(7000)
  await cdp.eval(`switchTab('ho')`)
  await wait(3500)
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return 1 })()`)
  await wait(3500)
  const cons = await cdp.eval(`(() => ({
    hasEndBtn: [...document.querySelectorAll('#hoDetail button')].some(b => b.textContent.includes('结束接管')),
    btns: [...document.querySelectorAll('#hoDetail button')].map(b => b.textContent.trim()),
    takenMark: (document.querySelector('#hoDetail')||{innerText:''}).includes('你正在管这个会话'),
  }))()`)
  console.log('  ⑥ 客服台按钮:', JSON.stringify(cons.btns))
  console.log('     有结束接管:', cons.hasEndBtn ? '✅' : '❌', '| 有接管中标记:', cons.takenMark ? '✅' : '❌')
  const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 } })
  fs.writeFileSync(`${OUT}\\hs_console.png`, Buffer.from(shot2.data, 'base64'))
  console.log('     截图 → hs_console.png')

  // ⑦ 点结束接管 → AI 恢复
  await jpost(`/api/handover/${it.id}/end?by=` + encodeURIComponent('张工'), {})
  await wait(3000)
  const since2 = (await jget(`/api/web/poll?session=${S}&since=0`)).last
  await jpost('/api/web/chat', { session: S, text: '打样要多久' })
  await wait(15000)
  const back = await jget(`/api/web/poll?session=${S}&since=${since2}`)
  console.log('  ⑦ 结束接管后客户再问，新增:', back.messages.map((m) => m.kind + ':' + m.content.slice(0, 24)).join(' | ') || '（无）')
  console.log('     ★ AI 恢复了吗:', back.messages.some((m) => m.kind === 'ai') ? '✅ 恢复了' : '❌ 没恢复')
} finally { await close() }
