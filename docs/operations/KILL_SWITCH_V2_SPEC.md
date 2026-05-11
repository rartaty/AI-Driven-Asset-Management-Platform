# Kill Switch V2 仕様書

> 本文書は致命的な金銭的損失を防ぐための最終防衛機構「Kill Switch V2」の詳細仕様を定義する。
> 上位文書: [REQUIREMENTS_DEFINITION.md §6](../REQUIREMENTS_DEFINITION.md), [REQUIREMENTS.md §5](../REQUIREMENTS.md), [TECHNICAL_SPECIFICATION.md §5](../TECHNICAL_SPECIFICATION.md)

---

## 1. 設計目的
- ポートフォリオの致命的な損失 (ドローダウン暴走・APIエラー連鎖) を物理的に阻止
- 単一プロセス内のメモリフラグではなく、**インフラレベル** での安全停止を保証
- システム再起動でもブロック状態を維持し、フラッシュクラッシュ後の不用意な自動再開を防ぐ

## 2. 発動条件 (Trigger Conditions)

### 2.1 自動発動
| 条件 | 閾値 | 検知主体 |
| --- | --- | --- |
| ポートフォリオ ドローダウン超過 | `User_Settings.max_drawdown_limit` (既定 -3%) | 監視ジョブ ([scheduler.py](../../backend/src/services/scheduler.py)) |
| HTTP エラー連鎖 | kabuステーション/OpenCanvas API HTTP 5xx 連続3回以上 → リトライ上限到達 | API client (Exponential Backoff 実装側) |
| キャッシュフロー異常 | (将来) 想定外の大量出金検知 | 監視ジョブ |

### 2.2 手動発動
- Discord WebHook 経由のコマンド (例: `!killswitch on`)
- Next.js ダッシュボード上の緊急停止ボタン

---

## 3. 永続化機構 (二重防御)

### 3.1 DB Flag
- カラム: `User_Settings.is_kill_switch_active` (Boolean, default `False`)
- 発動時 `True`、解除時 `False`
- 全買付関数の入口で参照

### 3.2 物理 `.kill.lock` ファイル
- 場所: ホスト共有マウント `/data/logs/.kill.lock`
- 発動時に生成、解除時に削除
- ファイル内容: 発動 timestamp + 発動理由 (JSON)

```json
{
  "activated_at": "2026-05-01T10:23:45+09:00",
  "trigger": "drawdown_exceeded",
  "drawdown_pct": -0.0312,
  "manual": false
}
```

### 3.3 二重防御の理由
- **DBアクセス失敗時**: ファイルだけで阻止可能 (SQLite ファイル破損・ロック・disk full 時のフェイルセーフ。将来 PostgreSQL 移行後は接続障害時にも同様に機能)
- **コンテナ再起動時**: ホスト永続ボリューム上のため、コンテナを破棄しても残存
- **両者の同期**: 整合性チェックを起動時に実施。不整合なら **より厳しい側 (= 発動状態)** を採用

### 3.4 起動時シーケンス
```
1. アプリ起動 → /data/logs/.kill.lock 存在チェック
2. ファイル存在 → スリープモード (= 発注ブロック状態維持) で起動
   → DB の is_kill_switch_active も True に同期
3. ファイル不在 + DB True → ファイル再生成 + 発動状態維持
4. ファイル不在 + DB False → 通常起動
```

---

## 4. 非対称オーバーライド (Asymmetric Override)

### 4.1 ブロック対象
新規ポジション構築につながる API 呼出をすべてブロック:
- 現物買付 (`place_buy_order`)
- 信用買付 (`place_margin_buy`)
- 空売りエントリー (`place_short_sell_entry`)

### 4.2 許可対象 (重要)
**既存ポジションの決済 (Exit) は許可** する。これは下記の致命的損失を回避するため必須:
- ショート建玉を持っている状態で、kill switch がエグジットを止めると → 損切できず**無限損失**化リスク
- 14:50 EOD_FLATTEN による強制決済も継続実行可能

許可対象:
- 買い建玉の利確/損切売却 (`place_sell_close`)
- ショート建玉の買戻し決済 (`place_buy_to_cover`)
- 14:50 強制全決済ジョブ (`job_profit_sweep` 内の決済処理)

### 4.3 実装パターン
```python
# backend/src/core/kill_switch.py
class KillSwitchError(Exception):
    """Kill switch is active — new entry blocked."""

def assert_not_kill_switched_for_entry():
    """
    新規エントリー前に必ず呼び出す。発動中なら例外送出。
    Exit (決済) 系の関数では呼び出さない。
    """
    if is_kill_switch_active():
        logger.critical(f"[KillSwitch] New entry blocked")
        notify_discord("[CRITICAL] [KillSwitch] New entry attempt blocked")
        raise KillSwitchError("Kill switch active — new entry blocked")
```

