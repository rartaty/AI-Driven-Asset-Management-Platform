"""
AIレポート自動生成モジュール (If-Then分析)
ローカルのOllama (Gemma 2 / Codestral等) を使用してトレードの反省レポートを生成する。
"""

import os
import requests
import json
from typing import Dict, Any, List
from datetime import datetime

class TradeAnalyzer:
    def __init__(self):
        # Ollamaのエンドポイントと使用モデルの設定
        self.ollama_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")
        self.model_name = os.getenv("OLLAMA_MODEL_NAME", "gemma2")
        # TRADE_MODE=PAPER のとき AI 推論はダミー文字列を返却 (要件 §3 / Phase 5: TEST_MODE deprecate)
        self.test_mode = os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER"
        self.latest_report = None  # 生成された最新のレポートを保持するキャッシュ

    def generate_daily_report(self, trade_data: List[Dict[str, Any]], target_date: str) -> str:
        """
        日次のトレード履歴を受け取り、Ollamaに投げてIf-Then分析のMarkdownレポートを生成する。
        """
        # プロンプトの構築（厳格なシステム指示とデータ）
        prompt = self._build_prompt(trade_data, target_date)

        if self.test_mode:
            # 安全装置: Ollamaに繋がない場合はモックレポートを返す
            report_text = self._get_mock_report(target_date)
            self.latest_report = report_text
            return report_text

        try:
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3 # 分析なので創造性より論理性を重視
                }
            }
            
            response = requests.post(self.ollama_url, json=payload, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            report_text = result.get("response", "エラー: AIからの応答が空でした。")
            self.latest_report = report_text
            return report_text
            
        except requests.exceptions.ConnectionError:
            print("エラー: Ollamaに接続できません。Ollamaが起動しているか確認してください。モックデータを返します。")
            return self._get_mock_report(target_date)
        except Exception as e:
            print(f"Ollama API Error: {e}")
            return f"**レポート生成エラー**\n\nAIモデル（{self.model_name}）での解析中にエラーが発生しました。\nエラー詳細: {str(e)}"

    def _build_prompt(self, trade_data: List[Dict[str, Any]], target_date: str) -> str:
        """
        AIに渡すためのプロンプトを組み立てる
        """
        data_str = json.dumps(trade_data, ensure_ascii=False, indent=2)
        
        return f"""
[PROMPT HIDDEN FOR PUBLIC RELEASE]
※独自のトレードノウハウ（If-Then分析の具体的な指示内容等）が含まれているため、
公開版ではAIへのプロンプト内容を非公開としています。
"""

    def _get_mock_report(self, target_date: str) -> str:
        return f"""# 日次トレード反省レポート ({target_date})

[MOCK REPORT HIDDEN FOR PUBLIC RELEASE]
※具体的な銘柄、戦略名、ダミーの分析結果などが含まれているため、
公開版では非公開としています。
"""
