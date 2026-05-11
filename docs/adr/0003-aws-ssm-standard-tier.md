# ADR-0003: AWS SSM Parameter Store Standard tier 採用 (Secrets Manager 不採用)

- **Status**: Accepted
- **Date**: 2026-05-03
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (DB 選定 — シークレット保管先と独立)

---

## Context (背景)

要件 E.6.1 で「APIキー / シークレットの平文埋め込み禁止 / AWS SSM Parameter Store (KMS暗号化) 経由動的取得」が定められている。Phase 4 で `core/aws_ssm.py` を本実装するにあたり、AWS の選択肢として:
- **Parameter Store (Standard tier)**: 無料・10,000 パラメータまで・4KB/値
- **Parameter Store (Advanced tier)**: $0.05/月/パラメータ・100,000 まで・8KB
- **Secrets Manager**: $0.40/月/シークレット・組込ローテーション・64KB

の3択が存在。本PJは個人ローカル運用 + 月額予算 $1〜$2 程度希望 + シークレット数 13 件 (将来 8 API 追加で 21〜37 件) という前提。

判断を保留すると、Phase 4 の `core/aws_ssm.py` 実装が進められない (boto3 のどの API を呼ぶか・どのリソース体系を前提にするかが決まらない)。

## Decision (決定)

**AWS SSM Parameter Store の Standard tier を採用する。Secrets Manager は不採用。**

### 決定の骨子
- 全シークレット (kabu / OpenCanvas / Discord webhooks / Anthropic API key 等) を Parameter Store の **Standard tier** で管理
- 暗号化は **AWS 管理 KMS キー (`alias/aws/ssm`)** を使用 (カスタム CMK は不採用 — 月額 $1/key 課金回避)
- ローテーションは **手動 + APScheduler 期限通知 (案A)** で実装 (Secrets Manager 自動ローテーションの組込対応は本PJ用シークレット種別では使えないため)
- 5重防御で月額 $0 を確実化:
  1. アプリ層: `put_parameter()` ラッパーで `Tier='Standard'` 強制
  2. アプリ層: 書き込み関数を `core/aws_ssm.py` 1 箇所に集約
  3. IAM ポリシー: `ssm:Tier=Advanced` を deny 条件で拒否
  4. AWS Budgets: 月額 $0.50 で Discord 通知、$1 で IAM 自動 deny
  5. キャッシュ: プロセス内 24h TTL で KMS リクエスト数を最小化 (37 シークレット × 1日 = 月 1,110 calls / 無料枠 20,000 の 5.5%)

### Why
- **Secrets Manager の自動ローテーションが本PJ用途で使えない** — 組込対応は RDS/Aurora/Redshift 等のみ。kabu STATION / OpenCanvas / Discord はパスワード変更 API を提供しないため、自動ローテーションを実装しても Lambda 自作になり SSM での自前リマインダーと同じか多い手間
- **コスト**: Secrets Manager は 13 シークレット × $0.40 = $5.20/月 → 予算超過
- **設計の一貫性**: SSM Standard で全件統一すれば運用が単純

## Consequences (結果)

### ✅ Positive
- 月額コスト $0 (5重防御で課金トリガーを完全遮断)
- KMS 無料枠 20,000 calls/月 に対し、本PJ規模 (キャッシュあり) で 5% 以下の使用率
- AWS の標準的・実績豊富なサービス (将来クラウド化時にもそのまま使える)
- `put_parameter()` 経由の API キー登録なので、CLI からも操作可

### ⚠️ Negative / Trade-off
- **オフライン稼働不可**: 起動時に必ず AWS への接続必須 (キャッシュにより 2 回目以降は不要)
- **AWS アカウント・IAM 設定の初期コスト** 1〜2 時間 (1回限り)
- 自動ローテーションがない → APScheduler ジョブで期限通知 (案A) を別途実装要
- 組織監査ログ (CloudTrail) の確認に AWS Console アクセス要

