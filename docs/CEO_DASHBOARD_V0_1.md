# KIO CEO Dashboard v0.2

## Purpose

KIOの経営判断に必要な「判断待ち・機会・停滞」を一画面で確認するための読み取り専用ダッシュボードです。

このDashboardは新しい正本ではありません。正本は既存システムに残します。

- タスク・期限・担当: Asana
- 正式資料: Google Drive
- 会話・経緯: Slack / Gmail
- 技術変更: GitHub
- 過去資料発掘: NakadachiArchiveAI

`dashboard/data/dashboard.json` は表示専用のRead Modelで、いつでも再生成可能です。

## v0.2: Asana接続

Asanaから以下だけを直接取得します。

- `AI Chief of Staff / PMO` の `20｜CEO判断待ち` にある実タスク
- 主要KIOプロジェクトの未完了件数
- 最短期限
- 期限超過件数
- `Production Blocker` を含む明示的ブロッカー

テンプレート的な運用タスクはCEO判断から除外します。Asanaの全タスクをそのままDashboardへ流し込まず、CEOが行動すべき情報だけを表示します。

### 追跡対象プロジェクト

- イベント企画・実行
- なにわ大賞・なにわ名物てれび
- 大阪フリンジ／大阪文化万博
- Osaka Fringe Production 1.0
- AI Chief of Staff / PMO

## Mac mini periodic refresh

既存の `com.kio.local-ai-node` LaunchAgentをそのまま利用します。新しい常駐Agentは追加しません。

Local AI Node自体は10分周期で動き、その中でCEO Dashboardは既定30分周期でAsanaから再生成します。Dashboard更新失敗がEngineering Loopやheartbeatを止めないように分離されています。

### 初回のみ: Asana認証

```bash
zsh scripts/configure_ceo_dashboard_asana.sh
```

プロンプトにAsana Personal Access Tokenを入力します。入力内容は画面に表示されません。

トークンは次のローカルファイルだけに保存されます。

```text
~/.config/kio-node/env
```

このファイルはGitHubへコミットしません。

### Local AI Node再インストール / 更新

```bash
zsh scripts/install_kio_node_agent.sh
```

既存env設定は保持されます。新しいDashboard関連キーが存在しない場合だけ追記します。

### Refresh interval

既定値:

```text
KIO_CEO_DASHBOARD_REFRESH_SECONDS="1800"
```

30分です。必要なら `~/.config/kio-node/env` の値だけ変更します。

### 手動同期

```bash
set -a
source ~/.config/kio-node/env
set +a
python3 scripts/sync_ceo_dashboard_asana.py
```

## Local view

```bash
python3 -m http.server 8765 -d dashboard
```

ブラウザで `http://localhost:8765/` を開きます。

## Display sections

1. 今日のCEO判断事項 — Asana接続済み
2. 進行中案件 — Asana接続済み
3. 営業 — 次段階
4. 助成金 — 次段階
5. 広報・ブランド — 次段階
6. リスク・停滞 — Asana接続済み

上部は次の4指標だけです。

- 判断待ち
- 期限超過
- 機会
- 要注意表示

## Design rules

- Dashboard自体を正本にしない
- 保存先やDBを追加しない
- Asanaの全タスクを表示しない
- CEOが行動すべき異常・機会を優先する
- AI Agentごとの別Dashboardを作らない
- 外部送信、契約、支払、押印等の実行機能を持たせない
- Source Adapterは既存システムから読み取り、同じRead Modelへ正規化する
- 新しい常駐LaunchAgentをDashboard専用に増やさない

## Next adapters

1. Gmail: 重要返信待ち、営業フォロー
2. GitHub: CI / PR / 技術ブロッカー
3. Slack: 明示的な依頼・判断待ち
4. 助成金 / 外部機会: 公募・締切

ただし追加前に、Asanaだけで十分な情報は重複取得しないことを原則とします。
