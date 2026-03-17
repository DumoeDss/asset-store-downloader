# Unity Asset Store 一括ダウンロードツール

Unity Asset Store で購入した全アセットを一括ダウンロードします。

## 機能

- **アセットリスト取得** - GraphQL API でページネーション取得（1ページ100件）
- **製品詳細取得** - 名前、サイズ、バージョン、カテゴリなどの完全な情報
- **一括ダウンロード** - スレッドプールによる `.unitypackage` ファイルの並行ダウンロード
- **レジューム対応** - 中断後に再実行すると前回の位置から自動的に再開
- **ダウンロード進捗** - リアルタイムのプログレスバー、速度、残り時間を表示
- **差分取得** - 再起動時に取得済みのページと詳細を自動スキップ
- **自動リトライ** - 5xxエラー、タイムアウト、接続エラーを指数バックオフでリトライ

## 必要環境

```bash
pip install requests
```

## セットアップ

1. 設定ファイルのサンプルをコピー：
   ```bash
   cp config.json.example config.json
   ```
2. ブラウザで [Unity Asset Store](https://assetstore.unity.com) にログイン
3. デベロッパーツール（F12）> Network タブ > 任意のリクエストから `Cookie` ヘッダーをコピー
4. `config.json` の `cookie` フィールドに貼り付け：
![](pics/cookie.png)
```json
{
  "cookie": "ここにCookie文字列を貼り付け",
  "download_dir": "./downloads",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| フィールド | 説明 |
|---|---|
| `cookie` | ブラウザからコピーした完全な Cookie 文字列 |
| `download_dir` | ダウンロード保存ディレクトリ |
| `max_workers` | スレッドプール並行数（推奨: 3、大きすぎるとレート制限の可能性あり） |
| `retry` | リクエスト失敗時のリトライ回数 |
| `timeout` | リクエストタイムアウト（秒） |

## 使い方

```bash
python asset_store_download.py
```

起動するとメニューが表示されます：

```
1. アセットリスト取得      - リスト + 詳細を取得し、JSONLファイルに書き込み
2. ダウンロード開始        - asset_ids.txt から .unitypackage をダウンロード
3. リスト取得 & ダウンロード - 上記を順番に実行
```

## 出力ファイル

| ファイル | 説明 |
|---|---|
| `asset_list.jsonl` | 1行1JSON、各ページの `searchMyAssets` データ（`page` フィールド付き） |
| `asset_info.jsonl` | 1行1JSON、製品詳細オブジェクト |
| `asset_ids.txt` | 1行1製品ID、ダウンロードの入力として使用 |
| `downloads/` | ダウンロードされた `.unitypackage` ファイル |

## レジューム動作

- **リスト取得**：`asset_list.jsonl` を読み込み、欠落ページを検出して取得
- **詳細取得**：`asset_info.jsonl` を読み込み、取得済み製品IDをスキップ
- **ファイルダウンロード**：`.tmp` ファイルを検出し、`Range` ヘッダーで前回のバイト位置から再開
