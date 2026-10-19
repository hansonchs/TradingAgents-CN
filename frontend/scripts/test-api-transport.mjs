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

// Null valuations and invalid numbers stay unknown, while genuine zero stays zero.
const moneyFn = balancesTree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'formatPaperAmount')
vm.runInNewContext(ts.transpileModule(moneyFn.getText(balancesTree).replace('export function', 'function') + '\nglobalThis.money = formatPaperAmount;', {
  compilerOptions: {target: ts.ScriptTarget.ES2020}
}).outputText, balances)
assert.equal(balances.balance({CNY: 0, HKD: 1, USD: null}, 'USD'), null)
for (const value of [null, undefined, NaN, Infinity, -Infinity, false, '10']) {
  assert.equal(balances.money(value), '未知')
}
assert.equal(balances.money(0), '0.00')
assert.equal(balances.money(-12.5), '-12.50')
assert.equal(balances.money(1234.5, true), '1,234.50')

// Evaluate actual view interpolation expressions, so adding a zero fallback fails.
const { parse: parseSfc } = await import('@vue/compiler-sfc')
const { baseParse } = await import('@vue/compiler-dom')
for (const view of ['Dashboard', 'PaperTrading']) {
  const source = fs.readFileSync(new URL(`../src/views/${view}/index.vue`, import.meta.url), 'utf8')
  const template = baseParse(parseSfc(source).descriptor.template.content)
  const expressions = []
  function visit(node) {
    if (node.type === 5) expressions.push(node.content.content)
    for (const child of node.children ?? []) visit(child)
  }
  visit(template)
  const account = {cash: {CNY: 0, HKD: 0, USD: 0}, positions_value: {CNY: null, HKD: null, USD: null}, equity: {CNY: null, HKD: null, USD: null}}
  const context = {account, paperAccount: account, currencyValue: balances.balance, fmtAmount: balances.money, formatMoney: balances.money,
    row: {last_price: null, avg_cost: 20, quantity: 3, unrealized_pnl: null}}
  const totals = expressions.filter(expression => /(?:positions_value|equity)/.test(expression))
  assert.equal(totals.length, 6, `${view} must exercise all three currency totals`)
  for (const expression of totals) assert.equal(vm.runInNewContext(expression, context), '未知', `${view}: ${expression}`)
  if (view === 'PaperTrading') {
    const pnl = expressions.find(expression => expression.startsWith('fmtAmount(') && expression.includes('row.'))
    assert.ok(pnl)
    assert.equal(vm.runInNewContext(pnl, context), '未知', 'missing quote must not fabricate a full loss')
  }
}
console.log('Paper valuation unknown/zero/invalid-number and real view binding regressions passed.')

const cashFn = balancesTree.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'paperCashForMarket')
vm.runInNewContext(ts.transpileModule(cashFn.getText(balancesTree).replace('export function', 'function') + '\nglobalThis.cashForMarket = paperCashForMarket;', {
  compilerOptions: {target: ts.ScriptTarget.ES2020}
}).outputText, balances)
assert.equal(balances.cashForMarket(100, '美股'), undefined)
assert.equal(balances.cashForMarket(100, 'A股'), 100)
assert.equal(balances.cashForMarket({CNY: 0, HKD: 12, USD: 30}, '港股'), 12)
assert.equal(balances.cashForMarket({CNY: 0, HKD: 12, USD: 30}, '美股'), 30)
assert.equal(balances.cashForMarket({CNY: 0, HKD: 12, USD: 30}, 'unknown'), undefined)
for (const value of [null, undefined, NaN, Infinity, -1, false, '100']) {
  assert.equal(balances.cashForMarket({CNY: 0, HKD: 0, USD: value}, 'US'), undefined)
}

// Run both real one-click handlers up to the confirmation boundary with fake APIs.
for (const [view, binding] of [['Analysis/SingleAnalysis', 'goSimOrder'], ['Reports/ReportDetail', 'applyToTrading']]) {
  const source = fs.readFileSync(new URL(`../src/views/${view}.vue`, import.meta.url), 'utf8')
  const script = parseSfc(source).descriptor.scriptSetup.content
  const tree = ts.createSourceFile('view.ts', script, ts.ScriptTarget.Latest, true)
  const declaration = tree.statements.filter(ts.isVariableStatement).flatMap(node => [...node.declarationList.declarations]).find(node => node.name.getText(tree) === binding)
  assert.ok(declaration)
  const handler = ts.transpileModule('const ' + declaration.getText(tree) + '; globalThis.run = ' + binding, {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText
  for (const scenario of ['quote-error', 'quote-missing', 'quote-infinite', 'unknown-cash', 'missing-position-quantity', 'valid']) {
    const errors = []
    let forms = 0
    let orders = 0
    const cash = scenario === 'unknown-cash' ? {CNY: 1000, HKD: 1000, USD: null} : {CNY: 1000, HKD: 1000, USD: 1000}
    const report = {stock_symbol: 'AAA', symbol: 'AAA', analysis_status: 'completed'}
    const context = {
      analysisResults: {value: report}, report: {value: report}, analysisForm: {},
      parseRecommendation: () => ({action: scenario === 'missing-position-quantity' ? 'sell' : 'buy'}),
      paperApi: {getAccount: async () => ({success: true, data: {account: {cash}, positions: [{code: 'AAA', quantity: null}]}}), placeOrder: async () => {orders++}},
      stocksApi: {getQuote: async () => {
        if (scenario === 'quote-error') throw Error('offline fake provider failure')
        return {success: true, data: {price: scenario === 'quote-missing' ? null : scenario === 'quote-infinite' ? Infinity : 5}}
      }},
      getMarketByStockCode: () => '美股', paperCashForMarket: balances.cashForMarket,
      getCashByCurrency: account => balances.cashForMarket(account.cash, '美股'),
      ElMessage: {error: value => errors.push(value), warning: value => errors.push(value)},
      reactive: () => {forms++; throw Error('test confirmation boundary')}, console: {error() {}, warn() {}}
    }
    vm.runInNewContext(handler, context)
    await context.run()
    assert.equal(forms, scenario === 'valid' ? 1 : 0, `${view}: ${scenario}`)
    assert.equal(orders, 0)
    if (scenario.startsWith('quote-')) assert.ok(errors.some(message => message.includes('实时价格不可用')))
    if (scenario === 'unknown-cash') assert.ok(errors.some(message => message.includes('余额不可用')))
  }
}
console.log('Both actual one-click handlers reject missing prices, unknown currency cash and unknown holdings before confirmation.')
