/**
 * 通用页面验收断言器（可复用于任意页面）。
 *
 * 用法：
 *   node scripts/ui_check_page.mjs <url> <标签文件> [截图名]
 * 标签文件：每行一个"标签|JS 表达式"，表达式求值为真即通过；空行与 # 开头忽略。
 *
 * 为什么要这样一个通用器：
 *   之前每个模块写一个专用截图脚本（task002/003/004/008/009…），
 *   复制粘贴越来越多。新页面只要写一个标签文件即可，脚本不再重复。
 *
 * ★ 断言失败即退出码 1，避免"截到白屏还报成功"。
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')

const [urlArg, specArg, shotNameArg] = process.argv.slice(2)
if (!urlArg || !specArg) {
  console.error('用法：node scripts/ui_check_page.mjs <url> <标签文件> [截图名]')
  process.exit(2)
}
const APP_URL = urlArg
const SHOT_NAME = shotNameArg || 'page-check'
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', `check-${RUN_ID}`)
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 10400 + (process.pid % 300)

/** 解析标签文件 → [[标签, 表达式], ...] */
function loadChecks(file) {
  return readFileSync(file, 'utf8')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => {
      const i = l.indexOf('|')
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()]
    })
}

/**
 * 用令牌换当前用户信息（写进 auth_user 缓存用）。
 * ★ 页面读不到用户信息时顶栏会显示"未知用户"、问候语无名，属于测试环境问题而非功能缺陷。
 */
async function fetchMe(token) {
  const api = process.env.DSH_API_BASE ?? 'http://127.0.0.1:8000'
  try {
    const resp = await fetch(`${api}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    })
    if (!resp.ok) return null
    return await resp.json()
  } catch {
    return null
  }
}

async function main() {
  mkdirSync(SHOT_DIR, { recursive: true })
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  const checks = loadChecks(specArg)
  if (!checks.length) {
    console.error('标签文件里没有可用断言')
    process.exit(2)
  }

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

    console.log(`① 打开 ${APP_URL}`)
    // ★ TASK-019 起业务页面需要登录：先注入令牌再导航，
    //   否则会被路由守卫送到登录页，断言全错。
    //   令牌由调用方通过 DSH_TEST_TOKEN 环境变量传入（见 scripts/run_page_checks.ps1）。
    const testToken = process.env.DSH_TEST_TOKEN
    if (testToken) {
      // 先到同源页面才能写 localStorage（about:blank 是不同源）
      await cdp.send('Page.navigate', { url: new URL(APP_URL).origin + '/login' })
      await cdp.waitFor(`document.readyState === 'complete'`, { label: '登录页加载', timeout: 20_000 })
      // ★ 除了令牌还要写用户信息：顶栏与问候语读的是 auth_user 缓存，
      //   只写令牌会出现"顶栏未登录 / 问候语无名"（实测踩过）。
      const userObj = testToken.startsWith('{') ? null : await fetchMe(testToken)
      const userJs = userObj ? JSON.stringify(JSON.stringify(userObj)) : "'{}'"
      await cdp.eval(
        `localStorage.setItem('auth_token', ${JSON.stringify(testToken)});` +
          `localStorage.setItem('auth_user', ${userJs}); true`,
      )
      console.log(userObj ? `   （已注入登录令牌与用户：${userObj.display_name}）` : '   （已注入登录令牌）')
    }
    await cdp.send('Page.navigate', { url: APP_URL })
    // 等页面挂载。
    // ★ 阈值不能太高：登录页内容很短（innerText 不到 80 字符），
    //   用 >80 会把正常的登录页判成"没挂载"（实测踩过）。
    //   改为"有可见文本 + #root 有内容"，具体断言交给标签文件。
    await cdp.waitFor(
      `(document.body.innerText.trim().length > 10) && ((document.getElementById('root')?.children.length ?? 0) > 0)`,
      { label: '页面挂载', timeout: 30_000 },
    )
    await sleep(1500)

    console.log(`② 逐项断言（${checks.length} 项）`)
    const failures = []
    for (const [label, expr] of checks) {
      let ok = false
      try {
        ok = await cdp.eval(`!!(${expr})`)
      } catch (e) {
        ok = false
      }
      console.log(`   ${ok ? '✅' : '❌'} ${label}`)
      if (!ok) failures.push(label)
    }

    await cdp.shot(SHOT_NAME, SHOT_DIR)
    const evidenceFile = join(EVIDENCE_DIR, `${SHOT_NAME}-api.json`)
    writeFileSync(evidenceFile, JSON.stringify(captured, null, 2), 'utf8')
    console.log(`  API 证据 → ${evidenceFile}（${captured.length} 条）`)

    if (captured.length) {
      console.log('\n③ 真实 API 响应摘要')
      for (const c of captured.slice(0, 6)) {
        const short = c.url.replace('http://127.0.0.1:5173', '')
        console.log(`   HTTP ${c.status} ${short}  (${c.body.length} 字节)`)
      }
    }

    if (failures.length) {
      console.error(`\n❌ 页面验收有 ${failures.length} 项未通过：`)
      failures.forEach((f) => console.error(`   · ${f}`))
      process.exitCode = 1
      return
    }
    console.log(`\n🎉 ${SHOT_NAME} 结构验收全部通过`)
  } finally {
    clearTimeout(watchdog)
    close()
  }
}

main().catch((err) => {
  console.error('脚本失败：', err)
  process.exit(1)
})
