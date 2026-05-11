# ADR-0005: Phase 4 スコープ = 案B Standard (core/ 3件 + Kill Switch + SSM 切替)

- **Status**: Completed (2026-05-09 検証完了)
- **Date**: 2026-05-03 (採択) / 2026-05-09 (DoD 検証)
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (DB 選定 / SQLite), ADR-0002 (動的比率 / 投資判断ファースト), ADR-0003 (SSM Standard tier), ADR-0004 (ハーネス9層構成)

## DoD 検証結果 (2026-05-09)

| # | DoD 項目 | 結果 |
| --- | --- | --- |
| 1 | core/ 3 ファイル TODO マーカーゼロ | ✅ Grep 検出ゼロ |
| 2 | pytest 全件 PASS | ✅ 87/87 PASS (3.15s) |
| 3 | kabucom/opencanvas SSM 経由認証 (要件 E.6.1) | ✅ `get_secret()` 経由実装済 |
| 4 | AWS IAM ポリシー + Budgets | ✅ ユーザー確認済 (SEC-0 Closed 2026-05-03) |
| 5 | secrets-scanner subagent 平文埋め込み検出 | ✅ CLEAN (検出 0 件) |
| 6 | discord-notify-validator `[COMPONENT]` 整合性 | ✅ PASS (4/4 セクション) |
| 7 | Activity Log + ADR Status 更新 | ✅ 本ファイル更新 + audit doc 記録 |

**軽微な指摘** (DoD は満たすが Phase 5 着手前に整理推奨):
- `kabucom.py:7` docstring が古い (「Phase 4 以降に移行予定」既に移行済)
- `kabucom.py:23, 48, 72` `print()` → `logger.error()` + `notify_system()` 推奨
- `kabucom.py:37`, `opencanvas.py:40` エラーメッセージが ".env" 文脈のみ

**関連**: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)

---

## Context (背景)

Phase 1 (要件・設計ドキュメント整備), Phase 2 (DB schema 12テーブル再設計), Phase 3 (api/ + core/ ディレクトリ再編) を完了し、Phase 4 (`core/` 配下の本実装) への着手段階。

当初 Phase 4 は **「`core/aws_ssm.py` + `discord.py` + `logger.py` のスケルトン3ファイルを本実装」** という narrow なスコープで定義されていた。

しかし、`requirements-gap-detector` subagent による R-2 監査 ([docs/audit/2026-05-03-pre-phase4.md](../audit/2026-05-03-pre-phase4.md)) で **Critical High 8件** が発見された:
- Kill Switch ロジック完全未実装 (DB カラムだけある)
- 生活防衛費保護未実装
- VIXギア発火判定未実装
- kabucom/opencanvas が `os.getenv` 直読み (要件 E.6.1 違反)
- core/ 3 件全て stub
- FastAPI ルーター全件で認証 dependency なし

これら 8 件のうち core/ 3 件本実装で塞がるのは 3 件のみ。残り 5 件 (Kill Switch / 生活防衛費 / VIX / kabucom 認証 / Web 認証) は別作業が必要。

判断を保留すると、Phase 4 完了時点で Critical な要件違反が 5 件残存し、Phase 5 への移行判断が曖昧化する。

## Decision (決定)

**Phase 4 のスコープを 案B (Standard) で確定**: `core/` 3 件本実装 + Kill Switch ロジック配線 + kabucom/opencanvas を SSM 経由に切替。期間目安 3 日程度。

### スコープ詳細

