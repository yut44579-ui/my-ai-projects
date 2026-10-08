/** 诊断页：抓 console/异常/最终 DOM，用于定位白屏原因。用法：node scripts/diag_page.mjs <url> */
import { mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// ★ 变量名不要叫 URL：会遮蔽全局 URL 构造器（实测踩过）
const TARGET_URL = process.argv[2] ?? 'http://127.0.0.1:5173/login'
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
mkdirSync(SHOT_DIR, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: join(ROOT, '.tmp-edge', `diag-${process.pid}-${Date.now()}`),
  port: 11200 + (process.pid % 300),
  windowSize: '1400,900',
})

const logs = []
try {
  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  await cdp.send('Page.enable')
  cdp.on('Runtime.consoleAPICalled', (p) => {
    logs.push(`[console.${p.type}] ${p.args.map((a) => a.value ?? a.description ?? '').join(' ')}`)
  })
  cdp.on('Runtime.exceptionThrown', (p) => {
    const d = p.exceptionDetails
    logs.push(`[EXCEPTION] ${d.exception?.description ?? d.text}`)
  })
  cdp.on('Log.entryAdded', (p) => {
    logs.push(`[log.${p.entry.level}] ${p.entry.text}`)
  })

  await cdp.send('Page.navigate', { url: TARGET_URL })
  await sleep(6000)

  const html = await cdp.eval('document.body.innerHTML.slice(0, 700)')
  const text = await cdp.eval('document.body.innerText')
  const rootHtml = await cdp.eval(`document.getElementById('root')?.innerHTML.slice(0,400) ?? 'NO #root'`)
  const title = await cdp.eval('document.title')

  console.log('URL      :', TARGET_URL)
  console.log('title    :', title)
  console.log('innerText 长度:', text.length)
  console.log('---- #root 内容（前 400）----')
  console.log(rootHtml)
  console.log('---- body（前 700）----')
  console.log(html)
  console.log('---- 控制台/异常 ----')
  if (!logs.length) console.log('（无输出）')
  logs.slice(0, 30).forEach((l) => console.log(l))

  await cdp.shot('diag-page', SHOT_DIR)
  console.log('截图 → docs/screenshots/diag-page.png')
} finally {
  close()
}
