/** 验证：转人工后转圈 → 人工接管后停转。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const BASE = 'http://43.134.58.30:8081/demo/kefu'
const OUT = 'D:\\ai-kefu\\docs\\screenshots'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile', port: 9750, windowSize: '420,900',
})
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const jget = (p) => fetch(BASE + p).then((r) => r.json())
const jpost = (p, b) => fetch(BASE + p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}),
}).then((r) => r.json())

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // ① 打开官网，问一句必然转人工的
  await cdp.send('Page.navigate', { url: BASE + '/site.html' })
  await wait(3500)
  await cdp.eval(`document.querySelector('#fab').click()`)
  await wait(1000)
  await cdp.eval(`(() => {
    const i = document.querySelector('#q2')
    const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    s.call(i, '能报个价吗'); i.dispatchEvent(new Event('input',{bubbles:true}))
    document.querySelector('#go2').click(); return 1 })()`)
  await wait(14000)

  const s1 = await cdp.eval(`(() => ({
    spinning: !!document.querySelector('.spinner'),
    waitbar: !!document.querySelector('.waitbar'),
    waitText: (document.querySelector('.waitbar')||{innerText:''}).innerText.replace(/\\s+/g,' '),
    taken: !!document.querySelector('.m2.taken'),
    status: document.querySelector('#st2').textContent,
  }))()`)
  console.log('  ① 转人工之后：')
  console.log('     转圈中 :', s1.spinning ? '✅ 在转' : '❌ 没转', '| 等待条:', s1.waitbar ? '有' : '无')
  console.log('     文案   :', s1.waitText)
  console.log('     已接入 :', s1.taken ? '出现了（不该出现）' : '✅ 还没出现')
  console.log('     顶栏   :', s1.status)

  // 截图"转圈中"
  const shot1 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\spinner_waiting.png`, Buffer.from(shot1.data, 'base64'))
  console.log('     → spinner_waiting.png')

  // ② 人工在工作台点「接管」
  const ho = await jget('/api/handover')
  const item = ho.items[0]
  console.log('\n  ② 人工接管（走接口，等同于点「接管」）')
  console.log('     队列项 #' + item.id, item.question)
  console.log('     ', JSON.stringify(await jpost(`/api/handover/${item.id}/take`, { by: '张工' })))

  // ③ 客户端轮询到停转
  await wait(4000)
  const s2 = await cdp.eval(`(() => ({
    spinning: !!document.querySelector('.spinner'),
    taken: !!document.querySelector('.m2.taken'),
    takenText: (document.querySelector('.m2.taken')||{innerText:''}).innerText,
    status: document.querySelector('#st2').textContent,
  }))()`)
  console.log('\n  ③ 接管之后：')
  console.log('     转圈中 :', s2.spinning ? '❌ 还在转' : '✅ 停了')
  console.log('     已接入 :', s2.taken ? '✅ 「' + s2.takenText + '」' : '❌ 没出现')
  console.log('     顶栏   :', s2.status)

  const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\spinner_taken.png`, Buffer.from(shot2.data, 'base64'))
  console.log('     → spinner_taken.png')

  // ④ 人工回复一句
  console.log('\n  ④ 人工回复一句')
  const r = await jpost(`/api/handover/${item.id}/reply`, {
    text: '报价要工程师看过图纸才能定，你把零件类型、材料和数量发我一下，我给你安排。',
    by: '张工', send: true,
  })
  console.log('     ', JSON.stringify(r).slice(0, 90))
  await wait(4000)
  const s3 = await cdp.eval(`(() => ({
    spinning: !!document.querySelector('.spinner'),
    last: (document.querySelector('#msgs2').lastElementChild||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,70),
    status: document.querySelector('#st2').textContent,
  }))()`)
  console.log('     转圈中 :', s3.spinning ? '❌ 还在转' : '✅ 停了')
  console.log('     最后一条:', s3.last)
  console.log('     顶栏   :', s3.status)
  const shot3 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\spinner_replied.png`, Buffer.from(shot3.data, 'base64'))
  console.log('     → spinner_replied.png')
} finally { await close() }
