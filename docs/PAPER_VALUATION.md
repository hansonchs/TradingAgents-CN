# Local simulator: unknown valuations and invalid numeric state

The `/api/paper` feature is an application-local virtual ledger. It does not
connect to IB, Futu, or another broker, and its fills are not broker paper fills.

When a held position has no finite positive quote, `last_price`, `market_value`,
and `unrealized_pnl` remain null. The affected currency's total positions value
and equity are null rather than zero or cash-only equity. An unrelated currency
remains available. A genuine empty position/currency can still have zero value.
Valid quotes returning later restore the estimate. Aggregate overflow also stays
unknown; it must not produce invalid JSON or a partial total.

Legacy non-finite balances, costs and malformed quantities are returned as unknown
numeric fields instead of causing JSON serialization failures. The dashboard and
paper page display unknown amounts explicitly. They use server PnL rather than
calculating a full loss from a zero replacement price. `valuation_status` indicates
incomplete numeric inputs; `available` does not establish freshness, executable
prices, validated event risk, or an audited portfolio valuation.

Simulation orders reject invalid/nonpositive quotes, nonpositive or overflowing
notionals, invalid commission rates/minimums, non-finite or negative calculated
fees, invalid existing balances/cost/quantity state, and overflowing derived
updates. Relevant state and calculated updates are checked before order balance,
position and fill mutations. BSON integer bounds apply to order and position
quantities. Missing-account creation is separate from executing a trade. Existing legacy
accounts are read without migration writes. A valid CNY trade can normalize scalar
balances in its validated account update; unknown foreign balances remain null. Invalid historic financial amounts are not guessed or
silently repaired; preserve and investigate the underlying private records.

Both research one-click dialogs reject missing prices and unavailable currency
cash before confirmation. The single-analysis page no longer falls back to a
fabricated price of 10. Legacy scalar CNY cash cannot fund a foreign-currency
order. The backend still obtains its own market-order quote when filling; the
editable dialog estimate is not a limit order or an execution guarantee.

## Verification

- `python -m pytest -q tests/test_paper_valuation.py`: real FastAPI handlers with
  isolated collections and fake quotes; missing and recovering estimates, currency
  isolation, invalid provider fields, corrupt legacy values, actual fee config
  validation, derived overflow, unchanged valid simulated fills and no writes on
  rejection.
- `cd frontend && yarn test:ui-contracts`: actual amount/currency helpers, actual
  view interpolation expressions, and both one-click handlers with fake APIs.
- `cd frontend && yarn build`: strict type checking and production bundle.

No provider, broker, live account, notification or production data is used by these
regressions. These checks are not a browser/login or deployed-data acceptance run.

## Separate work still required

The simulator still uses multiple independent Mongo writes for an order; it does
not yet provide transaction atomicity, concurrent cash/position reservation or
idempotent crash recovery. Quote timestamps and executable/session provenance are
not verified. Explicit market/code normalization and market-specific lot sizing
also need separate review. Numeric validation does not resolve these limitations
or certify the simulator for capital allocation. Existing realized PnL semantics
remain unchanged; this repair does not create a complete net-cost return model.
