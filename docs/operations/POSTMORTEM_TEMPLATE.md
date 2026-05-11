# Postmortem Template — 個人運用版

> **Status**: v1 (2026-05-09 / Phase 6 / ADR-0010 §6-7)
> **Scope**: 個人運用なので商用 Blameless Postmortem は不要、**5 Whys + 再発防止策の 1 枚物** で OK。
> **使い方**: 障害修復完了後、本テンプレを `docs/postmortems/<YYYY-MM-DD>-<short-title>.md` にコピーして埋める。
> **関連**: [INCIDENT_RESPONSE.md §6](INCIDENT_RESPONSE.md), [docs/runbooks/](../runbooks/)

---

## テンプレ (以下をコピーして使う)

```markdown
# Postmortem: <YYYY-MM-DD> <障害短タイトル>

> **Severity**: Critical / High / Med / Low
> **Status**: Draft / Reviewed / Closed
> **Author**: <自分>
> **Incident ID**: <IR-NNN>

---

## 1. サマリ (3 行以内)
何が起きたか・何に影響したか・どう直したか。

## 2. タイムライン (時刻順)
| 時刻 | イベント |
| --- | --- |
| HH:MM | <Discord 通知 / 自動発火> |
| HH:MM | <自分が気づいた> |
| HH:MM | <一次対処> |
| HH:MM | <根本原因仮説> |
| HH:MM | <修復実行> |
| HH:MM | <復旧確認> |

## 3. 影響範囲
- bucket: (Passive / Long_Solid / Long_Growth / Short / 全体)
- 機能: (発注 / 残高同期 / Discord 通知 / バックアップ / etc.)
- 金銭影響: (損失 ¥ / 機会損失 ¥ / なし)
- データ影響: (Daily_Asset_Snapshot N 日欠損 / Market_Ticks gap / etc.)

## 4. 根本原因 (5 Whys)
症状から原因まで「なぜ?」を 5 回繰り返す:

1. **症状**: ...
   - **なぜ?** ...
2. **直接原因**: ...
   - **なぜ?** ...
3. **中間原因**: ...
   - **なぜ?** ...
4. **背景原因**: ...
   - **なぜ?** ...
5. **根本原因**: ...

## 5. 一次対処 (止血)
障害発生後、最初に行った「広がりを止める」措置:
- [ ] Kill Switch 全体 / bucket 別 ON
- [ ] アプリ停止 (Podman down)
- [ ] バックアップから復元
- [ ] 該当ロジックを bypass / disable
- [ ] その他: ...

## 6. 根本対処 (再発防止)
今後同種障害が起きないようにするための変更:

| 種別 | 対応 | 担当 / 期限 |
| --- | --- | --- |
| コード修正 | ... | <自分> / YYYY-MM-DD |
| テスト追加 | <test_*.py に追加> | <自分> / YYYY-MM-DD |
| Runbook 更新 | RB-XXX に確認手順追加 | <自分> / YYYY-MM-DD |
| ドキュメント | INCIDENT_RESPONSE.md / SLI_SLO.md 更新 | <自分> / YYYY-MM-DD |

## 7. 良かった点 (続けるべき)
障害対応で機能した手順 / 仕組み:
- ...

## 8. 改善が必要な点
障害対応で機能しなかった / 遅かった手順:
- ...

## 9. アクションアイテム (TODO)
| # | 内容 | Status | 期限 |
| --- | --- | --- | --- |
| 1 | ... | Open / In Progress / Done | YYYY-MM-DD |
| 2 | ... | ... | ... |

## 10. 関連 Runbook / ADR
- [RB-XXX](../runbooks/RB-XXX-...md)
- [ADR-XXXX](../adr/XXXX-...md)

---
```

---

## 運用ルール (個人運用)

### いつ書くか
- **Critical / High** Severity の障害: **必ず書く**。修復完了から 24 時間以内。
- **Med** Severity: 同種障害が 1 ヶ月内に 2 回以上発生したら書く。
- **Low**: 任意 (System_Logs を見て後で振り返れる程度のメモ)。

### 書く目的
- **学びの記録** — 個人運用なので「過去の自分」が忘れた時に役立つ
- **再発防止の強制** — Action Items を書くと「直そう」というプレッシャーが生まれる
- **将来チーム化時の引き継ぎ資料** — 過去障害集は強力な onboarding 教材になる

### 書かないルール
- **書く前に Kill Switch 解除を急がない** — Postmortem 未作成 → IR §4 解除条件不満たし → 解除しない
- **詳細を埋めるのをサボらない** — 1 ヶ月後に読み返したとき意味がわからない記述は意味がない

---

## 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成 (Phase 6 / ADR-0010 §6-7 / 個人運用版テンプレ) |