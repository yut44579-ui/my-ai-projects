/**
 * TASK-001 验收截图工具：驱动本机 Edge（headless + CDP）跑一遍真实导入流程，
 * 顺手把浏览器真实发出的 /api/imports/* 响应体存下来当证据。
 *
 * ★ 零新增依赖：只用 Node 22 自带的 WebSocket + fetch，不装 playwright/puppeteer。
 * ★ 走的是真实前端：点击是真点击、文件是真选，响应是后端真返回的。
 *
 * 用法（后端 8000、前端 5173 都已启动）：
 *   node scripts/ui_screenshots.mjs
 */

import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
// ★ 每次跑用独立的 profile 目录与端口：避免和上一次残留的 Edge 进程抢同一个 profile
//   （抢了会表现为"启动后没有调试端口"，脚本干等）
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', RUN_ID)
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5173/customers'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9300 + (process.pid % 400)

const EDGE_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function findEdge() {
  const found = EDGE_CANDIDATES.find((p) => existsSync(p))
  if (!found) throw new Error('找不到 msedge.exe，请设置 EDGE_PATH 环境变量')
  return found
}

/** 极简 CDP 客户端：一个 WebSocket，按 id 匹配响应 */
class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.handlers = new Map()
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
        return
      }
      if (msg.method) {
        for (const fn of this.handlers.get(msg.method) ?? []) fn(msg.params)
      }
    })
  }

  on(method, fn) {
    this.handlers.set(method, [...(this.handlers.get(method) ?? []), fn])
  }

  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`CDP 超时：${method}`))
        }
      }, 30000)
    })
  }

  async eval(expression) {
    const { result } = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    return result.value
  }

  /** 轮询等待页面条件成立 */
  async waitFor(expression, { timeout = 15000, label = expression } = {}) {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      if (await this.eval(`(() => { try { return !!(${expression}) } catch (e) { return false } })()`)) {
        return true
      }
      await sleep(150)
    }
    throw new Error(`等待超时：${label}`)
  }

  async clickText(text, { tag = 'button' } = {}) {
    const clicked = await this.eval(
      `(() => {
         const nodes = [...document.querySelectorAll('${tag}, [role=button]')]
         const hit = nodes.find(n => (n.textContent || '').includes(${JSON.stringify(text)}) && !n.disabled)
         if (!hit) return false
         hit.click()
         return true
       })()`,
    )
    if (!clicked) throw new Error(`点不到「${text}」`)
    await sleep(200)
  }

  /** 截图前先自检：页面是不是真的处在预期状态，避免截到旧帧 */
  async diagnose(label) {
    const state = await this.eval(
      `(() => {
         const modal = document.querySelector('.ant-modal')
         return JSON.stringify({
           modals: document.querySelectorAll('.ant-modal').length,
           modalVisible: modal ? getComputedStyle(modal.closest('.ant-modal-wrap') || modal).display !== 'none' : false,
           buttons: [...document.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean),
           text: (modal || document.body).innerText.slice(0, 60).replace(/\\n/g, ' / '),
         })
       })()`,
    )
    console.log(`  [自检 ${label}] ${state}`)
  }

  async shot(name) {
    // ★ 不整页截：fixed 定位的弹窗会被平铺重复；按视口截才是用户真正看到的画面
    await sleep(400)
    const { data } = await this.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: false,
    })
    const file = join(SHOT_DIR, `${name}.png`)
    writeFileSync(file, Buffer.from(data, 'base64'))
    console.log(`  截图 → docs/screenshots/${name}.png`)
  }
}

