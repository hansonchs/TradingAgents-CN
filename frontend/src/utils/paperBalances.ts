import type { CurrencyAmount, ValuationAmount } from '@/api/paper'

/** Legacy scalar balances were CNY; their missing foreign balances stay unknown. */
export function currencyValue(value: CurrencyAmount | ValuationAmount | number | null | undefined, currency: keyof CurrencyAmount): number | null | undefined {
  return typeof value === 'number' ? (currency === 'CNY' ? value : undefined) : value?.[currency]
}

/** Missing valuations must not become zero or a fabricated loss. */
export function formatPaperAmount(value: number | null | undefined, grouped = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '未知'
  const amount = value.toFixed(2)
  return grouped ? amount.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : amount
}

/** A missing/legacy foreign balance cannot fund a simulated order. */
export function paperCashForMarket(value: CurrencyAmount | ValuationAmount | number | null | undefined, market: string): number | undefined {
  const currencies: Record<string, keyof CurrencyAmount> = {CN: 'CNY', HK: 'HKD', US: 'USD', 'A股': 'CNY', '港股': 'HKD', '美股': 'USD'}
  const currency = currencies[market]
  if (!currency) return undefined
  const amount = currencyValue(value, currency)
  return typeof amount === 'number' && Number.isFinite(amount) && amount >= 0 ? amount : undefined
}
