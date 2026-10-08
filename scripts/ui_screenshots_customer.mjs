/**
 * 客户详情页（客户工作台）验收截图：驱动本机 Edge 打开真实客户详情，
 * 断言「状态与下一步」「风险提醒」两块真的画出来了，并抓真实 API 响应体当证据。
 *
 * 用法（后端 8000、前端 5173 都已启动）：
 *   node scripts/ui_screenshots_customer.mjs
 *   CUSTOMER_ID=652 node scripts/ui_screenshots_customer.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', `customer-${RUN_ID}`)
const CUSTOMER_ID = process.env.CUSTOMER_ID ?? '652'
const APP_URL = process.env.APP_URL ?? `http://127.0.0.1:5173/customers/${CUSTOMER_ID}`
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9800 + (process.pid % 150)

const CHECKS = [
  ['客户姓名已加载', `document.body.innerText.length > 200`],
  ['基本信息卡', `document.body.innerText.includes('基本信息')`],
  ['生命周期标签', `document.body.innerText.includes('已联系') || document.body.innerText.includes('未联系')`],
  ['接管状态标签', `document.body.innerText.includes('接管')`],
  ['状态与下一步卡', `document.body.innerText.includes('状态与下一步')`],
  ['下一步建议', `document.body.innerText.includes('下一步建议')`],
  ['下一步带依据', `document.body.innerText.includes('依据：')`],
  ['接管流转历史', `document.body.innerText.includes('接管流转历史')`],
  ['风险提醒卡', `document.body.innerText.includes('风险提醒')`],
  ['来源与可追溯', `document.body.innerText.includes('来源与可追溯')`],
  ['事件流', `document.body.innerText.includes('事件流')`],
  ['沟通记录', `document.body.innerText.includes('沟通')`],
  ['无 [object Object]', `!document.body.innerText.includes('[object Object]')`],
  ['无 undefined', `!document.body.innerText.includes('undefined')`],
  ['无 NaN', `!document.body.innerText.includes('NaN')`],
]

async function main() {
  mkdirSync(SHOT_DIR, { recursive: true })
  mkdirSync(EVIDENCE_DIR, { recursive: true })

  const watchdog = setTimeout(() => {
    console.error('看门狗超时（240s），强制退出')
    process.exit(2)
  }, 240_000)
  watchdog.unref?.()

  const { cdp, close } = await launchEdge({
    profileDir: TMP_PROFILE,
    port: DEBUG_PORT,
    windowSize: '1600,1400',
  })

  try {
    const captured = []
    await cdp.send('Network.enable')
    await cdp.send('Page.enable')
    cdp.on('Network.responseReceived', async (params) => {
      const url = params.response.url
      if (!url.includes('/api/')) return
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
        /* ignore */
      }
    })

    console.log(`① 打开客户详情 ${APP_URL}`)
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor(`document.body.innerText.includes('状态与下一步')`, {
      label: '客户详情页',
      timeout: 30_000,
    })
    await sleep(1200)

    console.log('② 逐项断言')
    const failures = []
    for (const [label, expr] of CHECKS) {
      const ok = await cdp.eval(`!!(${expr})`)
      console.log(`   ${ok ? '✅' : '❌'} ${label}`)
      if (!ok) failures.push(label)
    }

    await cdp.shot(`task008-customer-${CUSTOMER_ID}`, SHOT_DIR)

    const evidenceFile = join(EVIDENCE_DIR, `task008-customer-${CUSTOMER_ID}-api.json`)
    writeFileSync(evidenceFile, JSON.stringify(captured, null, 2), 'utf8')
    console.log(`  API 证据 → ${evidenceFile}（${captured.length} 条）`)

    const ws = captured.find((c) => c.url.includes('/workspace'))
    if (ws) {
      console.log(`\n③ /workspace 真实响应（HTTP ${ws.status}）`)
      const d = JSON.parse(ws.body)
      console.log(`   生命周期=${d.lifecycle_status} 接管=${d.handover_state} AI可自动回复=${d.ai_auto_reply_allowed}`)
      console.log(`   下一步=${d.next_action.label}（${d.next_action.code}）`)
      console.log(`   风险=${d.risk_total.state}(${d.risk_total.value}) 历史=${d.handover_history.length} 条`)
    }

    if (failures.length) {
      console.error(`\n❌ 客户详情页验收有 ${failures.length} 项未通过：`)
      failures.forEach((f) => console.error(`   · ${f}`))
      process.exitCode = 1
      return
    }
    console.log('\n🎉 客户详情页结构验收全部通过')
  } finally {
    clearTimeout(watchdog)
    close()
  }
}

main().catch((err) => {
  console.error('脚本失败：', err)
  process.exit(1)
})
