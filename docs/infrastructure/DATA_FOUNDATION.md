# データ基盤詳細仕様書 (Data Foundation Specification)

> 本文書は本システムが扱うデータの取得経路・ETL・DB設計・シークレット管理・レート制限・監視の **インフラ基盤** を定義する。
> トレードロジック自体は対象外 (それは [docs/strategy/](../strategy/) 配下)。
>
> 上位文書: [REQUIREMENTS_DEFINITION.md §3 §8](../REQUIREMENTS_DEFINITION.md), [TECHNICAL_SPECIFICATION.md §1〜§3 §6](../TECHNICAL_SPECIFICATION.md)

---

## 1. 目的とスコープ
本ドキュメントが定義するもの:
- データソースとアクセス経路
- ETL (抽出・変換・正規化) 規約
- SQLite 主要テーブルの設計原則 (PostgreSQL 互換 SQL を SQLAlchemy 2.0 で生成)
- AWS SSM Parameter Store によるシークレット管理
- API レート制限 / Anti-BAN 機構
- 構造化ログ・Discord 通知・Kill Switch 連動

定義しないもの (別文書):
- 戦略・売買ロジック → [docs/strategy/](../strategy/)
- Kill Switch 詳細仕様 → [docs/operations/KILL_SWITCH_V2_SPEC.md](../operations/KILL_SWITCH_V2_SPEC.md)
- DB カラム詳細 → [TECHNICAL_SPECIFICATION.md §6](../TECHNICAL_SPECIFICATION.md)

---

## 2. データソース構成

| ソース | 経路 | アクセスモジュール (Phase 3 再編後) | データ性格 |
| --- | --- | --- | --- |
| EDINET API | REST + XBRLダウンロード | `backend/src/api/edinet.py` | 財務 (XBRL) |
| 三菱UFJ eスマート証券 API (kabuステーション) | REST + WebSocket Push | `backend/src/api/kabucom.py` | 株価・板情報・発注 |
| OpenCanvas API | REST | `backend/src/api/opencanvas.py` | 銀行残高 (読取専用) |
| yfinance | REST | `backend/src/api/yfinance.py` | 株価 (代替・補完) |
| FRED API | REST | `backend/src/api/yfinance.py` (同居) | マクロ経済指標 |

### 2.1 財務データ (EDINET API)
- **取得対象**: XBRL ファイル群
- **パーサ**: `BeautifulSoup4` + lxml
- **取得頻度**: 四半期ごと (決算報告タイミング)、または銘柄分析時のオンデマンド
- **データ用途**: V6 長期戦略の FCF / EBITDA / 総資産 / 発行済株式数 抽出

### 2.2 市場・株価データ (kabuステーション API)
- **取得対象**:
  - 日足 OHLCV (始値/高値/安値/終値/出来高)
  - リアルタイム板情報 (Push API)
  - リアルタイム約定情報 (Push API)
- **取得頻度**:
  - 日足: 営業日終了後の一括取得
  - Push: 短期トレード稼働中 (09:00-15:00) 常時購読
- **データ用途**: 長期 (大局判断) + 短期 (VWAP・OBI 計算)

### 2.3 マクロデータ (yfinance + FRED)
- **取得対象**:
  - USD/JPY 為替レート (yfinance)
  - 日本国10年国債利回り (FRED)
  - 米国 VIX 指数 (yfinance)
- **取得頻度**: 日次バッチ
- **データ用途**:
  - AI のマクロスコアリング補正 (ボラティリティ係数)
  - VIX ギア発火判定 (≤20 防御 / ≥35 攻撃)
  - ポートフォリオ動的見直しトリガー

---

## 3. ETL パイプライン詳細

### 3.1 財務データの正規化 (XBRL ベース)
| 処理 | 内容 |
| --- | --- |
| **FCF 算出** | `FCF = 営業CF − CAPEX (有形/無形固定資産の取得による支出)` を厳密に計算。EBITDA 算出も同様に標準化 |
| **IFRS / 日本基準対応** | 日本基準 (`jpcrp`) と IFRS (`ifrs`) でタグ名が異なるため、**マッピングテーブル** を実装し動的に吸収 |
| **異常値の平滑化** | 特別損益 (事業譲渡等) による FCF スパイク (前年比 +500% 等) を検知し、**過去3年平均値で平滑化** して保存 |