**含む (Phase 4)**:
1. `core/aws_ssm.py` 本実装 (boto3 + KMS 復号 + 24h TTL キャッシュ + fail-fast)
2. `core/discord.py` 本実装 (HTTP 送信 + `[COMPONENT]` プレフィクス + 非ブロッキング送信 + SSM 経由 Webhook URL 取得)
3. `core/logger.py` 本実装 (python-json-logger 構造化ロギング + System_Logs DB 書込)
4. `Kill Switch` ロジック配線 (drawdown 計算 / `is_kill_switch_active` 書込 / 全買付パスでの事前チェック / 解除手順)
5. `api/kabucom.py` / `api/opencanvas.py` を SSM 経由認証に切替 (要件 E.6.1 違反解消)
6. `scripts/load-secrets.ps1` 作成 (SEC-1)
7. `scripts/register_secrets.py` 作成 (SEC-4)
8. SSM パス命名規約確定 (SEC-3 — 推奨案: `/projectbig/{component}/{key-name}` ケバブ形式)
9. AWS IAM ポリシー設定 (Console 作業)
10. AWS Budgets 設定 (月額 $0.50 アラート / $1 でブロック)
11. APScheduler `check_secret_rotation` ジョブ追加 (SEC-9 期限通知)
12. 旧 `passive_core.py` の `sweep_to_trust` キー名を `sweep_to_long_solid` に rename (P8 解消・schema と整合)

**含まない (Phase 5 以降に持ち越し)**:
- 生活防衛費保護 (M2)
- VIXギア発火判定 (M3)
- Web 認証 (FastAPI ルーター auth dependency / E.5.1) (M4)
- 旧 50/30/20 ロジックの動的比率対応 refactor (P9)
- 朝08:30 トークン取得・年次/週次 rebalance ジョブ追加 (M5, M6)
- EDINET XBRL パイプライン (M7)
- WebSocket クライアント (M10)
- Frontend 残機能 (多角的時間軸/イントラデイ/銘柄照会/AI タイムライン等) (M11)
- runner/ ディレクトリ (M9)
- portfolio_sync.py の旧スキーマ整合修正 (P11)
- TEST_MODE / TRADE_MODE 二重環境変数の整理

**含めない理由 (案B 選定根拠)**:
- 案A (Narrow / core/ 3 のみ) では Kill Switch・SSM 切替が後回しになり、Critical 要件違反が長期化
- 案C (Full / Critical 8件全塞ぎ) は規模 1 週間 + 範囲が広すぎてレビューが分散
- 案B は「core/ 完成 + 最重要安全装置 + 要件 E.6.1 解消」を 1 フェーズに収める最適解

### Why
- Critical 違反のうち「コードレベルで塞げる代表的 4件」を一度に解消することで、Phase 5 以降の判断を「Operations 強化 (生活防衛費/VIX/認証)」と「機能拡充 (UI/EDINET 等)」に整理できる
- core/ 3 件の本実装が他層改修のブロッカー (kabucom が SSM 経由でないと先に進めない) のため、SSM 切替を同時実施するのが合理的
- Kill Switch は最大級のリスク領域であり、core/ logger・discord と統合する形でないと配線時に矛盾が生じる

## Consequences (結果)

### ✅ Positive
- core/ スケルトンが解消し、他層 (strategy/ services/ routers/) の作業ブロッカーが消える
- 要件 E.6.1 (シークレット管理) の違反状態を解消
- Kill Switch 配線で最大リスク (損失拡大保護) のコードレベル実装が完成
- ハーネス Topic 9 (シークレット管理) の SEC-1〜SEC-6 が同時に進行
- ADR-0003 (SSM 採用) の決定が初めて実装で活きる

### ⚠️ Negative / Trade-off
- **期間 3 日想定**: 1 日コミットでは終わらないため、複数セッションでの分割実装になる
- **AWS アカウント設定の初期コスト**: AWS Console 作業 1〜2 時間 (IAM / Budgets / 初期パラメータ登録)
- **テスト負荷増**: SSM モック / 14:50 強制決済 + Kill Switch シナリオの統合テスト追加要
- **生活防衛費 / VIX / Web 認証 が残課題**: Phase 5 で別途対応必要

