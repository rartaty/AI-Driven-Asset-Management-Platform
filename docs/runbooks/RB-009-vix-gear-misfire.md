# RB-009: VIX gear が誤発火している

> **Severity**: Med | **Category**: F2 (ロジックバグ) or F3 (データソース異常)

## 1. 症状
- Discord: `VIX gear fired (DEFEND, VIX=...)` または `(ATTACK, VIX=...)` が想定外のタイミングで発火
- Target_Portfolio に予期せぬレコードが追加される
- 週次リバランスが意図しない比率で実行される

## 2. 影響範囲
- ポートフォリオ目標比率の異常調整
- 不要なリバランス取引の発生 (取引コスト増)

## 3. 検知方法
```sql
-- 直近の Target_Portfolio 書込履歴
SELECT effective_date, cash_target_pct, trust_target_pct, stocks_target_pct, trigger, notes
  FROM target_portfolio ORDER BY effective_date DESC LIMIT 10;

-- VIX 値の推移
SELECT timestamp, vix FROM market_context ORDER BY timestamp DESC LIMIT 20;
```

## 4. 確認手順
- [ ] **VIX 値そのものが信頼できるか** — yfinance / kabu のどちらから取得したか
  ```python
  from services.market_context import fetch_vix
  print(fetch_vix())
  ```
- [ ] **業界ニュースで VIX 急変イベントがあったか** (株式急落 / FOMC 等の正常な急変)
- [ ] **閾値が誤って書き換わっていないか** ([market_context.py](../../backend/src/services/market_context.py) `VIX_DEFEND_THRESHOLD=20.0` / `VIX_ATTACK_THRESHOLD=35.0`)
- [ ] **発火頻度** — 1 日複数回発火は誤動作疑い (理論上 1 日 1 回程度)

## 5. 解消手順
1. **VIX 値が誤り (yfinance バグ等)**:
   - `fetch_vix()` の戻り値を一時的にハードコードしてテスト
   - kabu API が VIX を返すか手動確認
2. **正常な急変だった場合**:
   - Target_Portfolio の更新を尊重 (それが「動的トップレベル比率」の設計目的)
   - 短期 bucket キルスイッチ ON で追加取引を止めて様子見
3. **誤発火が連続する場合**:
   - market_context bucket キルスイッチ的な抑制 (現状未実装、Phase X 候補)
   - 緊急的に scheduler の market_context update_market_context を一時停止

## 6. エスカレーション
- yfinance データ品質問題 → fetch_vix の primary を kabu に切替
- threshold 自体の見直し → ADR-0002 (動的比率) 改訂議論

## 関連
- [backend/src/services/market_context.py](../../backend/src/services/market_context.py)
- [backend/src/services/target_portfolio.py](../../backend/src/services/target_portfolio.py)
- ADR-0002 (動的ポートフォリオ + 投資判断ファースト原則)
- ADR-0006 P9 (Target_Portfolio 連携)