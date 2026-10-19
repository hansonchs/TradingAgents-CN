import type { CurrencyAmount } from '@/api/paper'

/** Legacy scalar balances were CNY; their missing foreign balances stay unknown. */
export function currencyValue(value: CurrencyAmount | number | null | undefined, currency: keyof CurrencyAmount): number | undefined {
  return typeof value === 'number' ? (currency === 'CNY' ? value : undefined) : value?.[currency]
}