### 🔄 Required Follow-up
- 📋 Phase 4: `core/aws_ssm.py` 本実装 (boto3 + KMS 復号 + 24h TTL キャッシュ + fail-fast) [SEC-2]
- 📋 Phase 4: `scripts/load-secrets.ps1` 作成 (AWS SSM → 環境変数 → claude 起動) [SEC-1]
- 📋 Phase 4 着手時: SSM パス命名規約確定 (推奨案: `/projectbig/{component}/{key-name}` ケバブ形式) [SEC-3]
- 📋 Phase 4: `scripts/register_secrets.py` (対話式 SSM 登録) [SEC-4]
- 📋 Phase 4 同期: `apiKeyHelper` 設定 (Anthropic API キーを SSM 経由 or KeePassXC 経由で取得) [SEC-5]
- 📋 Phase 4 同期: Claude `~/.claude/.credentials.json` を KeePassXC vault に移管 [SEC-6]
- 📋 Phase 4: AWS Budgets 設定 + IAM ポリシー設定 (Console 作業)
- 📋 Phase 4: APScheduler `check_secret_rotation` ジョブ追加 (毎月1日に LastModifiedDate チェック) [SEC-9]

## Alternatives Considered (検討した代替案)

### 案 X: AWS Secrets Manager 採用
- **概要**: 専用シークレット管理サービス・組込ローテーション・$0.40/月/シークレット
- **却下理由**: コスト $5.20/月 が予算超過 + 主要シークレット (kabu/銀行) で組込ローテが使えないため利点を享受できない

### 案 Y: SSM Advanced tier 採用
- **概要**: 100,000 パラメータ・8KB 値・パラメータポリシー (有効期限・通知)
- **却下理由**: $0.05/月/パラメータ × 13 = $0.65/月 のコスト + 本PJのシークレットサイズ (キー文字列) は Standard 4KB に十分収まる

### 案 Z: `.env` + gitignore (AWS 不採用 / ローカル完結)
- **概要**: AWS 不要・完全ローカル・無料・オフライン可
- **却下理由**: 要件 E.6.1 違反・OneDrive 同期リスク (本PJが OneDrive 配下のため) ・将来クラウド化時の移行コスト
- **部分採用**: Anthropic API キーのみ KeePassXC 採用 (ADR-0003 の補完として SEC-6)

### 案 W: HashiCorp Vault セルフホスト
- **概要**: OSS のシークレット管理ツール
- **却下理由**: 個人ローカル運用に対してインフラ負担が大きい・AWS で十分

## Related (関連)

- 関連 ADR: なし (本 ADR は単独)
- ハーネスタスクボード: [.claude/harness-taskboard.md](../../.claude/harness-taskboard.md) Topic 9 / SEC-1〜SEC-9
- VantageAI 参考設計: [.claude/references/vantageai-harness-reference.md](../../.claude/references/vantageai-harness-reference.md) §7 (GCP Secret Manager の流儀を AWS に置換)
- 関連 Memory: `~/.claude/projects/.../memory/project_absolute_prohibitions.md` #2

## Notes

### コスト計算根拠 (8 API 追加時の試算)
| シナリオ | シークレット総数 | 月間 KMS calls (キャッシュあり) | 無料枠使用率 |
| --- | --- | --- | --- |
| 控えめ (1日1再起動 / 各API 1キー) | 21 | 630 | 3% |
| 標準 (1日1再起動 / 各API 2キー) | 29 | 870 | 4% |
| ヘビー (1日3再起動 / 各API 3キー) | 37 | 3,330 | 17% |

→ 100 シークレットまでスケールしても無料圏内維持可能。

### IAM ポリシー雛形 (Phase 4 で実装)
```json
{
  "Statement": [
    { "Effect": "Allow", "Action": ["ssm:GetParameter*", "ssm:DescribeParameters"],
      "Resource": "arn:aws:ssm:*:*:parameter/projectbig/*" },
    { "Effect": "Allow", "Action": "ssm:PutParameter",
      "Resource": "arn:aws:ssm:*:*:parameter/projectbig/*",
      "Condition": { "StringEquals": { "ssm:Tier": "Standard" } } },
    { "Effect": "Deny", "Action": "ssm:PutParameter",
      "Resource": "*",
      "Condition": { "StringEquals": { "ssm:Tier": "Advanced" } } }
  ]
}
```
