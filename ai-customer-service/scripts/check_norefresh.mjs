/** 验证：人工发消息时页面不刷新（滚动位置和草稿都保住）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9800, windowSize: '1420,1000',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const errs = []
  cdp.on('Runtime.exceptionThrown', (x) => errs.push('EXC ' + (x.exceptionDetails?.exception?.description || '').slice(0, 200)))
  cdp.on('Runtime.consoleAPICalled', (x) => { if (x.type === 'error') errs.push('ERR ' + JSON.stringify(x.args?.map(a=>a.value)).slice(0,200)) })

  // 先造几条转人工，让会话记录长一点（否则没得滚）
  for (const q of ['能报个价吗', '我要投诉', '能退款吗']) {
    await fetch(BASE + '/api/web/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: 'noscroll-demo', text: q }),
    })
    await wait(12000)
  }
  console.log('  造了 3 条转人工')

  await cdp.send('Page.navigate', { url: BASE + '/index.html#ho' })
  await cdp.waitFor(`document.querySelector('.isum') || document.querySelector('.hoitem')`,
    { timeout: 20000, label: '人工列表' })
  await wait(3000)
  // 选第一条并等详情
  await cdp.eval(`(() => { const f=document.querySelector('.hoitem'); if(f) f.click(); return 1 })()`)
  await cdp.waitFor(`document.querySelector('.tlwrap')`, { timeout: 15000, label: '会话记录' })
  await wait(2000)

  // 滚动到中间 + 在输入框里打一半草稿
  const before = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap')
    w.scrollTop = Math.floor(w.scrollHeight / 3)
    const t = document.querySelector('#hoText')
    t.value = '我正打到一半的草稿'
    t.focus()
    t.setSelectionRange(4, 4)
    return { scroll: w.scrollTop, draft: t.value, rows: document.querySelectorAll('.tl-row').length }
  })()`)
  console.log('\n  发送前:')
  console.log('     滚动位置:', before.scroll, '| 草稿:', JSON.stringify(before.draft), '| 会话行数:', before.rows)

  // ★ 发一条消息
  //   ★★ 注意：这里必须直接调 hoReply()，不能用 `document.querySelector('.btns button').click()`
  //      —— 页面上有多个 .btns，第一个不是"发送"按钮。
  //      我因为这个测试脚本自己的 bug，白查了一轮 hoReply。
  await cdp.eval(`(() => {
    const t = document.querySelector('#hoText')
    t.value = '报价要工程师看过图纸才能定，你把零件类型和材料发我一下。'
    hoReply(); return 1 })()`)
  await wait(4000)

  const after = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap')
    const t = document.querySelector('#hoText')
    return {
      scroll: w ? w.scrollTop : null,
      draft: t ? t.value : null,
      rows: document.querySelectorAll('.tl-row').length,
      last: (document.querySelector('.tlwrap').lastElementChild||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,60),
      focused: document.activeElement ? document.activeElement.id : '',
      msg: (document.querySelector('#hoMsg')||{textContent:''}).textContent.slice(0,40),
    }
  })()`)
  console.log('\n  发送后:')
  console.log('     滚动位置:', after.scroll, '(★ 没有跳回 0 就说明没刷新)')
  console.log('     草稿框  :', JSON.stringify(after.draft), '(发完应该清空)')
  console.log('     会话行数:', before.rows, '→', after.rows, '(应该 +1)')
  console.log('     最后一条:', after.last)
  console.log('     提示    :', after.msg)

  const ok1 = after.rows === before.rows + 1
  const ok2 = after.scroll !== 0 && after.scroll >= before.scroll
  console.log('\n  ★ 会话记录是追加的（不是重建）:', ok1 ? '✅' : '❌')
  console.log('  ★ 滚动位置保住了（没跳回顶部）:', ok2 ? '✅' : '❌')

  // 再等一个自动刷新周期，看详情会不会被重建
  const beforeWait = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap'); w.scrollTop = Math.floor(w.scrollHeight/2)
    return { scroll: w.scrollTop, rows: document.querySelectorAll('.tl-row').length }
  })()`)
  console.log('\n  等 25 秒（越过一次自动刷新）…')
  await wait(25000)
  const afterWait = await cdp.eval(`(() => {
    const w = document.querySelector('.tlwrap')
    return { scroll: w.scrollTop, rows: document.querySelectorAll('.tl-row').length }
  })()`)
  console.log('     滚动:', beforeWait.scroll, '→', afterWait.scroll,
    afterWait.scroll === beforeWait.scroll ? '✅ 没动' : '❌ 被重置了')
  console.log('     行数:', beforeWait.rows, '→', afterWait.rows,
    afterWait.rows === beforeWait.rows ? '✅ 没重建' : '⚠ 变了')

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1420, height: 1000, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\ho_norefresh.png`, Buffer.from(shot.data, 'base64'))
  console.log('\n  截图 → ho_norefresh.png')
} finally { await close() }
