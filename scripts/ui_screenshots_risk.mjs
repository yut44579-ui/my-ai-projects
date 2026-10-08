/**
 * 风险中心页验收截图：真浏览器打开 /risk，断言汇总卡与风险明细表都画出来了。
 * 用法（后端 8000、前端 5173 已启动）：node scripts/ui_screenshots_risk.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', `risk-${RUN_ID}`)
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5173/risk'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 10100 + (process.pid % 200)

const CHECKS = [
  ['侧栏「风险中心」高亮', `document.querySelector('.ant-menu-item-selected')?.innerText.includes('风险中心')`],
  ['汇总卡：风险总数', `document.body.innerText.includes('风险总数')`],
  ['汇总卡：待处理', `document.body.innerText.includes('待处理')`],
  ['汇总卡：高风险待处理', `document.body.innerText.includes('高风险待处理')`],
  ['汇总卡：需关注待处理', `document.body.innerText.includes('需关注待处理')`],
  ['汇总卡：已处置', `document.body.innerText.includes('已处置')`],
  ['判定依据说明', `document.body.innerText.includes('判定依据')`],
  ['扫描按钮', `document.body.innerText.includes('扫描消息生成风险')`],
  ['风险明细表', `document.body.innerText.includes('风险明细')`],
  ['表头：等级', `document.body.innerText.includes('等级')`],
  ['表头：证据', `document.body.innerText.includes('证据')`],
  ['证据锚点格式正确', `/customer_message:\\d+/.test(document.body.innerText)`],
  ['状态筛选器', `document.body.innerText.includes('已忽略')`],
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
    windowSize: '1600,1200',
  })

  try {
    const captured = []
    await cdp.send('Network.enable')
    await cdp.send('Page.enable')
    cdp.on('Network.responseReceived', async (params) => {
      const url = params.response.url
      if (!url.includes('/api/risks')) return
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

    console.log(`① 打开风险中心 ${APP_URL}`)
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor(`document.body.innerText.includes('风险明细')`, {
      label: '风险中心',
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

    await cdp.shot('task009-risk-center', SHOT_DIR)
    const evidenceFile = join(EVIDENCE_DIR, 'task009-risk-api.json')
    writeFileSync(evidenceFile, JSON.stringify(captured, null, 2), 'utf8')
    console.log(`  API 证据 → ${evidenceFile}（${captured.length} 条）`)

    const sum = captured.find((c) => c.url.includes('/risks/summary'))
    if (sum) {
      const d = JSON.parse(sum.body)
      console.log(`\n③ /risks/summary（HTTP ${sum.status}）`)
      console.log(`   总数=${d.total.state}(${d.total.value}) 待处理=${d.open_total.state}(${d.open_total.value})`)
      console.log(`   高风险待处理=${d.high_open.state}(${d.high_open.value}) 类型分布=${JSON.stringify(d.by_type)}`)
    }

    if (failures.length) {
      console.error(`\n❌ 风险中心验收有 ${failures.length} 项未通过：`)
      failures.forEach((f) => console.error(`   · ${f}`))
      process.exitCode = 1
      return
    }
    console.log('\n🎉 风险中心结构验收全部通过')
  } finally {
    clearTimeout(watchdog)
    close()
  }
}

main().catch((err) => {
  console.error('脚本失败：', err)
  process.exit(1)
})