### 3.2 株価補正処理 (Adjusted Price)
| 処理 | 内容 |
| --- | --- |
| **株式分割・併合の遡及補正** | 発生時、過去の全株価データに対して **Adjusted Price 再計算** を実行し DB を上書き |
| **重要: 「分割落ち」「大暴落」の誤検知防止** | これを怠ると Price Range 10% 未満の買いシグナルとして誤発火 → **致命的バグ** を引き起こす |
| **欠損値処理** | 取引なし (流動性ゼロ) 等で値が欠損している場合、最大3営業日まで前日終値で **Forward-fill** |

### 3.3 サバイバーシップ・バイアスの排除
- 上場廃止・買収済銘柄も **DBから物理削除しない** (`Asset_Master.is_active=False` で論理削除)
- 過去の時点 (Point-in-Time) でのユニバース再現を可能にする (バックテスト時の必須要件)

---

## 4. データベース設計 (SQLite — 将来 PostgreSQL 15 互換)

### 4.1 主要テーブル一覧
| テーブル | 役割 | 詳細カラム |
| --- | --- | --- |
| `User_Settings` | 固定積立額・ドローダウン閾値・キルスイッチ状態 | (簡易) |
| `Asset_Master` | 銘柄マスタ (証券コード・社名・業種・`is_active`) | (簡易) |
| `Financial_Reports` | 財務データ (FCF/EBITDA/総資産/発行済株式数等) | **JSONB活用** で将来の科目追加に柔軟対応 |
| `Daily_Price_History` | 補正済み株価・出来高 | `(date, ticker_symbol)` 複合インデックス必須 |
| `Daily_Asset_Snapshot` | 日次資産スナップショット (両プール) | [TECHNICAL_SPECIFICATION.md §6.1](../TECHNICAL_SPECIFICATION.md) 参照 |
| `Trade_Tick_Log` | 短期トレード イントラデイ損益 | [TECHNICAL_SPECIFICATION.md §6.2](../TECHNICAL_SPECIFICATION.md) 参照 |
| `Trade_Logs` | 売買履歴 + AI 意思決定理由 | テキスト (構造化ロガー出力) |
| `System_Logs` | システムイベント・エラー | テキスト |
| `Report_Archive` | レポートメタデータ | [TECHNICAL_SPECIFICATION.md §6.3](../TECHNICAL_SPECIFICATION.md) 参照 |
| `Cash_Pool_Status` | リアルタイム両プール残高 | (簡易) |
| `Target_Portfolio` / `Market_Context` | 動的目標比率 + 市況スナップショット | (簡易) |

### 4.2 設計原則
- **生SQL禁止**: SQLAlchemy 2.0 ORM を経由 (SQLインジェクション排除)
- **冪等性 (Idempotency-Key)**: 発注テーブル等は UUID をキーに、二重発注を DB レベルで弾く
- **JSONB の活用**: 将来追加可能性のあるフィールド (財務科目・AI判断詳細) は JSONB 列に格納
- **インデックス**: 時系列クエリ高速化のため、`(date, ticker_symbol)` 等の複合インデックスを必須化

---

## 5. シークレット管理 (AWS SSM Parameter Store)

### 5.1 絶対ルール
- **コード内・`.env` ファイルへの API キー平文保存は禁止** (要件 E.6.1)
- **すべての認証情報は AWS SSM Parameter Store (KMS暗号化)** から取得
- 取得経路: `boto3` 経由でシステム起動時または初回API呼出時に動的取得

### 5.2 パラメータ命名規約
```
/big-tester/{env}/{service}/{key}
```
例:
- `/big-tester/prod/kabucom/api_token`
- `/big-tester/prod/opencanvas/client_id`
- `/big-tester/prod/discord/webhook_url`
- `/big-tester/dev/kabucom/api_token` (開発環境)

### 5.3 ローカル AWS 認証
- ホスト OS の `~/.aws/credentials` に IAM ユーザー (SSM 読取権限のみ) を配置
- コンテナへは `~/.aws` を read-only マウント
- IAM ユーザーには **ssm:GetParameter / ssm:GetParameters / kms:Decrypt** のみを許可 (最小権限原則)

### 5.4 キャッシュ
- `core/aws_ssm.py` は復号済み値を **TTL付き in-memory キャッシュ** に保持 (毎回SSMコール=不要なレイテンシ)
- TTL: 既定15分。シークレットローテーション時は手動 invalidate

