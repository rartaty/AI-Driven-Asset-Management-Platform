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
3. **Database (PostgreSQL 15 コンテナ)**
   - アセット状態、AI履歴、意思決定ログの永続化。ポート 5432。JSONB型を活用。

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

### 4.1 長期運用（V6コア・サテライト）ロジック
- **データ取得パイプライン (`src/api/edinet.py`)**: 営業CF、投資CF、総資産、EBITDAを抽出。
- **モジュールのテスト責任帯**:
  - `FCF > 0` かつ `資産増加率 < EBITDA増加率` なら`True`を返すロジックが、Pandas DataFrame上で正確に機能するかをpytestで担保。

### 4.2 短期運用（VWAP平均回帰）ロジックと技術スタック
- **実行インフラの根拠**:
  - **シングルブローカーAPI完結**: 注文執行と情報取得を単一の証券会社APIで行うことで、マルチプラットフォーム間に生じるレイテンシやスリッページを最小化し、バックテストと実運用の乖離を抑える。
  - **動的銘柄抽出（Ranking API）と監視制限**: KabuステーションのPush APIの登録上限（50銘柄）を前提とし、リアルタイムランキング等から抽出した上位50銘柄を監視対象としてストリーミング解析を行う。
- **データ分析プロセス**:
  - **データ処理・特徴量生成**: フラクショナル微分による時系列データの定常化処理、VWAP乖離率とオーダーブック・インバランスによる特徴量生成。
  - **板情報（気配値）ストリーミング解析**: 上位50銘柄の板情報を高頻度で取得し、巨大な指値（大口の壁）の検知、出来高の急増（Volume Spike）の監視を行う。
  - **見せ板（Spoofing）検知アルゴリズム**: 大口注文の滞留時間（Time-in-force）を計測し、一定時間維持された注文のみを有効なシグナルとし、頻繁にキャンセルされる見せ板を排除する。
  - **ピンポイントの執行価格決定**: エントリー価格およびエグジット（利確・損切）価格の決定において、上記で検知した「大口の壁」の直前を狙うことで有利な約定を目指す。
  - **ローカルLLM活用**: Gemma2 / Codestral / Llama を用いてセキュアな環境で最新市況を構造化し、レジーム判定の補助を行う。
- **プロセス**: kabuの「売買代金ランキングAPI」からトップ銘柄を動的に抽出（最大50銘柄）し、リアルタイム価格とVWAP乖離からの標準偏差異常値（Zスコア）や需給の歪みを算出。板情報解析でエントリー価格を最適化し発注。14:50の強制全決済処理（ドローダウン最小化）を予約。
- **モジュールのテスト責任帯**:
  - PandasとScipyに人為的に歪ませたデータ配列（Mock株価・板情報等）を与え、想定した閾値でトリガーが発火するか（Z-Score検知・特徴量算出）を検証するテストコードを記述。

### 4.3 投資信託運用・リバランスロジック (Passive Core)
- **バッチ処理**: 毎営業日の14:50（全決済完了後）にスケジューラから呼び出されるバッチ処理として実装。
- **モジュールのテスト責任帯**:
  - `Sweep_Amount`の計算において、前日未補填損失（`L_prev`）が正しく考慮され、利益の50%のみが投信買付に回るかを確認する単体テスト。
  - 資産比率が$\pm5\%$を超えた際、正しい金額がリバランス調整額として算出されるかをモック残高を用いて検証。

### 4.4 レポート自動生成エンジン (Report Generator)
- **処理プロセス**: 日次・月次・年次のタイミングで、DBから損益データや振替額を集計。同時にトレード履歴をローカルLLMに渡し、改善点・If-Thenシナリオ（エントリー/エグジットの最適化案）を生成させ、レポートファイル（PDF/Markdown）として `/data/reports` に保存する。

---

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

### 6.1 `Daily_Asset_Snapshot` (日次資産スナップショット)
毎日の資産状態を記録し、月次・年次のポートフォリオ成長曲線の描画に使用する。

| カラム名 | 型 | 説明 |
| :--- | :--- | :--- |
| `date` | Date | 記録日 (PK) |
| `bank_balance` | BigInt | 銀行預金残高（生活防衛費含む） |
| `trust_value` | BigInt | 投資信託 評価額 |
| `long_solid_value` | BigInt | 長期（堅実コア）評価額 |
| `long_growth_value` | BigInt | 長期（成長サテライト）評価額 |
| `short_term_capital`| BigInt | 短期枠 元本（利益再投資分を含む） |
| `cumulative_transfer`| BigInt | 短期枠から投資信託への累計振替額 |

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