---

## 5. 解除手順

### 5.1 自動解除 (禁止)
- 時間経過による自動解除は **行わない**
- 「価格が戻った」「ボラティリティが下がった」等の市場条件による自動解除も **行わない**
- 理由: 一過性のフラッシュクラッシュ後の不用意な自動再開を防ぐ

### 5.2 手動解除
以下のいずれかでのみ解除可能:
1. **Discord WebHook 経由**: `!killswitch off` コマンド (要管理者認証)
2. **Next.js UI**: 緊急停止解除ボタン (要管理者ログイン)
3. **緊急時** (上記2系統が動かない場合): DB 直接操作 + `.kill.lock` 手動削除

### 5.3 解除後シーケンス
```
1. ユーザーが解除コマンド発行
2. is_kill_switch_active を False に更新
3. .kill.lock ファイルを削除
4. Discord に [INFO] [KillSwitch] 解除通知
5. 次の発注タイミングから通常運用復帰
```

---

## 6. ログと通知

### 6.1 ログ
- すべての発動・解除イベントを `python-json-logger` で構造化記録
- フィールド: `timestamp`, `event` (`activated`/`released`), `trigger`, `drawdown_pct`, `manual`, `user_id` (手動時)

### 6.2 Discord 通知
| イベント | タグ | 通知内容 |
| --- | --- | --- |
| 自動発動 | `🔴 [CRITICAL] [KillSwitch]` | 発動理由 + ドローダウン値 + 現在のポジション一覧 + 解除手順案内 |
| 手動発動 | `🟡 [WARN] [KillSwitch]` | 発動者 + 発動理由メモ |
| 解除 | `🟢 [INFO] [KillSwitch]` | 解除者 + 発動からの経過時間 |
| ブロック検知 | `⚠️ [WARN] [KillSwitch]` | ブロックされた発注詳細 (頻発時はログ抑制) |

---

## 7. テスト要件

[testing.md の異常系テスト](../../.claude/testing.md) と整合。以下を必須検証ケースとする:

### 7.1 自動発動テスト
- [ ] ドローダウン -3.0% を僅かに超える時系列で `is_kill_switch_active=True` 発火
- [ ] HTTP 5xx 連続3回モックで kill switch 発動

### 7.2 非対称オーバーライドテスト
- [ ] `is_kill_switch_active=True` 状態で `place_buy_order` 呼出 → `KillSwitchError` 例外
- [ ] `is_kill_switch_active=True` 状態で `place_sell_close` 呼出 → 正常実行
- [ ] `is_kill_switch_active=True` 状態で `place_buy_to_cover` 呼出 → 正常実行 (ショート決済)
- [ ] 14:50 強制全決済ジョブが kill switch 発動中でも完走する

### 7.3 永続化テスト
- [ ] `.kill.lock` 存在下でアプリ起動 → スリープモード起動 + DB Flag 同期
- [ ] DB Flag True / `.kill.lock` 不在 → ファイル再生成 + 発動状態維持
- [ ] 両方 False → 通常起動

### 7.4 解除テスト
- [ ] 手動解除コマンド → DB Flag False + ファイル削除 + Discord 通知
- [ ] 解除後の次の発注 → ブロックされず通常実行

---

## 8. 実装ファイル (予定)
| ファイル | 役割 |
| --- | --- |
| `backend/src/core/kill_switch.py` | 本体 (発動・解除・チェック関数) |
| `backend/src/models/schema.py` | `User_Settings.is_kill_switch_active` 列追加 |
| `backend/src/routers/admin.py` | 手動制御エンドポイント (解除API) |
| `backend/src/services/scheduler.py` | 監視ジョブ (drawdown チェック) |
| `backend/tests/test_kill_switch.py` | 必須検証ケースの実装 |

---

## 9. 依存関係 (実装着手前の前提)

| 依存 | 状態 |
| --- | --- |
| ホスト共有ボリューム `/data/logs/` のマウント設定 | docker-compose.yml で指定が必要 (未確認) |
| Discord webhook 認証付き解除コマンド機構 | core/discord.py 実装後に追加 |
| Next.js 緊急停止解除ボタン | フロントエンド実装後 |
| User_Settings に `is_kill_switch_active` 列 | スキーマ更新 (Phase 2) で追加 |

→ Phase 4 (Kill Switch V2 実装) は **Phase 2 (スキーマ更新) 完了後** に着手するのが安全。
