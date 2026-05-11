# AI-Driven Asset Management Platform

An automated stock trading and asset management platform integrating bank and securities APIs with AI-driven decision-making. 

This project features a comprehensive architecture designed for both long-term fundamental investing and short-term trading strategies, managed through a unified dashboard.

## Overview

The platform is designed to automate trading logic while adhering to strict risk management and portfolio rebalancing rules. It utilizes advanced AI models for post-trade analysis (If-Then reporting) and dynamic strategic planning.

### Key Features
- **Unified Dashboard**: Web-based Next.js interface for real-time monitoring of bank balances, buying power, and portfolio status.
- **Dual Engine Strategy**:
  - **Long-Term Core**: Focuses on stable growth and fundamental analysis.
  - **Short-Term VWAP**: Exploit market inefficiencies with mean-reversion and momentum tracking.
- **AI Integration**: Automatic generation of daily trade reflection reports using local LLMs.
- **Paper Trading & Live Execution**: Robust simulation data generation alongside actual broker API connectivity (Mitsubishi UFJ eSmart Securities).

*Note: Proprietary trading algorithms and parameters, as well as AI prompt configurations, have been redacted from this public repository to protect the system's Alpha.*

## Tech Stack

- **Frontend**: Next.js, React, TailwindCSS, TypeScript
- **Backend**: Python (FastAPI), SQLAlchemy, SQLite (for local development)
- **AI/LLM**: Local Ollama (Gemma 2 / Codestral)
- **APIs**: kabuステーション API (Mitsubishi UFJ eSmart Securities)

## Disclaimer
This repository contains a sanitized version of the trading system. All sensitive logic, configuration files, and trading histories have been safely removed or redacted for public release.
