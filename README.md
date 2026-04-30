# AI連携型 自動株取引・資産管理システム

「Secure by Design」を核とした、ローカルLLM駆動型の資産管理・運用アプリケーションです。  
個人の資産運用における意思決定をローカル環境のAI（Llama3.1/Gemma2等）で高度化し、セキュリティとプライバシーを両立しながら自動実行・管理するシステムを目指しています。  

【基本的なコンセプト】  
Privacy First & Secure by Design: APIキーなどの機密情報はAWS SSM/KMSで動的に管理し、ソースコードへのハードコードを徹底排除しています。  
Local Intelligence: Ollamaを用いたローカルLLM基盤により、外部APIにデータを送ることなく、高度な市場分析と意思決定を行います。  
Hybrid Strategy: Gemma 2による長期運用ロジックと、Codestralによる短期VWAP平均回帰ロジックを組み合わせたハイブリッド運用を実現します。  

【主要機能】  
＜自動トレード実行＞  
三菱UFJeスマート証券（KabuステーションAPI）と連携したリアルタイム自動発注。  
＜インテリジェント・スイープ＞  
NTTデータのクラウドサービス（OpencanvasAPI）を活用した、証券・銀行間の利益振替および残高照会の自動化。  
＜AI分析レポート＞  
取引結果をLLMがIf-Then分析し、Discord経由で人間が理解しやすい形式でフィードバック。  
＜セキュア・ダッシュボード＞  
Next.js + FastAPI構成による、資産状況の可視化と運用ロジックの制御。  

【技術スタック】  
＜Frontend / Backend＞  

Frontend: Next.js, React, Tailwind CSS  
Backend: Python 3.11, FastAPI  
ORM: SQLAlchemy + Pydantic (Strong Type Safety)  

＜AI / Data Science＞  
Inference Engine: Ollama  
Models: Gemma 2 (長期分析), Codestral / Llama 3.1 (短期判定)  
Logic: VWAP Mean Reversion, Order Flow Analysis  

＜Infrastructure / Security＞  
Runtime: Podman (Rootless Mode / Containerization)  
Orchestration: Docker Compose  
Database: PostgreSQL 15 (JSONB for trade logs)  
Secret Management: AWS SSM Parameter Store / KMS  

【システムアーキテクチャ】

```mermaid
flowchart LR
%% スタイルの定義
classDef frontend fill:#ffffff,stroke:#0070f3,stroke-width:2px,color:#333;
classDef backend fill:#ffffff,stroke:#009688,stroke-width:2px,color:#333;
classDef database fill:#ffffff,stroke:#336791,stroke-width:2px,color:#333;
classDef ai_model fill:#ffffff,stroke:#8e44ad,stroke-width:2px,color:#333;
classDef external fill:#f8f9fa,stroke:#6c757d,stroke-width:2px,stroke-dasharray: 5 5,color:#333;
classDef userNode fill:#333333,stroke:#333333,stroke-width:2px,color:#fff;

%% ノードの配置
User((ユーザー)):::userNode
UI[Frontend UI Next.js]:::frontend
API[Backend API FastAPI / Python 3.11]:::backend

DB[(PostgreSQL 15 SQLAlchemy/JSONB)]:::database
LLM_G[Gemma 2 長期コア・サテライト推論]:::ai_model
LLM_C[Codestral レジーム判定・VWAP短期ロジック]:::ai_model

AWS[AWS SSM/KMS セキュア鍵管理]:::external
GMO[GMOあおぞらネット銀行API]:::external
Kabu[三菱UFJeスマート証券 KabuステーションAPI]:::external
Discord[Discord Webhook 通知・監視]:::external

%% 接続フロー（線が被らないように配置順序を調整）
User --> UI
UI <-->|REST API| API

%% 上方向へ展開 (AI/DB)
API <-->|ORM Data Persistence| DB
API <-->|プロンプト推論依頼| LLM_G
API <-->|If-Then分析依頼| LLM_C

%% 右方向へ展開 (外部サービス)
AWS -.->|API認証情報 動的取得| API
API <-->|Profit Sweep・残高取得| GMO
API <-->|ストリーミング板情報解析・自動発注| Kabu
API -->|キルスイッチ・約定アラート| Discord

%% 配置を整えるための制約
UI --- API
API --- Kabu