async function main() {
  mkdirSync(SHOT_DIR, { recursive: true })
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  // 看门狗：卡住就退出，不要干等
  const watchdog = setTimeout(() => {
    console.error('看门狗超时（180s），强制退出')
    process.exit(2)
  }, 180_000)
  watchdog.unref?.()

  const edge = spawn(
    findEdge(),
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${TMP_PROFILE}`,
      '--window-size=1500,1200',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  try {
    // 等 DevTools 端口起来
    let targets = null
    for (let i = 0; i < 40 && !targets; i++) {
      await sleep(250)
      try {
        const resp = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)
        const list = await resp.json()
        targets = list.filter((t) => t.type === 'page')
        if (!targets.length) targets = null
      } catch {
        targets = null
      }
    }
    if (!targets) throw new Error('Edge 调试端口没起来')

    const ws = new WebSocket(targets[0].webSocketDebuggerUrl)
    await new Promise((res, rej) => {
      const timer = setTimeout(() => rej(new Error('WebSocket 连接超时')), 15000)
      ws.addEventListener('open', () => {
        clearTimeout(timer)
        res()
      })
      ws.addEventListener('error', (e) => {
        clearTimeout(timer)
        rej(new Error(`WebSocket 出错：${e.message ?? e.type}`))
      })
    })
    const cdp = new Cdp(ws)

    // 采集浏览器真实发出的导入接口响应体
    const captured = []
    const methods = new Map() // requestId -> HTTP 方法（responseReceived 里取不到，得从请求事件拿）
    await cdp.send('Network.enable')
    await cdp.send('Page.enable')
    cdp.on('Network.requestWillBeSent', (params) => {
      methods.set(params.requestId, params.request.method)
    })
    cdp.on('Network.responseReceived', async (params) => {
      const url = params.response.url
      if (!url.includes('/api/imports/') && !url.includes('/api/customers')) return
      try {
        const { body, base64Encoded } = await cdp.send('Network.getResponseBody', {
          requestId: params.requestId,
        })
        captured.push({
          method: methods.get(params.requestId) ?? '?',
          url,
          status: params.response.status,
          body: base64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body,
        })
      } catch {
        /* 有些响应体取不到（重定向/预检），忽略 */
      }
    })

    // 视口由 --window-size 决定；这里只是尽力设一次，不支持就跳过
    try {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: 1500,
        height: 1000,
        deviceScaleFactor: 1,
        mobile: false,
      })
    } catch {
      /* 某些 headless target 不支持，忽略 */
    }

    console.log('打开客户页…')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelector('.ant-menu')", { label: '页面框架' })
    await sleep(1200) // 等列表请求回来

    // ① 空态（零数字）
    await cdp.waitFor("document.body.innerText.includes('暂无客户数据') || document.querySelectorAll('tbody tr').length > 0")
    console.log('① 空态')
    await cdp.diagnose('空态')
    await cdp.shot('customers-empty')

    const importOnce = async (filePath, shotPrefix, fileLabel) => {
      console.log(`导入 ${fileLabel} …`)
      await cdp.clickText('导入 Excel/CSV')
      await cdp.waitFor("document.querySelector('input[type=file]')", { label: '上传框' })

      // 真选文件（CDP 会触发 change 事件，等同用户手选）
      const { root } = await cdp.send('DOM.getDocument', { depth: 1 })
      const { nodeId } = await cdp.send('DOM.querySelector', {
        nodeId: root.nodeId,
        selector: 'input[type=file]',
      })
      await cdp.send('DOM.setFileInputFiles', { files: [filePath], nodeId })
      await sleep(500)

      await cdp.clickText('解析预览')
      await cdp.waitFor("document.body.innerText.includes('列映射确认')", {
        label: '映射确认弹窗',
      })
      await sleep(800)
      await cdp.diagnose(`${fileLabel} 映射`)
      await cdp.shot(`${shotPrefix}-mapping`)

      await cdp.clickText('确认映射并导入')
      await cdp.waitFor(
        "document.body.innerText.includes('导入完成') || document.body.innerText.includes('部分导入')",
        { label: '导入结果' },
      )
      await sleep(1000)
      // 把弹窗滚到底，确保截图能看到跳过明细
      await cdp.eval("(() => { const b = document.querySelector('.ant-modal-body'); if (b) b.scrollTop = b.scrollHeight; return true })()")
      await cdp.diagnose(`${fileLabel} 结果`)
      await cdp.shot(`${shotPrefix}-result`)
    }

    // ② 干净样本：8 行 → SUCCESS
    await importOnce(join(ROOT, 'tests/fixtures/customers_sample_TEST_clean.csv'), 'import-clean', '干净样本(8 行)')
    await cdp.clickText('继续导入下一个文件')

    // ③ 脏样本：PARTIAL（黄色警告 + 跳过明细 + 未映射列）
    await importOnce(join(ROOT, 'tests/fixtures/customers_sample_TEST_messy.csv'), 'import-messy', '脏样本(GB18030)')

    // ④ 列表：TEST banner + 来源 Tag。直接重新加载页面 —— 关弹窗的点击太脆，
    //    重载还能顺带证明数据是真落库了（数据来自后端而不是前端内存）
    console.log('④ 重新加载列表（TEST banner）')
    await cdp.send('Page.navigate', { url: APP_URL })
    await cdp.waitFor("document.querySelector('.ant-menu')", { label: '页面框架' })
    await cdp.waitFor("document.body.innerText.includes('测试导入数据')", { label: 'TEST banner' })
    await cdp.waitFor("document.querySelectorAll('tbody tr').length > 0", { label: '客户表格' })
    await sleep(1000)
    await cdp.diagnose('列表')
    await cdp.shot('customers-with-test-banner')

    writeFileSync(join(EVIDENCE_DIR, 'browser-api-calls.json'), JSON.stringify(captured, null, 2))
    console.log(`  抓到的接口响应 ${captured.length} 条 → docs/evidence/browser-api-calls.json`)
  } finally {
    // 连子进程一起收掉，别留下抢 profile 的僵尸
    try {
      spawn('taskkill', ['/PID', String(edge.pid), '/T', '/F'], { stdio: 'ignore' })
    } catch {
      edge.kill()
    }
  }
  process.exit(0)
}

main().catch((err) => {
  console.error('失败：', err.message)
  process.exit(1)
})
