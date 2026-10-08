/**
 * TASK-003 验收截图：驱动本机 Edge 跑真实前端（客户详情 → 沟通记录空态 → 真发一条人工消息 →
 * 刷新页面证明消息仍在），并把浏览器真实发出的 /api/customers* 响应体存下来当证据。
 *
 * ★ 零新增依赖：复用 scripts/lib/cdp.mjs（与 TASK-001 / TASK-002 截图脚本同一套 CDP 客户端）。
 * ★ 走的是真实前端 + 真实后端：输入是真敲、发送真点、刷新是 Page.navigate 重新加载。
 * ★ 唯一会写库的动作是「发送人工消息」——这正是本 TASK 要验的链路（sender_type=HUMAN）。
 *
 * 用法（后端 8103、前端 5183 都已启动）：
 *   APP_URL=http://127.0.0.1:5183/customers node scripts/ui_screenshots_task003.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', RUN_ID)
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5183/customers'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9700 + (process.pid % 200)

const MARK = `TASK-003 页面验收留言 ${RUN_ID}`

/** 给受 React 控制的控件赋值（必须走原生 setter，否则 React 收不到变更） */
const setNativeValue = (selector, value, proto) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)})
  if (!el) return false
  const setter = Object.getOwnPropertyDescriptor(window.${proto}.prototype, 'value').set
  setter.call(el, ${JSON.stringify(value)})
  el.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`

/** 把「沟通记录」卡片滚到视口顶部附近（详情页很长，不滚会截不到内容） */
const scrollToMessagesCard = `(() => {
  const titles = [...document.querySelectorAll('.ant-card-head-title')]
  const hit = titles.find(t => (t.textContent || '').includes('沟通记录'))
  if (!hit) return false
  hit.scrollIntoView({ block: 'start' })
  window.scrollBy(0, -70)
  return true
})()`

async function main() {
  mkdirSync(SHOT_DIR, { recursive: true })
  mkdirSync(EVIDENCE_DIR, { recursive: true })

  const watchdog = setTimeout(() => {
    console.error('看门狗超时（240s），强制退出')
    process.exit(2)
  }, 240_000)
  watchdog.unref?.()

  const { cdp, close } = await launchEdge({ profileDir: TMP_PROFILE, port: DEBUG_PORT })

  try {
    const captured = []
    await cdp.send('Network.enable')
    await cdp.send('Page.enable')
    cdp.on('Network.responseReceived', async (params) => {
      const url = params.response.url
      if (!url.includes('/api/customers')) return
      try {
        const { body, base64Encoded } = await cdp.send('Network.getResponseBody', {
          requestId: params.requestId,
        })
        captured.push({
          url,
          status: params.response.status,
          body: base64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body,
        })
      } catch {
        /* 取不到响应体的忽略 */
      }
    })

    // ① 列表页 → 进入一个**没有消息**的客户详情，先看空态
    console.log('① 打开客户列表并进入一个无消息客户…')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelectorAll('tbody tr.ant-table-row').length > 0", {
      label: '客户表格',
    })
    await sleep(600)

    // 用「姓名」找到夹具里那条没有消息的客户（周杰），点进去
    const entered = await cdp.eval(
      `(() => {
         const rows = [...document.querySelectorAll('tbody tr.ant-table-row')]
         const hit = rows.find(r => (r.innerText || '').includes('周杰')) || rows[0]
         if (!hit) return null
         hit.click()
         return hit.getAttribute('data-row-key')
       })()`,
    )
    if (!entered) throw new Error('点不到表格行')
    await cdp.waitFor("document.body.innerText.includes('沟通记录')", { label: '沟通记录区块' })
    await cdp.waitFor("document.body.innerText.includes('暂无沟通记录')", { label: '沟通记录空态' })
    await sleep(400)
    await cdp.eval(scrollToMessagesCard)
    await sleep(600)
    await cdp.diagnose('沟通记录空态')
    await cdp.shot('task003-message-empty', SHOT_DIR)
    const emptyId = await cdp.eval('location.pathname.split("/").pop()')
    console.log(`  空态客户 id=${emptyId}`)

    // ② 用另一个有消息的客户发一条（避免把空态客户写脏）：先回列表点「张伟」
    console.log('② 进入张伟的详情并真发一条人工消息…')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelectorAll('tbody tr.ant-table-row').length > 0", {
      label: '客户表格',
    })
    await sleep(600)
    const targetId = await cdp.eval(
      `(() => {
         const rows = [...document.querySelectorAll('tbody tr.ant-table-row')]
         const hit = rows.find(r => (r.innerText || '').includes('张伟'))
         if (!hit) return null
         hit.click()
         return hit.getAttribute('data-row-key')
       })()`,
    )
    if (!targetId) throw new Error('找不到张伟这一行')
    await cdp.waitFor("document.querySelector('textarea')", { label: '回复输入框' })
    await sleep(600)

    if (!(await cdp.eval(setNativeValue('textarea', MARK, 'HTMLTextAreaElement')))) {
      throw new Error('输入框写不进去')
    }
    await sleep(300)
    await cdp.clickText('发送')
    await cdp.waitFor(`document.body.innerText.includes(${JSON.stringify(MARK)})`, {
      label: '新消息出现在时间线',
    })
    await sleep(400)
    await cdp.eval(scrollToMessagesCard)
    await sleep(600)
    await cdp.diagnose('发送后')
    await cdp.shot('task003-message-sent', SHOT_DIR)
    console.log(`  已发送并出现在时间线（客户 id=${targetId}）`)

    // ③ 刷新页面（真重新加载）→ 消息仍在 = 真落库
    console.log('③ 刷新页面验证消息仍在…')
    await cdp.send('Page.navigate', { url: `${APP_URL}/${targetId}` })
    await cdp.waitFor(`document.body.innerText.includes(${JSON.stringify(MARK)})`, {
      label: '刷新后消息仍在',
      timeout: 20000,
    })
    await sleep(400)
    await cdp.eval(scrollToMessagesCard)
    await sleep(600)
    await cdp.diagnose('刷新后')
    await cdp.shot('task003-message-after-reload', SHOT_DIR)

    writeFileSync(join(EVIDENCE_DIR, 'task003-api-calls.json'), JSON.stringify(captured, null, 2))
    console.log(`  浏览器真实调用 ${captured.length} 条 → docs/evidence/task003-api-calls.json`)
  } finally {
    close()
  }
  process.exit(0)
}

main().catch((err) => {
  console.error('失败：', err.message)
  process.exit(1)
})
