/**
 * TASK-002 验收截图：驱动本机 Edge 跑真实前端（列表页 → 搜索空态 → 来源筛选空集 → 客户详情），
 * 顺手把浏览器真实发出的 /api/customers* 响应体存下来当证据。
 *
 * ★ 零新增依赖：复用 scripts/lib/cdp.mjs（与 TASK-001 截图脚本同一套 CDP 客户端）。
 * ★ 走的是真实前端：搜索是真敲、行是真点、响应是后端真返回的。
 * ★ 本脚本只读：不导入、不提交，不会往库里写任何数据。
 *
 * 用法（后端 8000、前端 5173 都已启动）：
 *   node scripts/ui_screenshots_task002.mjs
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
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5173/customers'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9500 + (process.pid % 400)

/** 给受 React 控制的输入框赋值（必须走原生 setter，否则 React 收不到变更） */
const setInput = (selector, value) => `(() => {
  const el = document.querySelector(${JSON.stringify(selector)})
  if (!el) return false
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  setter.call(el, ${JSON.stringify(value)})
  el.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`

/** 点搜索框右侧的放大镜按钮（里面的图标没有文字，只能按选择器点；AntD v6 是 .ant-input-search-btn） */
const clickSearchButton = `(() => {
  const btn = document.querySelector('.ant-input-search-btn')
  if (btn) {
    btn.click()
    return true
  }
  // 兜底：真敲一次回车（Input.Search 的 onSearch 同样会触发）
  const input = document.querySelector('input[placeholder*="搜索"]')
  if (!input) return false
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
  input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
  return true
})()`

/** 搜索：敲词 + 点搜索按钮 + 等结果 */
async function search(cdp, term, expect) {
  await cdp.eval(setInput('input[placeholder*="搜索"]', term))
  if (!(await cdp.eval(clickSearchButton))) throw new Error('点不到搜索按钮')
  await cdp.waitFor(expect, { label: `搜索结果（${term || '清空'}）` })
}

/** 用真实鼠标点某个元素（AntD 的下拉只认真实鼠标事件，JS .click() 打不开） */
async function clickElement(cdp, selector, index = 0) {
  const box = await cdp.eval(
    `(() => {
       const el = document.querySelectorAll(${JSON.stringify(selector)})[${index}]
       if (!el) return null
       const r = el.getBoundingClientRect()
       return [r.x + r.width / 2, r.y + r.height / 2].join(',')
     })()`,
  )
  if (!box) throw new Error(`找不到元素：${selector}[${index}]`)
  const [x, y] = box.split(',').map(Number)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
  await sleep(400)
}

/** 下拉选来源：打开下拉 → 点选项 */
async function pickSourceType(cdp, label) {
  await clickElement(cdp, '.ant-select', 0)
  await cdp.waitFor("document.querySelectorAll('.ant-select-item-option').length > 0", {
    label: '来源下拉',
  })
  const box = await cdp.eval(
    `(() => {
       const hit = [...document.querySelectorAll('.ant-select-item-option')]
         .find(n => n.textContent.trim() === ${JSON.stringify(label)})
       if (!hit) return null
       const r = hit.getBoundingClientRect()
       return [r.x + r.width / 2, r.y + r.height / 2].join(',')
     })()`,
  )
  if (!box) throw new Error(`下拉里没有「${label}」`)
  const [x, y] = box.split(',').map(Number)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
  await sleep(500)
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
        /* 取不到响应体的（重定向等）忽略 */
      }
    })

    // ① 列表页：四个指标卡 + TEST banner + 表格
    console.log('① 打开客户列表…')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelector('.ant-menu')", { label: '页面框架' })
    await cdp.waitFor(
      "document.body.innerText.includes('客户总数') && document.body.innerText.includes('本周新增')",
      { label: '四个指标卡' },
    )
    await cdp.waitFor("document.querySelectorAll('tbody tr.ant-table-row').length > 0", {
      label: '客户表格',
    })
    await sleep(800)
    await cdp.diagnose('列表页')
    await cdp.shot('task002-customers-stats', SHOT_DIR)

    // ② 搜索一个不存在的词 → 空态，页面上不许出现任何业务数字
    console.log('② 搜索一个不存在的词…')
    await search(cdp, 'zzz-不存在的客户-zzz', "document.body.innerText.includes('暂无客户数据')")
    await sleep(600)
    await cdp.diagnose('搜索空态')
    await cdp.shot('task002-customers-empty', SHOT_DIR)

    // ③ 来源筛选 = REAL（库里一条都没有）→ 指标卡必须是 NO_DATA 语义而不是 0
    console.log('③ 来源筛选 REAL（空集）…')
    await search(cdp, '', "document.querySelectorAll('tbody tr.ant-table-row').length > 0")
    await sleep(400)
    await pickSourceType(cdp, 'REAL 真实')
    await cdp.waitFor("document.body.innerText.includes('暂无数据')", { label: 'REAL 空集' })
    await sleep(600)
    await cdp.diagnose('REAL 空集')
    await cdp.shot('task002-customers-real-nodata', SHOT_DIR)

    // ④ 回到全部来源，点第一行 → 客户详情
    console.log('④ 点一行进入客户详情…')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelectorAll('tbody tr.ant-table-row').length > 0", {
      label: '客户表格',
    })
    await sleep(600)
    const firstId = await cdp.eval(
      "(() => { const tr = document.querySelector('tbody tr.ant-table-row'); if (!tr) return null; return tr.getAttribute('data-row-key') })()",
    )
    const clicked = await cdp.eval(
      `(() => {
         const tr = document.querySelector('tbody tr.ant-table-row')
         if (!tr) return false
         tr.click()
         return true
       })()`,
    )
    if (!clicked) throw new Error('点不到表格行')
    await cdp.waitFor("document.body.innerText.includes('来源与可追溯')", { label: '客户详情页' })
    await cdp.waitFor("document.body.innerText.includes('暂无沟通记录')", { label: '沟通记录空态' })
    await sleep(800)

    const url = await cdp.eval('location.pathname')
    console.log(`  详情页路径：${url}（第一行客户 id=${firstId}）`)
    // 详情页较长：把视口加高，一屏截全（基本信息 / 来源 / 事件流 / 三个空态区块）
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1500,
      height: 1900,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await sleep(600)
    await cdp.diagnose('客户详情')
    await cdp.shot('task002-customer-detail', SHOT_DIR)
    await cdp.shot('task002-customer-detail-top', SHOT_DIR)

    writeFileSync(
      join(EVIDENCE_DIR, 'task002-api-calls.json'),
      JSON.stringify(captured, null, 2),
    )
    console.log(`  浏览器真实调用 ${captured.length} 条 → docs/evidence/task002-api-calls.json`)
  } finally {
    close()
  }
  process.exit(0)
}

main().catch((err) => {
  console.error('失败：', err.message)
  process.exit(1)
})
