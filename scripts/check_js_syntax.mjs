/** 检查 web/ 下所有界面里 <script> 块的 JS 语法。
 *
 *  ★ 自动发现文件，而不是写死列表 ——
 *    写死的话，新加的页面（比如 site.html）永远不会被检查到。
 *    这类"检查器覆盖不到新东西"的问题很隐蔽：
 *    它不报错，只是**安静地不检查**。
 */
import fs from 'node:fs'

const DIR = 'D:/ai-kefu/web/'
const files = fs.readdirSync(DIR).filter((f) => f.endsWith('.html')).sort()
let bad = 0
if (!files.length) {
  console.log('  ❌ web/ 下没有 html 文件')
  process.exit(1)
}
for (const f of files) {
  const t = fs.readFileSync(DIR + f, 'utf8')
  const blocks = [...t.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  if (!blocks.length) {
    console.log(`  ⚠ ${f}: 没有 script 块`)
    continue
  }
  let ok = true
  for (let i = 0; i < blocks.length; i++) {
    try {
      new Function(blocks[i][1])
    } catch (e) {
      ok = false
      bad++
      console.log(`  ❌ ${f} 第 ${i + 1} 个 script 块: ${e.message}`)
    }
  }
  if (ok) console.log(`  ✅ ${f}: ${blocks.length} 个 script 块语法通过`)
}
console.log(bad ? `\n  ${bad} 个块有语法错误` : `\n  ${files.length} 个界面全部通过`)
process.exit(bad ? 1 : 0)
