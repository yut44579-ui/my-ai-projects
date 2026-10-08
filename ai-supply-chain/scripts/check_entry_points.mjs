/**
 * 只读检查：现在线上「哪里能点到系统」、哪里点不到。
 * 不改任何东西，只为把问题说准。
 */
import { launchEdge } from './lib/cdp.mjs'

const SITE = 'http://43.134.58.30:8081'
const { cdp, close } = await launchEdge({
  profileDir: 'D:\\处理qa\\cdp_profile',
  port: 9380,
  windowSize: '390,844',
})

try {
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  })

  // 首页：项目卡片上有什么可点的
  await cdp.send('Page.navigate', { url: SITE + '/' })
  await new Promise((r) => setTimeout(r, 4000))
  const home = await cdp.eval(`(() => {
    const cards = [...document.querySelectorAll('.pcard')]
    return {
      cardCount: cards.length,
      cards: cards.map(c => ({
        title: (c.querySelector('h3') || {}).innerText || '?',
        links: [...c.querySelectorAll('a')].map(a => a.innerText.trim() + ' → ' + (a.getAttribute('href') || '')),
      })),
      anyTo8082: [...document.querySelectorAll('a')].some(a => (a.getAttribute('href') || '').includes(':8082')),
    }
  })()`)
  console.log('【首页】项目卡片数:', home.cardCount)
  for (const c of home.cards) {
    console.log('  ·', c.title)
    for (const l of c.links) console.log('      可点:', l)
  }
  console.log('  首页上有指向 :8082 的链接吗 →', home.anyTo8082 ? '有' : '❌ 没有')

  // 详情页：进入系统的入口在哪
  await cdp.send('Page.navigate', { url: SITE + '/projects/ai-supply-chain.html' })
  await new Promise((r) => setTimeout(r, 4000))
  const detail = await cdp.eval(`(() => {
    const links = [...document.querySelectorAll('a')].map(a => ({
      t: a.innerText.trim().slice(0, 20),
      h: a.getAttribute('href') || '',
      top: Math.round(a.getBoundingClientRect().top + window.scrollY),
      visible: a.getBoundingClientRect().width > 0,
    }))
    const btn = links.find(l => l.h.includes(':8082') && !l.h.includes('/customers'))
    return {
      to8082: links.filter(l => l.h.includes(':8082')).map(l => l.t + ' → ' + l.h),
      mainBtnTop: btn ? btn.top : null,
      pageHeight: document.documentElement.scrollHeight,
      allLinks: links.slice(0, 12).map(l => l.t + ' → ' + l.h.slice(0, 50)),
    }
  })()`)
  console.log('\n【AI供应链 详情页】')
  console.log('  页面总高:', detail.pageHeight, 'px（手机要滚很久）')
  console.log('  指向 :8082 的链接:')
  for (const l of detail.to8082) console.log('    ·', l)
  console.log('  主按钮位置: 距顶部约', detail.mainBtnTop, 'px')

  // 销售 Agent 详情页：有没有入口
  await cdp.send('Page.navigate', { url: SITE + '/projects/sales-report-agent.html' })
  await new Promise((r) => setTimeout(r, 4000))
  const sales = await cdp.eval(`(() => {
    const t = document.body.innerText || ''
    const links = [...document.querySelectorAll('a')].map(a => ({
      t: a.innerText.trim().slice(0, 24), h: a.getAttribute('href') || '',
    }))
    return {
      to8082: links.filter(l => l.h.includes(':8082')).length,
      toAnyPort: links.filter(l => /:\\d{4}/.test(l.h)).map(l => l.t + ' → ' + l.h),
      saysLocalOnly: t.includes('本机真实运行') || t.includes('面试时可现场启动'),
      chips: links.filter(l => !l.h || l.h === '#').map(l => l.t).slice(0, 8),
    }
  })()`)
  console.log('\n【销售报表 Agent 详情页】')
  console.log('  指向外部的链接:', sales.toAnyPort.length ? sales.toAnyPort : '❌ 一个都没有')
  console.log('  写着"只在本机运行":', sales.saysLocalOnly ? '是（所以访客进不去）' : '否')
  console.log('  不可点的标签:', sales.chips.join(' / ') || '无')
} finally {
  await close()
}
