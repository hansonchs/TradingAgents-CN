# Research application repair validation

The frontend now passes the existing strict TypeScript check and production build. Corrections include response-envelope versus raw Blob downloads, report types, partial user preferences, scheduler records, and currency-specific local simulation balances. Compiler checks were not disabled. Reports are explicitly research; the local simulation account is not a broker account, and the usage dashboard remains labelled sample data.

Security boundaries repaired in this candidate:

- Report listing/read/module/download/delete and task status/result Mongo fallbacks require explicit owner identity. Shared memory reads and mutations are checked before use. Global task listings and legacy ownerless reports require administrator privileges.
- Notification WebSockets resolve the same active user as HTTP authentication. Private task progress is sent to the stored owner only; unknown-owner events are omitted. Task-specific sockets check owner before accepting. Connection statistics require an administrator. TaskCenter uses its existing authenticated polling rather than an invalid unauthenticated socket URL.
- All current `v-html` sinks sanitize after Markdown parsing. Provider-controlled news anchors accept only HTTP(S). Known credential-value log statements in data-source updates and authentication were removed.
- Cached news respects the publication window and announcement filter. Sync failures returned as stats, exceptions, and ambiguous empty provider fetches remain unavailable; they are not evidence of no news.
- Research MongoDB storage initialization requires an actual MongoDB URI. Missing/invalid/disabled configuration never attempts a connection, and initialization exceptions do not log the URI or credential text.
- Data-source manager checks use the actual shared consistency checker; the deleted unified_dataframe API is not recreated just for tests.

180 selected offline regressions passed (including 116 local simulator valuation/state checks) (fake Mongo, two users, fake providers; no broker, model or messaging requests):

```bash
python -m pytest -q tests/test_paper_valuation.py tests/test_research_storage_config.py tests/test_task_ownership.py tests/test_report_ownership.py tests/test_websocket_ownership.py tests/test_risk_manager_contract.py tests/unit/test_stocks_kline_news_api.py tests/system/test_llm_provider_sanitization.py tests/unit/dataflows/test_data_source_fallback.py tests/test_system_config_summary_sse_queue.py tests/test_sse_and_worker_config.py
cd frontend
npm run test:ui-contracts
npm run build
```

The simulator retains missing quotes and legacy invalid balances/costs as unknown, rejects invalid numbers before order mutations, and no longer migrates an existing account during GET. Both one-click dialogs reject missing quotes/currency cash; the single-analysis fake-price fallback is removed. [Contract, verification and remaining limits](PAPER_VALUATION.md).

These are selected contract and regression checks, not the entire backend test tree or a complete multi-tenant security audit. Real browser/login, Mongo/Redis restart, live data freshness and actual provider research still need deployment acceptance. Existing build warnings about legacy Sass APIs and large bundles remain performance/maintenance follow-up work.
