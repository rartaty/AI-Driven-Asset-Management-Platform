# Architecture Decision Records (ADR)

> 本PJの **アーキテクチャレベルの重要決定** を 1 決定 1 ファイルで記録する。
> 軽量な決定は [taskboard.md Decisions Log](../../taskboard.md) 側で運用 (ハイブリッド方針)。

---

## ADR 採用基準 (ADR にするか taskboard で済ますかの判断)

**ADR にする** (重い決定):
- 後から覆すコストが大きい (DB 選定 / 認証方式 / 主要 API 選定)
- 複数モジュールに跨る原則 (動的比率 / 投資判断ファースト / セキュリティ方針)
- 将来「なぜこれを選んだか」を別の人 (or 未来の自分) が知りたくなる
- 代替案を明示的に却下した記録が必要

**taskboard.md Decisions Log で十分** (軽い決定):
- ファイル名・関数名・小規模リファクタの方針
- 戦略パラメータ (Z-score 閾値・lookback 期間等)
- ツールの個別選定 (CSS フレームワーク等の lifestyle 判断)
- 実装中の戦術的トレードオフ

---

## ADR インデックス

| # | タイトル | 状態 | 日付 |
| --- | --- | --- | --- |
| [0001](0001-sqlite-as-primary-db.md) | SQLite を本番 DB として採用 (PostgreSQL は将来オプション) | Accepted | 2026-05-03 |
| [0002](0002-dynamic-portfolio-investment-first.md) | 動的トップレベル比率 + 投資判断ファースト原則 | Accepted | 2026-04-30 |
| [0003](0003-aws-ssm-standard-tier.md) | AWS SSM Parameter Store Standard tier 採用 (Secrets Manager 不採用) | Accepted | 2026-05-03 |
| [0004](0004-harness-9-layer-vantageai.md) | Claude Code ハーネス 9 層構成 (VantageAI 流儀準拠) | Accepted | 2026-05-02 |
| [0005](0005-phase4-scope-standard.md) | Phase 4 スコープ = 案B Standard (core/ 3件 + Kill Switch + SSM 切替) | Accepted | 2026-05-03 |

---

## 新規 ADR の追加手順

1. [`0000-template.md`](0000-template.md) をコピー
2. 連番でファイル名: `NNNN-kebab-case-title.md` (例: `0006-frontend-css-modules-migration.md`)
3. 内容を埋めて状態を `Proposed` に
4. ユーザー承認後 `Accepted` に変更
5. 本 README の表に 1 行追記
6. 関連 ADR からの双方向リンク追加 (Related セクション)
7. taskboard.md Activity Log に追記

## 状態遷移

```
Proposed → Accepted → Deprecated
                   ↓
              Superseded by ADR-NNNN
```

- **Proposed**: 提案段階 (議論中)
- **Accepted**: 確定・実装中 or 実装済み
- **Deprecated**: 採用していたが効力停止 (代替案を別 ADR で定義)
- **Superseded**: 別 ADR で書き換えられた (リンク先を参照)

## 参考フォーマット
本PJは [MADR (Markdown ADR)](https://adr.github.io/madr/) 軽量版に準拠。
