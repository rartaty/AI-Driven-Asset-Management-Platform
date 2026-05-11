# 資産管理・自動運用システム 詳細技術仕様書 (Technical Specifications)

## 1. システム・アーキテクチャ全体構成
本システムは、高い堅牢性と再現性を保つため、コンテナによるハイブリッド構成とテスタビリティを意識した設計とします。

- **ホストOS環境**: Windows 11 / 10
  - KabuステーションGUIアプリを常駐（ポート18080を開放）。
- **コンテナエンジン**: Podman (またはDocker Desktop)
- **インフラ管理**: `docker-compose.yml`

### 1.1 構成コンポーネント
1. **Frontend (Next.js コンテナ)**
   - ユーザー操作画面と描画。ポート 3000。
2. **Backend (Python 3.11 / FastAPI コンテナ)**
   - REST API サーバーおよび、スケジュールワーカー（`APScheduler` または `schedule`）の同居稼働。
3. **Database (SQLite ファイル `backend/src/data.db`)**
   - アセット状態、AI履歴、意思決定ログの永続化。ファイルベースのためコンテナ・ポート不要。SQLAlchemy 2.0 の Mapped Style で PostgreSQL 互換 SQL を生成 (将来 PostgreSQL 移行時は DATABASE_URL 1行差替えのみ)。本格的な JSONB 必要時は PostgreSQL 移行を検討。

---

## 2. ソフトウェア・技術スタック詳細

### 2.1 バックエンド (Python 3.11)
バグや想定外の挙動を防ぐため、厳格な型ヒント (`typing`モジュール) を義務付けるコーディング規約を採用。

- **Webフレームワーク**: FastAPI (Pydanticによるデータバリデーションモデル定義を含む)
- **データベース・ORM**: SQLAlchemy 2.0 (生SQLを書かずインジェクションを防ぐ)、psycopg2。
- **データ分析・演算**: `pandas` (時系列データ・移動平均), `numpy`, `scipy` (短期ロジックZスコア計算等)。
- **外部通信モジュール**: `requests`, `websocket-client` (KabuステーションREST/Websocket通信等)、`BeautifulSoup4` (EDINET等からXBRLパーシング用)。
- **認証連携モジュール**: `boto3` (AWS SSMからKMS暗号化APIキー動的取得 ※ローカルの`.aws/credentials`利用)。
- **ログ管理**: `python-json-logger` (Elasticsearch連携等を可能にするための構造化ロギング実装)。

### 2.2 テスト・品質保証基盤 (Testing Strategy)
システムは段階的開発における「堅牢な振る舞い保証」を目的としてテスト基盤を組み込む。

- **Unit Testing**: `pytest` を用いたビジネスロジック（FCF算出、判断ロジック、ポートフォリオリバランス計算）のテスト。
- **Mocking**: `pytest-mock` を活用し、実際のAPIへリクエストを飛ばさずに「想定されるRESTレスポンスJSON」を用いた単体テストを実施。
- **DI (Dependency Injection)**: FastAPIの`Depends`機能を使い、テスト環境下では自動的に「APIクライアント＝Mockクライアント」へ差し替わるように設計する。

---

## 3. ディレクトリ構成 (AI-Friendly Architecture)
保守性とSRP（単一責任の原則）を厳守した構成。

```text
/trading-system
├── docker-compose.yml       # コンテナ群の環境定義
├── .env                     # 環境変数 (AWSパス、Mock動作フラグ: USE_MOCK=True)
├── /data                    # 永続化マウント (SQLite Cache、DB群、Logs)
│   └── /reports             # 自動生成された運用レポート・アルバムの実体ファイル保管庫
├── /frontend                # Next.js のフロントエンドソース
└── /backend                 # Python 3.11 バックエンド
    ├── Dockerfile           # Pythonコンテナ定義
    ├── requirements.txt     # 依存ライブラリ一覧 (pytest等を含む)
    ├── pyproject.toml       # pytest設定、linter/formatter等定義
    └── /src
        ├── /api             # 各種外部APIリクエスト (Mock切り替え機構付き)
        │   ├── kabucom.py
        │   ├── edinet.py
        │   └── yfinance.py
        ├── /core            # 共通基盤
        │   ├── aws_ssm.py   # SSMパラメータ復号処理
        │   ├── discord.py   # Webhook送信ロジック [CRITICAL_LOGGER]
        │   └── logger.py    # JSON構造化ロガー設定
        ├── /models          # SQLAlchemyテーブル定義 (User_Settings等)
        ├── /routers         # FastAPIのエンドポイント群 (/api/v1/... )
        ├── /strategy        # 自動取引ロジック推論モデル
        │   ├── v6_long.py   # FCF絶対重視・マクロ調整型長期モデル
        │   └── vwap_short.py# 当日活況モメンタム抽出、VWAP Zスコアモデル
        └── /runner
            ├── main_trade.py# メインジョブスケジューラ (本稼働用)
            └── backtest.py  # シミュレーション用スクリプト
```

