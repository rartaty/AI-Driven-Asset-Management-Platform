# RB-006: Profit Sweep が時刻通りに完了しない

> **Severity**: Critical | **Category**: F2 (ロジックバグ) — オーバーナイトリスク発生

## 1. 症状
- 14:50 を過ぎても `[ProfitSweep] All paper positions closed` 通知が来ない
- 翌朝 paper_trader.positions に未決済ポジションが残存
- (REAL モードでは) 翌営業日の寄付で意図しないキャリーオーバー

## 2. 影響範囲
- **要件 §6 オーバーナイトリスク排除違反**
- 短期戦略のリスク管理が物理的に崩壊
- Profit Sweep 計算 (短期 50% → 長期コア振替) が不発 → bucket 比率歪曲

## 3. 検知方法
```sql
-- 当日 14:50-15:00 の job_profit_sweep 関連ログ
SELECT timestamp, level, component, event FROM system_logs
 WHERE component LIKE '[ProfitSweep]%' OR component='[Scheduler]'
   AND timestamp BETWEEN datetime('now', 'start of day', '+14 hours', '+50 minutes')
                     AND datetime('now', 'start of day', '+15 hours')
 ORDER BY timestamp;
```

## 4. 確認手順
- [ ] **APScheduler が動作しているか** — `system_scheduler.scheduler.running == True`
- [ ] **取引時間中 (mon-fri) か** — 祝日や土日は登録通り skip
- [ ] **kabu API / paper_trader が応答するか** ([RB-002](RB-002-kabu-api-down.md))
- [ ] **WebSocket 切断ループに巻き込まれていないか** ([RB-010](RB-010-websocket-loop.md))
- [ ] **paper_trader.positions の状態**:
  ```python
  from services.paper_trader import paper_trader_engine
  print(paper_trader_engine.positions)
  ```

## 5. 解消手順
1. **手動 Profit Sweep 実行** (緊急止血):
   ```python
   from services.scheduler import system_scheduler
   system_scheduler.job_profit_sweep()
   ```
   または `POST /api/v1/system/test/profit_sweep`
2. **未決済ポジションを個別決済**:
   ```python
   from services.paper_trader import paper_trader_engine
   for p in list(paper_trader_engine.positions):
       paper_trader_engine.execute_virtual_order(p["symbol"], p["name"], p["shares"], is_buy=False)
   ```
3. **scheduler が動いていない** → アプリ再起動 (Podman: `restart: always` で自動復旧の確認)

## 6. エスカレーション
- スケジューラ自体が壊れている → CI 水準のリグレッションテスト追加
- REAL モードで実発注パスが未完了の状態でキャリーオーバー → 翌朝寄付前に手動決済 (PAPER 経路) + 損失計上

## 関連
- [backend/src/services/scheduler.py](../../backend/src/services/scheduler.py) `job_profit_sweep`
- [backend/src/services/paper_trader.py](../../backend/src/services/paper_trader.py)
- 要件 §6 オーバーナイトリスク排除