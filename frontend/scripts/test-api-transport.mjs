// Exercise the actual ApiClient methods with a fake HTTP transport, offline.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const source = fs.readFileSync(new URL('../src/api/request.ts', import.meta.url), 'utf8')
const tree = ts.createSourceFile('request.ts', source, ts.ScriptTarget.Latest, true)
const node = tree.statements.find(n => ts.isClassDeclaration(n) && n.name.text === 'ApiClient')
assert.ok(node)
const code = ts.transpileModule(node.getText(tree).replace('export class', 'class') + '\n globalThis.Client = ApiClient;', {
  compilerOptions: { target: ts.ScriptTarget.ES2020 }
}).outputText
const body = { settings: 'raw body' }
const calls = []
const request = Object.fromEntries(['get', 'post', 'put', 'patch', 'delete'].map(method => [method, (...args) => {
  calls.push({method, args})
  return Promise.resolve(body)
}]))
const sandbox = { request }
vm.runInNewContext(code, sandbox)
for (const method of ['Get', 'Post', 'Put', 'Patch', 'Delete']) {
  const result = method === 'Delete'
    ? await sandbox.Client.rawDelete('/test', { skipAuth: true })
    : await sandbox.Client[`raw${method}`]('/test', undefined, { skipAuth: true })
  assert.equal(result, body)
  assert.equal(calls.at(-1).method, method.toLowerCase())
  assert.equal(calls.at(-1).args.at(-1).skipAuth, true)
}
await sandbox.Client.rawGet('/test', {page: 1}, {skipAuth: true})
assert.equal(calls.at(-1).args[1].skipAuth, true)
assert.equal(calls.at(-1).args[1].params.page, 1)
const envelope = {success: true, data: {value: 1}, message: 'ok'}
request.get = () => Promise.resolve(envelope)
assert.equal(await sandbox.Client.get('/enveloped'), envelope)
console.log('ApiClient raw body and envelope transport checks passed (offline).')

const formatter = fs.readFileSync(new URL('../src/utils/researchDecision.ts', import.meta.url), 'utf8')
const formatterCode = ts.transpileModule(formatter.replace('export function', 'function') + '\nglobalThis.format = formatResearchPercent;', {
  compilerOptions: { target: ts.ScriptTarget.ES2020 }
}).outputText
const display = {}
vm.runInNewContext(formatterCode, display)
for (const value of [null, undefined, NaN, Infinity, '0', false, -1, 2]) {
  assert.equal(display.format(value), '未知')
}
assert.equal(display.format(0), '0.0%')
assert.equal(display.format(0.3), '30.0%')
console.log('Unknown research risk remains unknown in display checks.')
