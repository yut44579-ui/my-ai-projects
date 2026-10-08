/**
 * 个人网站渲染检查（唐宇作品集）。
 *
 * ★ 为什么要检查：这个站点是 JS 渲染的（#pgrid / #shotsStrip 由 app.js 填充），
 *   资源 404 的话页面会是一片空白 —— 光看 HTTP 200 发现不了。
 *   所以这里等渲染完，数卡片、数截图、查坏图。
 */
import { launchEdge } from './lib/cdp.mjs'

const URL_ = process.argv[2] ?? 'http://127.0.0.1:8899/'
const OUT = process.argv[3] ?? 'D:\\biz-assistant-int\\docs\\screenshots'
const NAME = process.argv[4] ?? 'portfolio'
/** 可选：截图前滚动到某个选择器（便于拍下半部分的项目区） */
const SCROLL_TO = process.argv[5] ?? ''

const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9334,
  windowSize: '1440,1200',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  // ★ 必须禁用缓存：站点是静态文件，浏览器会缓存 assets/data.js，
  //   改完内容刷新看到的还是旧的 —— 会误判成"改动没生效"。
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Page.navigate', { url: URL_ })
  await new Promise((r) => setTimeout(r, 4000))
  // 再刷一次，确保拿到的是最新资源
  await cdp.send('Page.reload', { ignoreCache: true })
  await new Promise((r) => setTimeout(r, 3500))

  const info = await cdp.eval(`(() => {
    const txt = document.body.innerText || ''
    const pgrid = document.getElementById('pgrid')
    const shots = document.getElementById('shotsStrip')
    return {
      title: document.title,
      len: txt.length,
      cards: pgrid ? pgrid.children.length : -1,
      shots: shots ? shots.children.length : -1,
      hasName: txt.includes('唐宇'),
      sections: [...document.querySelectorAll('section[id]')].map(s => s.id),
      snippets: txt.slice(0, 500),
      brokenImgs: [...document.images].filter(i => !i.complete || i.naturalWidth === 0)
        .map(i => i.getAttribute('src')).slice(0, 8),
      scriptErrs: window.__errs || [],
    }
  })()`)

  console.log('  标题     :', info.title)
  console.log('  正文字数 :', info.len)
  console.log('  项目卡片 :', info.cards)
  console.log('  截图条   :', info.shots)
  console.log('  含"唐宇" :', info.hasName)
  console.log('  区块     :', info.sections.join(', '))
  console.log('  坏图     :', info.brokenImgs.length ? info.brokenImgs.join(', ') : '无')
  console.log('  正文开头 :', info.snippets.replace(/\s+/g, ' ').slice(0, 260))

  if (SCROLL_TO) {
    await cdp.eval(`(() => {
      const el = document.querySelector(${JSON.stringify(SCROLL_TO)})
      if (el) el.scrollIntoView({ block: 'start' })
      return !!el
    })()`)
    await new Promise((r) => setTimeout(r, 1200))
  }

  await cdp.shot(NAME, OUT)
  console.log('  截图已存 :', OUT + '\\' + NAME + '.png')
} finally {
  await close()
}