---

## 4. ロジック層の実装詳細とテストポイント

> [!IMPORTANT]
> **[PROPRIETARY LOGIC REDACTED]**
> 
> セキュリティおよび戦略上の優位性を保護するため、具体的なアルゴリズム、選定基準、パラメータ設定などの詳細は公開版リポジトリからは削除（非公開）としています。

## 5. アラート・監視仕様 (Discord)
例外発生時は、タグ `[COMPONENT]` を先頭に付与してDiscordに送出する。（エラーハンドラーの責務）
- 例: `🔴 [CRITICAL] [API:Kabucom] kabuステーションAPI 連続3回タイムアウト`
- フェイルセーフ検知: 「資産の3%ドローダウン」を検知した場合、システム変数 `is_kill_switch_active = True` に設定し、コードレベルで完全な「Buy（買い）の遮断」を行う。これもDiscordに緊急通知される。
- **定期通知・トレード結果通知仕様**:
  - **成功時**: 当日の実現利益、投資信託への振替額（Sweep額）、および更新後の資産比率（投信・長期・短期）を通知。
  - **損失時**: 損失額に加え、AI（ローカルLLM）による損失理由の定性分析結果（ドリルダウン）、キルスイッチの稼働状況を併せて通知。

---

## 6. データベース構造 (Database Schema)
UIの可視化要件（多角的分析・イントラデイ詳細）を満たすため、統計情報を保存する専用テーブルを定義する。

**テーブル数: 13** (Phase 7 で `Market_Ticks` 追加 / ADR-0009)。完全な定義は [backend/src/models/schema.py](../backend/src/models/schema.py) を参照。本書は主要テーブルのみ詳細記載。

### 6.1 `Daily_Asset_Snapshot` (日次資産スナップショット)
毎日の資産状態を記録し、月次・年次のポートフォリオ成長曲線の描画に使用する。
銀行口座と証券口座は独立プールとして別カラム管理 (REQUIREMENTS_DEFINITION.md §2.2)。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `date` | Date | 記録日 (PK) |
| `bank_balance` | BigInt | **銀行口座残高** (生活防衛費含む) ※読取専用、API: OpenCanvas |
| `buying_power` | BigInt | **証券口座 現金** (買付余力 = 未投入資金)。下限なし (投資機会がなければ100%現金保持も許容) |
| `trust_value` | BigInt | 投資信託 (Passive Core) 評価額 |
| `long_solid_value` | BigInt | 長期コア 評価額 |
| `long_growth_value` | BigInt | 長期サテライト 評価額 |
| `short_term_capital` | BigInt | 短期枠 **元本** (利益再投資分を含む) |
| `short_term_market_value` | BigInt | 短期枠 **評価額** (元本との差が含み損益) |
| `cumulative_sweep_to_long_solid` | BigInt | 短期枠から **長期コア** への累計 Profit Sweep 額 (旧 `cumulative_transfer` から振替先変更に伴い改名) |

### 6.2 `Trade_Tick_Log` (短期トレード用ログ)
イントラデイ（1日の中）での詳細な損益推移とAIの判断を可視化するために記録する。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `timestamp` | DateTime | 記録日時 (1分〜5分間隔) |
| `unrealized_pnl` | Int | 未実現損益（含み損益） |
| `realized_pnl` | Int | 実現損益 |
| `regime_type` | String | AIによるレジーム判定結果（トレンド・デイ等） |

