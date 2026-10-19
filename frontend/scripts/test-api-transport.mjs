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

// Exercise the actual CSV/database exporters: blob options must be transport config.
const exportsSeen = []
const fakeApi = {
  rawGet: (...args) => { exportsSeen.push(['get', ...args]); return Promise.resolve('csv') },
  rawPost: (...args) => { exportsSeen.push(['post', ...args]); return Promise.resolve('database') }
}
function loadBinding(rel, name) {
  const src = fs.readFileSync(new URL(rel, import.meta.url), 'utf8')
  const sf = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true)
  const binding = sf.statements.find(n => (ts.isClassDeclaration(n) && n.name.text === name) || (ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(sf) === name)))
  const code = ts.transpileModule(binding.getText(sf).replace(/^export /, '') + `\nglobalThis.binding = ${name};`, {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText
  const context = {ApiClient: fakeApi, URLSearchParams}
  vm.runInNewContext(code, context)
  return context.binding
}
const logs = loadBinding('../src/api/operationLogs.ts', 'OperationLogsApi')
assert.equal(await logs.exportOperationLogsCSV({action_type: 'analysis'}), 'csv')
assert.equal(exportsSeen.at(-1)[1], '/api/system/logs/export/csv?action_type=analysis')
assert.equal(exportsSeen.at(-1)[2], undefined)
assert.equal(exportsSeen.at(-1)[3].responseType, 'blob')
const database = loadBinding('../src/api/database.ts', 'databaseApi')
assert.equal(await database.exportData({sanitize: true}), 'database')
assert.equal(exportsSeen.at(-1)[2].sanitize, true)
assert.equal(exportsSeen.at(-1)[3].responseType, 'blob')

const balancesSource = fs.readFileSync(new URL('../src/utils/paperBalances.ts', import.meta.url), 'utf8')
const balancesTree = ts.createSourceFile('paperBalances.ts', balancesSource, ts.ScriptTarget.Latest, true)
const balancesFn = balancesTree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'currencyValue')
const balancesCode = ts.transpileModule(balancesFn.getText(balancesTree).replace('export function', 'function') + '\nglobalThis.balance = currencyValue;', {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText
const balances = {}
vm.runInNewContext(balancesCode, balances)
assert.equal(balances.balance(100, 'CNY'), 100)
assert.equal(balances.balance(100, 'HKD'), undefined)
assert.equal(balances.balance({CNY: 0, HKD: 50, USD: 1}, 'CNY'), 0)
assert.equal(balances.balance({CNY: 0, HKD: 50, USD: 1}, 'HKD'), 50)
console.log('Real exporter options and legacy/multi-currency balance contracts passed.')
