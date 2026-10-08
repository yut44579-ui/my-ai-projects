/**
 * TASK-004 验收截图：驱动本机 Edge 跑真实前端 + 真实后端，走完 AI 回复的三条路径：
 *   ① 普通问题 → AI 真回复（落库 REPLIED）
 *   ② 敏感问题 → 代码层闸门拦下、转人工（不调 LLM，落库 HUMAN_REQUIRED）
 *   ③ LLM 失败 → 界面显示「AI 暂时无法回复，请人工处理」（落库 FAILED，不编造回复）
 *
 * ★ 零新增依赖：复用 scripts/lib/cdp.mjs。
 * ★ 走的是真实前端：粘问题真敲、按钮真点、提示与气泡都是后端真返回的内容。
 *
 * 用法（对应后端/前端端口都已启动）：
 *   APP_URL=http://127.0.0.1:5183/customers                      node scripts/ui_screenshots_task004.mjs
 *   EXPECT_FAILURE=1 APP_URL=http://127.0.0.1:5184/customers     node scripts/ui_screenshots_task004.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', `task004-${RUN_ID}`)
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5183/customers'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9900 + (process.pid % 100)
const EXPECT_FAILURE = process.env.EXPECT_FAILURE === '1'
const EVIDENCE_FILE = EXPECT_FAILURE
  ? 'task004-api-calls.json'
  : 'task004-api-calls.json'

const NORMAL_QUESTION = '你们的售后支持时间是怎样的？'
const SENSITIVE_QUESTION = '老客户能不能给个折扣？'
const FAIL_QUESTION = '请问你们公司的服务网点有哪些？'

const setNativeValue = (selector, value) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)})
  if (!el) return false
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(el, ${JSON.stringify(value)})
  el.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`

const scrollToMessagesCard = `(() => {
  const titles = [...document.querySelectorAll('.ant-card-head-title')]
  const hit = titles.find(t => (t.textContent || '').includes('沟通记录'))
  if (!hit) return false
  hit.scrollIntoView({ block: 'start' })
  window.scrollBy(0, -70)
  return true
})()`

/** 进入某个客户详情（按姓名找行；没有就点第一行） */
async function openCustomer(cdp, name) {
  await cdp.send('Page.navigate', { url: APP_URL })
  await cdp.waitFor("document.querySelectorAll('tbody tr.ant-table-row').length > 0", {
    label: '客户表格',
  })
  await sleep(600)
  const id = await cdp.eval(
    `(() => {
       const rows = [...document.querySelectorAll('tbody tr.ant-table-row')]
       const hit = rows.find(r => (r.innerText || '').includes(${JSON.stringify(name)})) || rows[0]
       if (!hit) return null
       hit.click()
       return hit.getAttribute('data-row-key')
     })()`,
  )
  if (!id) throw new Error('点不到表格行')
  await cdp.waitFor("document.querySelector('textarea')", { label: '输入框' })
  await sleep(700)
  return id
}

/** 在输入框里写问题并点「AI 回复」 */
async function askAi(cdp, question) {
  if (!(await cdp.eval(setNativeValue('textarea', question)))) throw new Error('输入框写不进去')
  await sleep(300)
  await cdp.clickText('AI 回复')
}

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

    if (EXPECT_FAILURE) {
      // ③ LLM 失败：后端指向一个连不上的 LLM 地址
      console.log('③ LLM 失败路径…')
      await openCustomer(cdp, '李娜')
      await askAi(cdp, FAIL_QUESTION)
      await cdp.waitFor("document.body.innerText.includes('AI 暂时无法回复，请人工处理')", {
        label: '失败兜底文案',
        timeout: 30_000,
      })
      await sleep(500)
      await cdp.eval(scrollToMessagesCard)
      await sleep(600)
      await cdp.diagnose('LLM 失败')
      await cdp.shot('task004-ai-failed-fallback', SHOT_DIR)
      writeFileSync(
        join(EVIDENCE_DIR, 'task004-api-calls-failed.json'),
        JSON.stringify(captured, null, 2),
      )
      console.log('  已截图：失败兜底文案 + 时间线里的 FAILED 行')
    } else {
      // ① 普通问题 → AI 真回复
      console.log('① 普通问题 → AI 回复…')
      const id = await openCustomer(cdp, '李娜')
      await askAi(cdp, NORMAL_QUESTION)
      await cdp.waitFor("document.body.innerText.includes('AI 已回复')", {
        label: 'AI 回复落库',
        timeout: 60_000,
      })
      await sleep(500)
      await cdp.eval(scrollToMessagesCard)
      await sleep(600)
      await cdp.diagnose('AI 回复')
      await cdp.shot('task004-ai-reply', SHOT_DIR)
      console.log(`  客户 id=${id}：AI 回复已出现在时间线`)

      // ② 敏感问题 → 闸门拦下、转人工（不调 LLM）
      console.log('② 敏感问题 → 转人工…')
      await askAi(cdp, SENSITIVE_QUESTION)
      await cdp.waitFor("document.body.innerText.includes('已转人工处理')", {
        label: '转人工提示',
        timeout: 20_000,
      })
      await cdp.waitFor("document.body.innerText.includes('需人工处理')", { label: '转人工气泡' })
      await sleep(500)
      await cdp.eval(scrollToMessagesCard)
      await sleep(600)
      await cdp.diagnose('敏感转人工')
      await cdp.shot('task004-ai-human-required', SHOT_DIR)
      writeFileSync(join(EVIDENCE_DIR, EVIDENCE_FILE), JSON.stringify(captured, null, 2))
      console.log(`  浏览器真实调用 ${captured.length} 条 → docs/evidence/${EVIDENCE_FILE}`)
    }
  } finally {
    close()
  }
  process.exit(0)
}

main().catch((err) => {
  console.error('失败：', err.message)
  process.exit(1)
})