---

## 6. API レート制限・Anti-BAN

### 6.1 Exponential Backoff
- 全 API リクエストにリトライロジックを実装
- 失敗時は `wait_time = base * (2 ** attempt) + jitter` で待機
- 最大リトライ回数: 3回 (超過時は Discord [CRITICAL] 通知 + Kill Switch 発動候補)

### 6.2 Token Bucket レート制限 (短期トレード API 向け)
- 証券会社からの API BAN・口座凍結を絶対回避するための物理機構
- **すべての REST API 呼出 (`/sendorder`, `/positions` 等) は内部「流量制限モジュール」経由**
- スロットル制御: 「秒間X回・分間Y回」の上限超過リクエストはキューに滞留 / 必要に応じて意図的に Drop
- 証券会社サーバーへの物理到達を阻止する

### 6.3 設計の根拠
- HFT 業者と物理的な通信速度では競合しないため、レート制限による多少の遅延は許容
- 一方、API BAN 発生時のシステム停止コストは極めて大きい → **保守的な制限が最適**

---

## 7. ログ・監視・エラーリカバリ

### 7.1 構造化ログ
- ライブラリ: `python-json-logger`
- すべての判断・発注・エラーを **JSON 形式** で出力
- 共通フィールド: `timestamp`, `level`, `component`, `event`, `payload`

### 7.2 Discord 通知
| イベント | タグ | 例 |
| --- | --- | --- |
| API 連続タイムアウト | `🔴 [CRITICAL] [API:Kabucom]` | `kabuステーションAPI 連続3回タイムアウト` |
| DB 接続エラー | `🔴 [CRITICAL] [DB]` | `SQLite database locked / file not found / disk full` |
| Kill Switch 発動 | `🔴 [CRITICAL] [KillSwitch]` | (KILL_SWITCH_V2_SPEC.md §6.2 参照) |
| 約定通知 | `🟢 [INFO] [Trade]` | 銘柄・約定数量・価格 |
| AI 推奨銘柄検知 | `🟡 [INFO] [AI]` | 銘柄・スコア・判断根拠サマリ |

### 7.3 Kill Switch 連動
- API 連続エラーがリトライ上限到達 → 自動で Kill Switch V2 発動を **検討** (条件は KILL_SWITCH_V2_SPEC.md §2.1 参照)
- 重大エラー検知 → Discord [CRITICAL] 通知 + ユーザー判断を待つ (誤検知での自動停止は損失機会)

---

## 8. 実装ファイル予定 (Phase 3 ディレクトリ再編準拠)

```
backend/src/
├── api/                            # 外部システムアダプタ層
│   ├── kabucom.py                  # 三菱UFJ eスマート証券 API
│   ├── opencanvas.py               # 銀行 API (読取専用)
│   ├── edinet.py                   # XBRL 財務データ
│   ├── yfinance.py                 # マクロ + 株価補完 (FRED含む)
│   └── specs/                      # YAML 仕様書
│       ├── kabu_STATION_API.yaml
│       └── ParaSOL-*.yaml
├── core/                           # 共通基盤層
│   ├── aws_ssm.py                  # SSM 復号 + TTL キャッシュ
│   ├── discord.py                  # Webhook 送信
│   ├── logger.py                   # python-json-logger 設定
│   ├── kill_switch.py              # Kill Switch V2
│   └── rate_limiter.py             # Token Bucket 流量制限
└── ...
```

---

## 9. 依存関係と着手順序

| Phase | 内容 | 前提 |
| --- | --- | --- |
| Phase 2 | スキーマ実装 (`backend/src/models/schema.py` 全面再設計) | 本書 §4 が確定 |
| Phase 3 | ディレクトリ再編 (services/ → api/ + core/ + services/) | Phase 2 完了 |
| Phase 4 | Kill Switch V2 実装 | Phase 2 (User_Settings 列追加) + Phase 3 (core/ 新設) 完了 |
| 別途 | docker-compose.yml に `/data/logs/` ホストマウント設定追加 | Phase 4 着手前 |
| 別途 | AWS IAM ユーザー作成 + SSM パラメータ事前登録 | 実運用開始前 |

→ **Phase 2 → Phase 3 → Phase 4 の順** で着手するのが安全。
