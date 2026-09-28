# syfu-market-dashboard

SyFu Marketplace の成約履歴 (https://manekineko.syfu.io/activity) を毎日取得して保存し、相場ダッシュボードを生成します。

- `scraper/scrape.py` — 取得。5秒間隔・直列・エラー時バックオフ。`daily` は既知の取引に当たった時点で停止（通常1リクエスト）。
- `scraper/build_dashboard.py` — `data/trades.jsonl` を埋め込んだ `dist/index.html` を生成。
- `scraper/build_prices.py` — 種類ごとの相場（直近7日の中央値）と tokenId→種類 の対応を `dist/prices.json` に出力。資産ノートの保有資産ページが評価額に使う。
- `.github/workflows/scrape.yml` — 毎日 03:47 JST に実行し、データをコミット。
