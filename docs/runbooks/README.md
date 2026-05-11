# Runbooks Index

> **Status**: v1 (2026-05-09 / Phase 6 / ADR-0010 §6-5)
> **Scope**: 障害発生時に「考えずに手順通り進める」ためのチェックリスト集。
> **使い方**: [INCIDENT_RESPONSE.md](../operations/INCIDENT_RESPONSE.md) §1 の障害分類 → 該当 Runbook へ。

---

## Runbook 一覧

| # | タイトル | 障害分類 | Severity | リンク |
| --- | --- | --- | --- | --- |
| RB-001 | Kill Switch が発火した | F2 | High | [link](RB-001-kill-switch-fired.md) |
| RB-002 | kabu Station API が応答しない | F3 | High | [link](RB-002-kabu-api-down.md) |
| RB-003 | AWS SSM 認証失敗 | F3 | Critical | [link](RB-003-aws-ssm-failure.md) |
| RB-004 | Discord 通知が届かない | F3 | Med | [link](RB-004-discord-down.md) |
| RB-005 | Daily_Asset_Snapshot 書込失敗 | F1/F2 | High | [link](RB-005-snapshot-write-failure.md) |
| RB-006 | Profit Sweep が時刻通りに完了しない | F2 | Critical | [link](RB-006-profit-sweep-incomplete.md) |
| RB-007 | Market_Ticks の delta_volume 異常検知 | F2/F3 | High | [link](RB-007-market-ticks-anomaly.md) |
| RB-008 | data.db の破損疑い | F1 | Critical | [link](RB-008-data-db-corruption.md) |
| RB-009 | VIX gear が誤発火している | F2 | Med | [link](RB-009-vix-gear-misfire.md) |
| RB-010 | WebSocket 切断ループ (再接続失敗連発) | F3 | High | [link](RB-010-websocket-loop.md) |

## 共通テンプレ (各 Runbook の構造)

各ファイルは以下の構造で記述:
1. **症状** — Discord 通知文 / ログのキーワード
2. **影響範囲** — どの bucket / 機能が止まるか
3. **検知方法** — System_Logs SQL / 観測対象
4. **確認手順** — 仮説検証のためのチェックリスト
5. **解消手順** — 手順書通り進めれば修復できる
6. **解除確認** (kill switch 系のみ) — INCIDENT_RESPONSE.md §4 に従う
7. **エスカレーション** — 自力で直らない場合の連絡先 / 次の手

## 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成 (Phase 6 / ADR-0010 §6-5 / Runbook 10 件のインデックス) |