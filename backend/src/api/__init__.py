"""
api/ — 外部システムアダプタ層 (External API Clients)

- kabucom: 三菱UFJ eスマート証券 (旧auカブコム証券) — kabuステーション API
- opencanvas: 銀行 API (読取専用 — 出金機能は意図的に使用しない)
- (将来) edinet: XBRL 財務データ
- (将来) yfinance: マクロ + 株価補完 (FRED含む)

specs/ — 公式 API 仕様書 (YAML)

参照: docs/infrastructure/DATA_FOUNDATION.md §2
"""
