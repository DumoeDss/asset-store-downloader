# Unity Asset Store Batch Downloader

[中文](README_ZH.md) | [日本語](README_JA.md) | [한국어](README_KO.md)

Batch download all your purchased assets from Unity Asset Store.

## Features

- **Fetch asset list** via GraphQL API with pagination (100 per page)
- **Fetch product details** for each asset (name, size, version, category, etc.)
- **Batch download** `.unitypackage` files with thread pool concurrency
- **Resume support** - interrupted downloads resume from where they left off
- **Progress display** - real-time progress bar, speed, and ETA for each download
- **Incremental fetch** - restart safely; already fetched pages/details are skipped
- **Auto retry** - 5xx errors, timeouts, and connection errors retry with exponential backoff

## Requirements

```bash
pip install requests
```

## Setup

1. Copy the example config:
   ```bash
   cp config.json.example config.json
   ```
2. Log in to [Unity Asset Store](https://assetstore.unity.com) in your browser
3. Open DevTools (F12) > Network tab > copy the `Cookie` header from any request
4. Paste it into the `cookie` field of `config.json`:
![](pics/cookie.png)
```json
{
  "cookie": "your_cookie_string_here",
  "download_dir": "./downloads",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| Field | Description |
|---|---|
| `cookie` | Full cookie string from browser |
| `download_dir` | Download save directory |
| `max_workers` | Thread pool concurrency (recommended: 3) |
| `retry` | Retry count for failed requests |
| `timeout` | Request timeout in seconds |

## Usage

```bash
python asset_store_download.py
```

You will see a menu:

```
1. Fetch asset list      - Fetch list + details, write to JSONL files
2. Start download        - Download .unitypackage files from asset_ids.txt
3. Fetch list & download - Run both sequentially
```

## Output Files

| File | Description |
|---|---|
| `asset_list.jsonl` | One JSON per line, each line is a page's `searchMyAssets` data with a `page` field |
| `asset_info.jsonl` | One JSON per line, each line is a product detail object |
| `asset_ids.txt` | One product ID per line, used as download input |
| `downloads/` | Downloaded `.unitypackage` files |

## Resume Behavior

- **List fetch**: reads `asset_list.jsonl`, detects missing pages, only fetches those
- **Detail fetch**: reads `asset_info.jsonl`, skips already fetched product IDs
- **File download**: detects `.tmp` files, sends `Range` header to resume from last byte