### 🔄 Required Follow-up
- ✅ 着手前: ADR-0001/0002/0003/0004 (前提決定) 確定
- ✅ 着手前: 監査スナップショット保存 ([docs/audit/2026-05-03-pre-phase4.md](../audit/2026-05-03-pre-phase4.md))
- 📋 着手中: Phase 4 サブタスクの順序決定 (推奨: aws_ssm.py → load-secrets.ps1 → IAM/Budgets → register_secrets.py → discord.py → logger.py → Kill Switch → kabucom/opencanvas SSM 切替)
- 📋 完了後: 統合テスト (PAPER モードで end-to-end 動作確認)
- 📋 完了後: ADR-0006 (Phase 5 スコープ) 起草

## Alternatives Considered (検討した代替案)

### 案 A: Narrow (core/ 3 ファイルのみ)
- **概要**: 当初予定の最小スコープ・1 日完了見込み
- **却下理由**: Critical 8件中 3件しか塞がらず、要件 E.6.1 違反 (kabucom/opencanvas SSM 未経由) と Kill Switch 未配線が長期化

### 案 C: Full (Critical 8件全塞ぎ)
- **概要**: Web 認証・生活防衛費・VIX も含めて 1 フェーズで完了
- **却下理由**: 規模 1 週間 + 6 領域に分散しレビュー集中力が低下・各領域の議論が混在しやすい

### 案 D: Sequential (細分化)
- **概要**: Phase 4a (core/), 4b (Kill Switch), 4c (SSM), 4d (kabucom 切替) と各サブフェーズに分割
- **却下理由**: 採用可だが、細分化のオーバーヘッド (各サブフェーズ毎に taskboard 更新・ADR 起草) が大きい・案B 一括の方が実装効率が高い

## Related (関連)

- 監査スナップショット: [docs/audit/2026-05-03-pre-phase4.md](../audit/2026-05-03-pre-phase4.md)
- ハーネスタスクボード: [.claude/harness-taskboard.md](../../.claude/harness-taskboard.md) Topic 9 / SEC-1〜SEC-6
- Activity Log: [taskboard.md](../../taskboard.md) 2026-05-03 行
- 関連 ADR: ADR-0001 (DB), ADR-0002 (投資判断ファースト), ADR-0003 (SSM), ADR-0004 (ハーネス)
- 関連 Memory: `~/.claude/projects/.../memory/project_current_focus.md` (Phase 4 の山として記載済)

## Notes

### Phase 4 推奨実装順序 (依存関係順)
1. `core/aws_ssm.py` 本実装 (他全ての SSM 経由処理の前提)
2. `scripts/register_secrets.py` 作成 (登録ヘルパー先行)
3. AWS Console 作業: IAM ポリシー + Budgets + 初期パラメータ 13 件登録
4. `scripts/load-secrets.ps1` 作成 (起動ラッパー)
5. `core/discord.py` 本実装 (Webhook URL を SSM 経由取得テスト)
6. `core/logger.py` 本実装 (System_Logs テーブルへの書込)
7. `Kill Switch` ロジック配線 (drawdown 計算ジョブ + 全買付パスでの事前チェック)
8. `api/kabucom.py` / `api/opencanvas.py` を SSM 経由認証に切替
9. 既存 `passive_core.py` の `sweep_to_trust` rename
10. 統合テスト (PAPER モード end-to-end)

### Phase 4 完了の Definition of Done
- core/ 3 ファイルの全関数が本実装済み (TODO コメント残存ゼロ)
- pytest 全件 Pass + Kill Switch シナリオを含む新規テスト追加
- kabucom/opencanvas が SSM 経由認証で実行可能 (env 直読みコード残存ゼロ)
- AWS IAM ポリシー + Budgets 設定済み・月額 $0 で運用継続確認
- secrets-scanner subagent 実行で平文埋め込み検出ゼロ
- discord-notify-validator subagent 実行で `[COMPONENT]` プレフィクス整合性 OK
- Activity Log + 本 ADR の Status を完了内容で更新
