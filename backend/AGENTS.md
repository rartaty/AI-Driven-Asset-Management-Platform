# AGENTS.md — backend/

> backend/ 配下で作業するときに追加ロードされる Python 固有規約。
> 共通規約 (Python全般・テスト) は [@../.claude/code-style.md](../.claude/code-style.md) と [@../.claude/testing.md](../.claude/testing.md) を必ず参照。

---

## スタック概要
- Python 3.11
- FastAPI (REST API)
- SQLAlchemy 2.0 (ORM・Mapped Style)
- APScheduler (時間ベースジョブ)
- pandas / yfinance (データ取得・分析)
- pytest 8.0 + pytest-mock 3.12 (テスト)
- Pydantic 2.5+ (新規データ構造の標準)

## 環境セットアップ
```powershell
cd backend
.\venv\Scripts\Activate.ps1
pytest tests/ -v   # 動作確認
```

---

## 必読 (auto-import)
@../.claude/code-style.md
@../.claude/testing.md

---

## backend 固有の必須遵守事項

### DB モデル命名 (PEP8 例外)
- DB モデルクラス名は **`PascalCase_With_Underscores`** (例: `User_Settings`, `Asset_Master`, `Daily_Asset_Snapshot`)
- これは PEP8 非準拠だが本プロジェクト固有規約として継続。新規モデルも踏襲する。

### Trading Mode 分岐
```python
import os
mode = os.getenv("TRADE_MODE", "PAPER").upper()
if mode == "REAL":
    # 本番経路 (ユーザー明示確認必須)
    ...
else:
    # PAPER 経路 (デフォルト)
    ...
```
- `os.getenv("TRADE_MODE")` 直接呼び出しを必ず使用。設定値のキャッシュ禁止。
- REAL 経路は環境変数未設定時に到達不能であること。

### スケジューラ追加
- 新しい時間ベースジョブは [scheduler.py](src/services/scheduler.py) の `_register_jobs()` 内で登録
- `CronTrigger` には必ず `day_of_week='mon-fri'` を付与 (土日の暴走防止)
- ジョブ関数は同ファイル内に配置 (1ファイル完結)

### 14:50 強制決済
- スケジューラ経由のみ。inline `time.sleep()` / `threading.Timer` での代替実装禁止
- これは要件 §6 のフェイルセーフ要件であり、設計レベルで一元化されている

### エラー処理
- 例外メッセージには **`[COMPONENT]` プレフィクス** を必ず付与
  - 例: `[DB]`, `[API:Kabucom]`, `[Strategy:VWAP]`, `[Notify:Discord]`
- `except: pass` / bare `except` 禁止 (Fail-Fast)
- 例外は最低限ログ + Discord 通知 + re-raise

### 外部 API 呼び出し前のチェック
- **買付・発注パス**は実行前に `is_kill_switch_active` を必ずチェック
- ドローダウン閾値は `User_Settings.max_drawdown_limit` から取得 (ハードコード禁止)

### シークレット
- API キー・パスワード・Webhook URL は **AWS SSM Parameter Store** 経由で動的取得 (要件 E.6.1)
- 開発時の暫定として `.env` (gitignore済) → `os.getenv()` も可
- ソースコードへの平文埋め込みは絶対禁止 (ルート CLAUDE.md 絶対禁止2)

---

## 高リスクファイル編集時の追加確認

| 編集対象 | 追加対応 |
| --- | --- |
| `src/models/schema.py` | `permissions.ask` 対象。編集後 [docs/DB_MIGRATION_GUIDE.md](../docs/DB_MIGRATION_GUIDE.md) と整合確認 |
| `src/services/scheduler.py` | `permissions.ask` 対象。編集後 `paper-trade-validator` agent に確認依頼 |
| `src/strategy/**` | 編集後 `strategy-reviewer` agent に確認依頼 |

---

## テスト規約 (要点のみ・詳細は @../.claude/testing.md)
- 新規ロジック追加時は `backend/tests/test_<module>.py` を必ず作成
- 必須3カテゴリ: 正常系 / 異常系 / 境界値
- 本番 API への実通信禁止 — `mocker.patch` または `responses` でモック化
- 時刻依存テストは `freezegun` で固定 (未導入の場合は引数注入)
- `TRADE_MODE=PAPER` 固定で実行
