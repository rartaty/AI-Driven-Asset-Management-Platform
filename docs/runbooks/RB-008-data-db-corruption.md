# RB-008: data.db の破損疑い

> **Severity**: Critical | **Category**: F1 (DB データ破損) — システム全停止の最重要事案

## 1. 症状
- 起動時例外: `sqlite3.DatabaseError: database disk image is malformed`
- 任意のクエリで `Could not open database` / `unable to open database file`
- FK 違反例外が連発 (`FOREIGN KEY constraint failed`)
- `data.db-journal` が残り続ける (commit 失敗の証拠)

## 2. 影響範囲
- **システム全停止**

## 3. 検知方法
```powershell
sqlite3 backend/src/data.db "PRAGMA integrity_check;"
# 期待: "ok"
# 異常: 行ごとに破損レコードが列挙される

sqlite3 backend/src/data.db "PRAGMA quick_check;"  # 高速版
```

## 4. 確認手順
- [ ] **バックアップの存在確認**:
  ```powershell
  python scripts/backup_db.py  # または ls backups/data-*.db
  ```
- [ ] **直近の disk full / 強制終了履歴** — Windows イベントビューア
- [ ] **`data.db-journal` の有無** (commit 中断の痕跡)
- [ ] **`data.db-wal` の有無** (WAL モードで commit 中断の痕跡)
- [ ] **OneDrive sync 衝突** — このプロジェクトは OneDrive 配下 (working dir = `OneDrive\デスクトップ\Project Big Tester`)、sync conflict による file lock の可能性

## 5. 解消手順
1. **アプリ停止** (Podman / プロセス kill — 書込中の停止は破損を悪化させるので注意):
   ```powershell
   podman compose down
   ```
2. **journal/WAL ファイル退避** (証拠保全):
   ```powershell
   Move-Item backend/src/data.db-journal backends/incident-<id>-journal.bak -ErrorAction SilentlyContinue
   Move-Item backend/src/data.db-wal backends/incident-<id>-wal.bak -ErrorAction SilentlyContinue
   ```
3. **破損 DB を保存** (ポストモーテム用):
   ```powershell
   Copy-Item backend/src/data.db backups/incident-<id>-corrupted.db
   ```
4. **バックアップから復元**:
   ```powershell
   Copy-Item backups/data-<最新日付>.db backend/src/data.db
   ```
5. **bucket 別 JSON 論理 dump で部分復旧** (バックアップ不足時):
   ```powershell
   python scripts/export_buckets.py  # 失敗するなら別 backup から
   # 各 bucket JSON を手動で再 import
   ```
6. **integrity_check で OK 確認**
7. **アプリ再起動 + 1 時間観察**

## 6. エスカレーション
- バックアップも破損 → 週次 export_buckets の出力から `Trade_Logs` を再構築 (損失あり)
- すべて失われた → 仕方なく最初から (Asset_Master / User_Settings は手動再投入)

## 関連
- [scripts/backup_db.py](../../scripts/backup_db.py)
- [scripts/export_buckets.py](../../scripts/export_buckets.py)
- ADR-0007 OQ-6 (DB 破損対策はバックアップ運用で十分)