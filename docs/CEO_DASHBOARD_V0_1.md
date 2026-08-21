# KIO CEO Dashboard v0.1

## Purpose

KIOの経営判断に必要な「判断待ち・機会・停滞」を一画面で確認するための読み取り専用ダッシュボードです。

このDashboardは新しい正本ではありません。正本は既存システムに残します。

- タスク・期限・担当: Asana
- 正式資料: Google Drive
- 会話・経緯: Slack / Gmail
- 技術変更: GitHub
- 過去資料発掘: NakadachiArchiveAI

Dashboardの `dashboard/data/dashboard.json` は表示用の捨てられるRead Modelです。

## v0.1 sections

1. 今日のCEO判断事項
2. 進行中案件
3. 営業
4. 助成金
5. 広報・ブランド
6. リスク・停滞

上部には以下の4指標だけを表示します。

- 判断待ち
- 期限超過
- 機会
- 要注意

## Run locally

リポジトリルートで:

```bash
python3 scripts/build_ceo_dashboard.py
python3 -m http.server 8765 -d dashboard
```

ブラウザで `http://localhost:8765/` を開きます。

## Design rules

- Dashboard自体に正式情報を手入力して蓄積しない
- 保存先やDBを追加しない
- 数字を増やしすぎず、CEOが行動すべき異常・機会を優先する
- AI Agentごとの別Dashboardを作らない
- 将来のSource Adapterは既存システムから読み取り、同じ表示JSONへ出力する
- 外部送信、契約、支払、押印などの実行機能はDashboardに持たせない

## Next adapters (v0.2 candidates)

優先度順:

1. Asana: 期限超過、担当、進行案件
2. Gmail: 重要返信待ち、営業フォロー
3. GitHub: CI / PR / 技術ブロッカー
4. Slack: 明示的な依頼・判断待ち
5. 助成金 / 外部機会: 公募・締切の読み取り

v0.2でも新規DBは作らず、各Source Adapterが取得した情報を一時的に同じRead Modelへ正規化します。
