/** 截 AI客服控制台的实际界面（真跑，真调模型）。 */
import fs from 'node:fs'
import { launchEdge } from './lib/cdp.mjs'

const OUT = 'D:\\ai-kefu\\docs\\screenshots'
fs.mkdirSync(OUT, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9460,
  windowSize: '1500,1500',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:8600/' })
  await new Promise((r) => setTimeout(r, 3500))

  // 问一句真实的
  await cdp.eval(`(() => {
    const i = document.querySelector('#q')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(i, '报销单咋填啊')
    i.dispatchEvent(new Event('input', { bubbles: true }))
    document.querySelector('#go').click()
    return true
  })()`)
  await new Promise((r) => setTimeout(r, 22000))

  const info = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    return {
      hasAnswer: t.includes('金额') || t.includes('发票'),
      decision: (t.match(/(自动发送|AI 起草，待确认|转人工)/) || [])[1] || null,
      hasTier: t.includes('保守') && t.includes('标准') && t.includes('激进'),
      hasReasons: t.includes('该修哪里'),
      hasGaps: t.includes('待补充的问题'),
      hasStyles: t.includes('学到的说话方式'),
      text: t.replace(/\\s+/g, ' ').slice(0, 700),
    }
  })()`)
  console.log('  有回答    :', info.hasAnswer ? '✅' : '❌')
  console.log('  判定      :', info.decision)
  console.log('  档位面板  :', info.hasTier ? '✅' : '❌')
  console.log('  三块学习面板:', [info.hasReasons, info.hasGaps, info.hasStyles].join(' / '))
  console.log('  正文      :', info.text.slice(0, 340))

  const shot = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: 1500, height: 1460, scale: 1 },
  })
  fs.writeFileSync(`${OUT}\\console.png`, Buffer.from(shot.data, 'base64'))
  console.log('  截图 →', `${OUT}\\console.png`)

  const shot2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(`${OUT}\\console_view.png`, Buffer.from(shot2.data, 'base64'))
} finally {
  await close()
}
