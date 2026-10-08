/**
 * 找出手机端横向溢出的元凶。
 * ★ 光看"溢出多少 px"没用，得知道是哪个元素撑宽的才能修。
 */
import { launchEdge } from './lib/cdp.mjs'

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173'
const PAGES = ['/', '/profile', '/reports']

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9351,
  windowSize: '375,812',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })

  const testToken = process.env.DSH_TEST_TOKEN
  if (testToken) {
    await cdp.send('Page.navigate', { url: BASE + '/login' })
    await new Promise((r) => setTimeout(r, 2500))
    const me = await (
      await fetch('http://127.0.0.1:8000/api/auth/me', {
        headers: { Authorization: `Bearer ${testToken}` },
      })
    ).json()
    await cdp.eval(
      `localStorage.setItem('auth_token', ${JSON.stringify(testToken)});` +
        `localStorage.setItem('auth_user', ${JSON.stringify(JSON.stringify(me))}); true`,
    )
  }

  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 375,
    height: 812,
    deviceScaleFactor: 2,
    mobile: true,
  })

  for (const p of PAGES) {
    await cdp.send('Page.navigate', { url: BASE + p })
    await new Promise((r) => setTimeout(r, 3500))

    const info = await cdp.eval(`(() => {
      const vw = 375
      const offenders = []
      document.querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect()
        if (r.width > vw + 1 || r.right > vw + 1) {
          // 只报"自己宽"或"右边界超出"的元素，并跳过纯容器
          const cls = (el.className && typeof el.className === 'string') ? el.className : ''
          offenders.push({
            tag: el.tagName.toLowerCase(),
            cls: cls.split(' ').filter(c => c.startsWith('ant-')).slice(0, 3).join('.'),
            w: Math.round(r.width),
            right: Math.round(r.right),
            overflowX: getComputedStyle(el).overflowX,
            text: (el.innerText || '').slice(0, 24).replace(/\\s+/g,' '),
          })
        }
      })
      // 只保留最外层的几个（子元素通常跟着父元素一起超）
      return {
        scrollW: document.documentElement.scrollWidth,
        offenders: offenders.slice(0, 12),
      }
    })()`)

    console.log(`\n--- ${p} ---  文档宽 ${info.scrollW}px`)
    if (!info.offenders.length) {
      console.log('   无溢出元素')
      continue
    }
    for (const o of info.offenders) {
      console.log(
        `   ${o.tag}${o.cls ? '.' + o.cls : ''}  宽${o.w} 右边界${o.right}  overflowX=${o.overflowX}  「${o.text}」`,
      )
    }
  }
} finally {
  await close()
}
