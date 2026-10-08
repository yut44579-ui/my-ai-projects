/**
 * 极简 CDP 客户端 + 本机 Edge 启动器（零新增依赖：只用 Node 自带的 WebSocket / fetch）。
 *
 * ★ 从 scripts/ui_screenshots.mjs（TASK-001）抽出来的共用实现 ——
 *   TASK-001 与 TASK-002 的截图脚本共用这一份，不再各抄一套。
 *
 * 用法：
 *   const { launchEdge, sleep } = await import('./lib/cdp.mjs')
 *   const { cdp, close } = await launchEdge({ profileDir, port })
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const EDGE_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
]

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export function findEdge() {
  const found = EDGE_CANDIDATES.find((p) => existsSync(p))
  if (!found) throw new Error('找不到 msedge.exe，请设置 EDGE_PATH 环境变量')
  return found
}

/** 极简 CDP 客户端：一个 WebSocket，按 id 匹配响应 */
export class Cdp {
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

  /** 截当前视口（不整页截：fixed 定位的弹窗会被平铺重复） */
  async shot(name, dir) {
    await sleep(400)
    const { data } = await this.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: false,
    })
    mkdirSync(dir, { recursive: true })
    const file = join(dir, `${name}.png`)
    writeFileSync(file, Buffer.from(data, 'base64'))
    console.log(`  截图 → ${file}`)
  }
}

/** 启动 headless Edge 并连上 CDP，返回 { cdp, close } */
export async function launchEdge({ profileDir, port, windowSize = '1500,1200' }) {
  const edge = spawn(
    findEdge(),
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profileDir}`,
      `--window-size=${windowSize}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const close = () => {
    try {
      // 连子进程一起收掉，别留下抢 profile 的僵尸
      spawn('taskkill', ['/PID', String(edge.pid), '/T', '/F'], { stdio: 'ignore' })
    } catch {
      edge.kill()
    }
  }

  try {
    let targets = null
    for (let i = 0; i < 40 && !targets; i++) {
      await sleep(250)
      try {
        const resp = await fetch(`http://127.0.0.1:${port}/json/list`)
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
    try {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: Number(windowSize.split(',')[0]),
        height: 1000,
        deviceScaleFactor: 1,
        mobile: false,
      })
    } catch {
      /* 某些 headless target 不支持，忽略 */
    }
    return { cdp, close }
  } catch (err) {
    close()
    throw err
  }
}
