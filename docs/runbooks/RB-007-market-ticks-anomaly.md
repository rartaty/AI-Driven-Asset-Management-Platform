# RB-007: Market_Ticks の delta_volume 異常検知

> **Severity**: High | **Category**: F2 (ロジックバグ) or F3 (データソース異常)

## 1. 症状
- Discord: `[MarketData] ABNORMAL: cumulative_volume regression for <ticker>: prev=... curr=...`
- Layer 1 deque で `delta_volume < 0` の tick が記録されない (TickReconstructor が emit ブロック)
- 短期戦略の VWAP 計算が想定外の値

## 2. 影響範囲
- 該当銘柄の短期戦略シグナル発火が止まる (data 不在)
- 異常が複数銘柄に拡大 → kabu Push 全体の信頼性低下

## 3. 検知方法
```sql
-- 直近の異常ログ
SELECT timestamp, event, payload FROM system_logs
 WHERE component='[MarketData]' AND event LIKE '%ABNORMAL%'
 ORDER BY timestamp DESC LIMIT 20;

-- delta_volume が 0 続きの銘柄 (Push 不通の疑い)
SELECT ticker_symbol, COUNT(*) as zero_count FROM market_ticks
 WHERE delta_volume = 0 AND timestamp > datetime('now', '-1 hour')
 GROUP BY ticker_symbol HAVING zero_count > 100;
```

## 4. 確認手順
- [ ] **場前リセット時刻 (09:00-09:05) ではないか** — 特例処理が働く時間帯
- [ ] **kabu Push WebSocket の接続状態**:
  ```python
  from services.kabu_push_client import get_client
  print(get_client().is_running())
  ```
- [ ] **該当銘柄のストップ高/安張り付き** — `delta_volume=0` 持続なら正常 (取引停止)
- [ ] **TickReconstructor の前 bucket 値**:
  ```python
  from services.market_data import get_reconstructor
  print(get_reconstructor()._last_completed.get("<ticker>"))
  ```

## 5. 解消手順
1. **データソース異常 (F3)**: kabu Station 側の不整合 → アプリ再起動 + WebSocket 再接続
2. **ロジックバグ (F2)**: TickReconstructor の場前リセット判定が誤っている可能性 → unit test 追加
3. **緊急止血**: 短期 bucket キルスイッチ ON (ADR-0010 §6-1 の `activate_bucket(session, "Short", ...)`)
4. **REST 補完で gap を埋める** (Phase 7 設計の `is_synthetic=True` フラグ運用)

## 6. エスカレーション
- 全銘柄で同時発生 → kabu Station アプリ全体の不調 → [RB-002](RB-002-kabu-api-down.md)
- TickReconstructor の場前リセット時刻調整が必要 → 仕様再確認 (要件 §B.2)

## 関連
- [backend/src/services/market_data.py](../../backend/src/services/market_data.py) `TickReconstructor._finalize_bucket`
- [backend/src/services/kabu_push_client.py](../../backend/src/services/kabu_push_client.py)
- ADR-0008 (Tick Data 設計決定) / ADR-0009 (Phase 7 スコープ)