### 6.3 `Report_Archive` (運用レポート・アルバム)
日次・月次・年次の自動生成レポートのメタデータと、AIによる振り返りを格納し、アルバムUIの基盤とする。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `report_id` | UUID | レポート一意識別子 (PK) |
| `report_type` | Enum | 日次(Daily) / 月次(Monthly) / 年次(Annual) |
| `target_date` | Date | 対象期間・日付 |
| `file_path` | String | レポート実体ファイル（`/data/reports/...`）のパス |
| `ai_summary` | Text | AIによる振り返り（If-Then分析、改善点）の要約テキスト |

### 6.4 `Market_Ticks` (歩み値再構築データ / Phase 7 / ADR-0008/0009)
kabu Push API (REAL) または yfinance (PAPER) から再構築した 1 秒 bucket の連続価格・出来高データ。
PK: `(ticker_symbol, timestamp)` の複合キー。配置は **共有テーブル** (bucket 隔離対象外 / Asset_Master と同等扱い)。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `ticker_symbol` | String | 銘柄コード (PK) — Asset_Master FK |
| `timestamp` | DateTime | bucket 終端時刻 (PK) |
| `last_price` | BigInt | bucket 終端の last_price (円単位整数) |
| `cumulative_volume` | BigInt | bucket 終端の当日累積出来高 |
| `delta_volume` | BigInt | 当 bucket 内の出来高 (前 bucket との差分) |
| `bid_price` / `ask_price` | BigInt nullable | bucket 終端の板情報 (push 由来) |
| `side_inference` | Enum (TickSide) | LR-EMO 結果 (`BUY_AGGR` / `SELL_AGGR` / `MID`) |
| `is_synthetic` | Bool | gap 補完 / yfinance 由来 = True |
| `push_count` | Int | bucket 内 push 数 (流動性指標) |

### 6.5 `User_Settings` (ユーザー設定 + Kill Switch / Phase 6 / ADR-0010)
シングルトン (id=1)。bucket 別キルスイッチ列を Phase 6 で追加 (ADR-0007 OQ-4 / ADR-0010 §6-1)。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `id` | Int | PK (常に 1 シングルトン) |
| `aws_kms_key_id` | String nullable | SSM/KMS 参照キー |
| `discord_webhook_url` | String nullable | 暗号化対象 (実値は SSM 経由) |
| `trading_mode` | String | 'Live' or 'Mock' (PAPER) |
| `max_drawdown_limit` | Float | ドローダウン閾値 (デフォルト -0.03) |
| `is_kill_switch_active` | Bool | **全体停止** (全 bucket の新規エントリーをブロック) |
| `is_kill_switch_active_passive` | Bool | 投資信託のみ停止 (Phase 6 追加) |
| `is_kill_switch_active_long_solid` | Bool | 長期コアのみ停止 (Phase 6 追加) |
| `is_kill_switch_active_long_growth` | Bool | 長期サテライトのみ停止 (Phase 6 追加) |
| `is_kill_switch_active_short` | Bool | 短期トレードのみ停止 (Phase 6 追加) |

非対称オーバーライド: 全体 OR bucket 別の OR で「該当 bucket 新規エントリーをブロック」。決済 (sell) はブロック対象外。
CLAUDE.md 絶対禁止 #3 (キルスイッチ無断解除禁止) は **全フラグに適用**。

### 6.6 残テーブル概要 (詳細は schema.py 参照)
- `Asset_Master`: 銘柄マスタ (Survivor Bias 排除、`is_active=False` で論理削除)
- `Financial_Reports`: XBRL 財務データ (FCF/EBITDA/総資産/発行済株式数)
- `Daily_Price_History`: 日次株価 (Adjusted Price 補正済み)
- `Cash_Pool_Status`: 銀行+証券キャッシュ統合状態
- `Target_Portfolio`: 動的トップレベル比率 (cash/trust/stocks)
- `Market_Context`: 市況スナップショット (VIX/USD/JPY/JP10Y)
- `Trade_Logs`: 売買履歴 + AI 判断理由
- `System_Logs`: システムイベント・エラーログ
