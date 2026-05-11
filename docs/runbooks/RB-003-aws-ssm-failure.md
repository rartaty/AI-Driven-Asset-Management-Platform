# RB-003: AWS SSM 認証失敗

> **Severity**: Critical | **Category**: F3 (外部 API 障害) — システム起動を物理的に阻止する致命層

## 1. 症状
- 起動時例外: `[AWS_SSM] get_parameter failed for path '/projectbig/...'`
- `[Secrets] Could not resolve secret for '<key>' from both SSM and environment variables`
- システム全体が立ち上がらない (kabu / OpenCanvas / Discord 全件で SSM 経由認証必須)

## 2. 影響範囲
- 全機能停止 (要件 E.6.1 / ADR-0003)

## 3. 検知方法
```sql
SELECT timestamp, event FROM system_logs
 WHERE component LIKE '[AWS_SSM]%' OR component LIKE '[Secrets]%'
 ORDER BY timestamp DESC LIMIT 20;
```
ターミナル起動ログ:
- `botocore.exceptions.ClientError: ParameterNotFound`
- `botocore.exceptions.NoCredentialsError`
- `botocore.exceptions.ExpiredTokenException`

## 4. 確認手順
- [ ] **AWS 認証情報を確認**:
  ```powershell
  aws sts get-caller-identity
  ```
- [ ] **SSM パラメータの存在確認**:
  ```powershell
  aws ssm get-parameter --name /projectbig/kabucom/api-password --with-decryption
  ```
- [ ] **AWS Budgets 上限を超えていないか** ([ADR-0003](../adr/0003-aws-ssm-standard-tier.md) 5重防御 §4)
- [ ] **`scripts/load-secrets.ps1`** の実行履歴確認

## 5. 解消手順
1. **認証情報期限切れ**: `aws sso login` または IAM ロール再生成
2. **パラメータ消失**: `scripts/register_secrets.py` 再実行
3. **Budgets 超過**: [ADR-0003](../adr/0003-aws-ssm-standard-tier.md) 5重防御 §4 該当 → IAM 自動 deny の解除手順
4. **応急処置**: 環境変数 fallback で起動 (テスト用)
   ```powershell
   $env:KABUCOM_API_PASSWORD = "<value>"
   $env:DISCORD_WEBHOOK_SYSTEM = "<value>"
   ...
   ```
   ※ ただし要件 E.6.1 違反になるので**当日中に SSM 復旧必須**

## 6. エスカレーション
- AWS 側障害: AWS Health Dashboard 確認 → 復旧待ち
- KMS キー削除 (誤操作含む): 復旧不能 → SSM 全パラメータ再登録 (パスワード等は手元から)
- Budgets 自動 deny: AWS Console で手動解除後、再設定

## 関連
- [ADR-0003](../adr/0003-aws-ssm-standard-tier.md) (SSM Standard tier 5重防御)
- [backend/src/core/aws_ssm.py](../../backend/src/core/aws_ssm.py)
- [backend/src/core/secrets.py](../../backend/src/core/secrets.py)
- [scripts/load-secrets.ps1](../../scripts/load-secrets.ps1)
- [scripts/register_secrets.py](../../scripts/register_secrets.py)
- [docs/operations/AWS_SETUP_GUIDE.md](../operations/AWS_SETUP_GUIDE.md)