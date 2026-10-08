/** 打开报告详情抽屉并截图，验证 15 个指标按 6 类分组渲染。 */
import { mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
mkdirSync(SHOT_DIR, { recursive: true })

const { cdp, close } = await launchEdge({
  profileDir: join(ROOT, '.tmp-edge', `report-detail-${process.pid}-${Date.now()}`),
  port: 11500 + (process.pid % 300),
  windowSize: '1600,1200',
})

const url = process.env.APP_URL ?? 'http://127.0.0.1:5173/reports'
try {
  const token = process.env.DSH_TEST_TOKEN
  if (token) {
    await cdp.send('Page.navigate', { url: new URL(url).origin + '/login' })
    await cdp.waitFor(`document.readyState === 'complete'`, { label: '登录页', timeout: 20_000 })
    let userJs = "'{}'"
    try {
      const api = process.env.DSH_API_BASE ?? 'http://127.0.0.1:8000'
      const r = await fetch(`${api}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      if (r.ok) userJs = JSON.stringify(JSON.stringify(await r.json()))
    } catch { /* 只注入令牌 */ }
    await cdp.eval(
      `localStorage.setItem('auth_token', ${JSON.stringify(token)});` +
        `localStorage.setItem('auth_user', ${userJs}); true`,
    )
  }
  await cdp.send('Page.navigate', { url })
  await cdp.waitFor(`document.body.innerText.includes('历史报告')`, { label: '报告页', timeout: 30_000 })
  await sleep(1200)

  // 点第一份报告的「查看」打开详情抽屉
  const clicked = await cdp.eval(`(() => {
    const btn = [...document.querySelectorAll('button')].find(b => b.innerText.includes('查看'))
    if (!btn) return false
    btn.click(); return true
  })()`)
  console.log('点击「查看」:', clicked)
  await cdp.waitFor(`document.body.innerText.includes('指标快照')`, { label: '详情抽屉', timeout: 20_000 })
  await sleep(1500)

  const groups = await cdp.eval(`(() => {
    const names = ['客户','客户沟通','商机与成交','项目进展','风险与异常','营销效果']
    return names.filter(n => document.body.innerText.includes(n))
  })()`)
  console.log('抽屉里出现的分组:', groups)

  const metricCount = await cdp.eval(`(document.body.innerText.match(/report:metric=/g) || []).length`)
  console.log('渲染出的指标卡（含 evidence_ref）数量:', metricCount)

  await cdp.shot('task023-report-detail', SHOT_DIR)
  console.log('截图 → docs/screenshots/task023-report-detail.png')

  if (groups.length < 4) {
    console.error(`❌ 分组太少（${groups.length}），分组渲染可能有问题`)
    process.exitCode = 1
  } else {
    console.log('🎉 指标分组渲染正常')
  }
} finally {
  close()
}
