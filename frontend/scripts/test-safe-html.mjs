import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { JSDOM } from 'jsdom'
import createDOMPurify from 'dompurify'
const dom = new JSDOM('')
const purifier = createDOMPurify(dom.window)
const source = fs.readFileSync(new URL('../src/utils/safeHtml.ts', import.meta.url), 'utf8')
const tree = ts.createSourceFile('safeHtml.ts', source, ts.ScriptTarget.Latest, true)
const fn = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'sanitizeHtml')
const script = ts.transpileModule(fn.getText(tree).replace('export function', 'function') + '\nglobalThis.clean = sanitizeHtml;', {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText
const sandbox = {DOMPurify: purifier}
vm.runInNewContext(script, sandbox)
const payload = '<h2>Research</h2><img src="x" onerror="alert(1)"><a href="javascript:alert(2)">source</a><script>alert(3)</script><svg onload="alert(4)"></svg>'
const parsed = new JSDOM(sandbox.clean(payload)).window.document
assert.equal(parsed.querySelector('h2')?.textContent, 'Research')
assert.equal(parsed.querySelector('script'), null)
assert.equal(parsed.querySelector('[onerror],[onload]'), null)
assert.equal(parsed.querySelector('a')?.hasAttribute('href'), false)
assert.equal(sandbox.clean(null), '')
for (const file of ['../components/Global/TaskResultDialog.vue','../components/Global/TaskReportDialog.vue','../views/Reports/ReportDetail.vue','../views/Queue/index.vue','../views/Analysis/SingleAnalysis.vue','../views/Stocks/Detail.vue','../views/Learning/Article.vue']) {
 const s=fs.readFileSync(new URL(file.replace('../','../src/'), import.meta.url),'utf8')
 assert.ok([...s.matchAll(/v-html="([^"]+)"/g)].every(m => m[1].startsWith('sanitizeHtml(')), file)
}
dom.window.close()
console.log('Actual sanitizer rejects active HTML in every current report/news HTML sink.')

const linkFn = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'safeExternalUrl')
vm.runInNewContext(ts.transpileModule(linkFn.getText(tree).replace('export function', 'function') + '\nglobalThis.safe = safeExternalUrl;', {compilerOptions:{target:ts.ScriptTarget.ES2020}}).outputText, Object.assign(sandbox, {URL}))
for (const input of ['javascript:alert(1)', 'data:text/html,evil', '//example.test', null]) assert.equal(sandbox.safe(input), undefined)
assert.equal(sandbox.safe('https://example.test/news'), 'https://example.test/news')
console.log('Provider-controlled source links allow HTTP(S) only.')
