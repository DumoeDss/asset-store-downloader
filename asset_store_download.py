import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

import requests

from i18n import LANG_MAP, LANG_PROMPT, set_lang, t


GRAPHQL_URL = "https://assetstore.unity.com/api/graphql/batch"
DOWNLOAD_URL = "https://assetstore.unity.com/api/downloads"

COMMON_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7,zh-CN;q=0.6,ja;q=0.5",
    "origin": "https://assetstore.unity.com",
    "referer": "https://assetstore.unity.com/",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "dnt": "1",
    "x-requested-with": "XMLHttpRequest",
    "x-source": "storefront",
}

SEARCH_QUERY = """query SearchMyAssets($page: Int, $pageSize: Int, $q: [String], $tagging: [String!], $assignFrom: [String!], $ids: [String!], $sortBy: Int, $reverse: Boolean, $other: String) {
  searchMyAssets(page: $page, pageSize: $pageSize, q: $q, tagging: $tagging, assignFrom: $assignFrom, ids: $ids, sortBy: $sortBy, reverse: $reverse, other: $other) {
    results {
      id
      orderId
      grantTime
      tagging
      assignFrom
      product {
        id
        productId
        itemId
        name
        mainImage {
          icon75
          icon
          __typename
        }
        publisher {
          id
          name
          __typename
        }
        publishNotes
        state
        currentVersion {
          name
          publishedDate
          __typename
        }
        downloadSize
        __typename
      }
      __typename
    }
    organizations
    total
    category {
      name
      count
      __typename
    }
    publisherSuggest {
      name
      count
      __typename
    }
    __typename
  }
}
"""

PRODUCT_QUERY = """query Product($id: ID!) {
  product(id: $id) {
    ...product
    packageInListHotness
    reviews(rows: 2, sortBy: "rating") {
      ...reviews
      __typename
    }
    __typename
  }
}

fragment product on Product {
  id
  productId
  itemId
  slug
  name
  description
  aiDescription
  elevatorPitch
  keyFeatures
  compatibilityInfo
  customLicense
  rating {
    average
    count
    __typename
  }
  currentVersion {
    id
    name
    publishedDate
    __typename
  }
  reviewCount
  downloadSize
  assetCount
  publisher {
    id
    name
    url
    supportUrl
    supportEmail
    gaAccount
    gaPrefix
    __typename
  }
  userOverview {
    lastDownloadAt: last_downloaded_at
    __typename
  }
  mainImage {
    big
    facebook
    small
    icon
    icon75
    __typename
  }
  originalPrice {
    itemId
    originalPrice
    finalPrice
    isFree
    discount {
      save
      percentage
      type
      saleType
      __typename
    }
    currency
    entitlementType
    __typename
  }
  images {
    type
    imageUrl
    thumbnailUrl
    __typename
  }
  category {
    id
    name
    slug
    longName
    __typename
  }
  firstPublishedDate
  publishNotes
  supportedUnityVersions
  state
  overlay
  overlayText
  plusProSale
  licenseText
  vspProperties {
    ... on ExternalVSPProduct {
      externalLink
      __typename
    }
    __typename
  }
  __typename
}

fragment reviews on Reviews {
  count
  canRate: can_rate
  canReply: can_reply
  canComment: can_comment
  hasCommented: has_commented
  totalEntries: total_entries
  lastPage: last_page
  comments {
    id
    date
    editable
    rating
    user {
      id
      name
      profileUrl
      avatar
      __typename
    }
    isHelpful: is_helpful {
      count
      score
      __typename
    }
    subject
    version
    full
    is_complimentary
    vote
    replies {
      id
      editable
      date
      version
      full
      user {
        id
        name
        profileUrl
        avatar
        __typename
      }
      isHelpful: is_helpful {
        count
        score
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""


# ──────────────────── Utility functions ────────────────────


def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_asset_ids(path="asset_ids.txt"):
    ids = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    return ids


def extract_csrf(cookie_str):
    match = re.search(r"_csrf=([^;]+)", cookie_str)
    return match.group(1) if match else ""


def make_graphql_headers(config, operations):
    csrf = extract_csrf(config["cookie"])
    return {
        **COMMON_HEADERS,
        "content-type": "application/json;charset=UTF-8",
        "cookie": config["cookie"],
        "x-csrf-token": csrf,
        "operations": operations,
    }


# ──────────────────── Fetch list & details (write per page) ────────────────────


def request_with_retry(method, url, retry, **kwargs):
    """HTTP request with retry on 5xx / timeout / connection errors."""
    for attempt in range(1, retry + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code >= 500 and attempt < retry:
                wait = 2**attempt
                print(t("server_error").format(resp.status_code, wait, attempt))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retry:
                wait = 2**attempt
                print(t("network_error").format(wait, attempt, e))
                time.sleep(wait)
            else:
                raise
    return resp


def fetch_asset_list_page(config, page, page_size=100):
    headers = make_graphql_headers(config, "SearchMyAssets")
    payload = [
        {
            "query": SEARCH_QUERY,
            "variables": {
                "page": page,
                "pageSize": page_size,
                "q": [],
                "tagging": [],
                "ids": [],
                "assignFrom": [],
                "sortBy": 7,
            },
            "operationName": "SearchMyAssets",
        }
    ]
    retry = config.get("retry", 3)
    resp = request_with_retry(
        requests.post,
        GRAPHQL_URL,
        retry,
        headers=headers,
        json=payload,
        timeout=config.get("timeout", 60),
    )
    return resp.json()


def fetch_product_details(config, product_ids):
    if not product_ids:
        return []
    operations = ",".join(["Product"] * len(product_ids))
    headers = make_graphql_headers(config, operations)
    payload = [
        {
            "query": PRODUCT_QUERY,
            "variables": {"id": pid},
            "operationName": "Product",
        }
        for pid in product_ids
    ]
    retry = config.get("retry", 3)
    resp = request_with_retry(
        requests.post,
        GRAPHQL_URL,
        retry,
        headers=headers,
        json=payload,
        timeout=config.get("timeout", 120),
    )
    return resp.json()


def load_existing_list(list_path="asset_list.jsonl"):
    """Load existing list JSONL, return {page: data} dict."""
    pages = {}
    try:
        with open(list_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pages[obj["page"]] = obj
    except FileNotFoundError:
        pass
    return pages


def load_existing_detail_ids(info_path="asset_info.jsonl"):
    """Load existing detail JSONL, return set of fetched product IDs."""
    ids = set()
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pid = str(obj.get("id", ""))
                if pid:
                    ids.add(pid)
    except FileNotFoundError:
        pass
    return ids


def append_list_page(page_num, page_data, f_list):
    """Append one page of searchMyAssets data (with page field) to JSONL."""
    search_data = page_data[0]["data"]["searchMyAssets"]
    record = {**search_data, "page": page_num}
    f_list.write(json.dumps(record, ensure_ascii=False) + "\n")
    f_list.flush()


def append_detail_batch(details, f_info, f_ids, existing_ids):
    """Append a batch of detail results to JSONL and IDs file, return count written."""
    count = 0
    for item in details:
        product = item.get("data", {}).get("product")
        if not product:
            continue
        f_info.write(json.dumps(product, ensure_ascii=False) + "\n")
        pid = str(product["id"])
        if pid not in existing_ids:
            f_ids.write(pid + "\n")
            existing_ids.add(pid)
        count += 1
    f_info.flush()
    f_ids.flush()
    return count


def extract_product_ids_from_list(existing_pages):
    """Extract all product IDs from existing list data (preserving order)."""
    seen = set()
    result = []
    for page_num in sorted(existing_pages.keys()):
        for item in existing_pages[page_num].get("results", []):
            pid = str(item["product"]["id"])
            if pid not in seen:
                seen.add(pid)
                result.append(pid)
    return result


# ──────────────────── Download ────────────────────


def load_info_map(info_path="asset_info.jsonl"):
    """Load {product_id: {name, size}} mapping from asset_info.jsonl."""
    info_map = {}
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                product = json.loads(line)
                pid = str(product.get("id", ""))
                if pid:
                    info_map[pid] = {
                        "name": product.get("name", ""),
                        "size": int(product.get("downloadSize") or 0),
                    }
    except FileNotFoundError:
        pass
    return info_map


def format_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def format_eta(seconds):
    if seconds < 0 or seconds > 86400:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# Thread-safe lock for progress printing
_print_lock = threading.Lock()


def print_progress(asset_id, filename, downloaded, total_size, speed, finished=False):
    if total_size and total_size > 0:
        pct = min(downloaded / total_size * 100, 100)
        bar_len = 25
        filled = int(bar_len * downloaded / total_size)
        bar = "█" * filled + "░" * (bar_len - filled)
        eta = format_eta((total_size - downloaded) / speed) if speed > 0 else "--:--"
        status = (
            f"  [{asset_id}] {filename}\n"
            f"    {bar} {pct:5.1f}%  {format_size(downloaded)}/{format_size(total_size)}"
            f"  {format_size(speed)}/s  ETA {eta}"
        )
    else:
        status = (
            f"  [{asset_id}] {filename}\n"
            f"    {t('downloaded_no_total').format(format_size(downloaded), format_size(speed))}"
        )
    with _print_lock:
        if finished:
            print(status)
        else:
            print(status, end="\r\033[A\r", flush=True)


def parse_filename(response, asset_id):
    cd = response.headers.get("content-disposition", "")
    match = re.search(r'filename="(.+?)"', cd)
    if match:
        return unquote(match.group(1))
    match = re.search(r"filename\*=UTF-8''(.+)", cd)
    if match:
        return unquote(match.group(1))
    return f"{asset_id}.unitypackage"


def download_asset(asset_id, config, download_dir, total_size=0):
    url = f"{DOWNLOAD_URL}/{asset_id}"
    headers = {
        **COMMON_HEADERS,
        "accept": "*/*",
        "cookie": config["cookie"],
        "accept-encoding": "gzip, deflate, br, zstd",
    }
    for key in ["content-type", "origin", "x-requested-with", "x-source", "dnt"]:
        headers.pop(key, None)

    timeout = config.get("timeout", 300)
    retry = config.get("retry", 3)
    cache_dir = download_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    meta_path = cache_dir / f"{asset_id}.meta"

    # Check cached meta first — skip download without any network request
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cached_filename = meta.get("filename", "")
        if cached_filename:
            filepath = download_dir / cached_filename
            if filepath.exists():
                return asset_id, True, t("exists_skip").format(cached_filename)

    for attempt in range(1, retry + 1):
        try:
            tmp_path = None
            resumed_bytes = 0
            req_headers = dict(headers)

            # Check for partially downloaded .tmp file via cached meta
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                cached_filename = meta.get("filename", "")
                if cached_filename:
                    tmp_path = (download_dir / cached_filename).with_suffix(
                        (download_dir / cached_filename).suffix + ".tmp"
                    )
                    if tmp_path.exists():
                        resumed_bytes = tmp_path.stat().st_size

            if resumed_bytes > 0:
                req_headers["Range"] = f"bytes={resumed_bytes}-"

            resp = requests.get(url, headers=req_headers, stream=True, timeout=timeout)

            if resp.status_code == 401:
                return asset_id, False, t("cookie_expired")
            if resp.status_code == 403:
                return asset_id, False, t("no_permission")
            if resp.status_code == 404:
                return asset_id, False, t("not_found")

            # 416 Range Not Satisfiable — file already fully downloaded
            if resp.status_code == 416:
                filename = parse_filename(resp, asset_id)
                filepath = download_dir / filename
                if tmp_path and tmp_path.exists():
                    tmp_path.rename(filepath)
                    return asset_id, True, t("resume_full").format(filename)

            resp.raise_for_status()

            filename = parse_filename(resp, asset_id)
            filepath = download_dir / filename

            # Save filename mapping to cache for resume / skip support
            meta_path.write_text(
                json.dumps({"filename": filename}, ensure_ascii=False),
                encoding="utf-8",
            )

            if filepath.exists():
                return asset_id, True, t("exists_skip").format(filename)

            tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")

            # Check if this is a resumed (partial content) response
            is_resumed = resp.status_code == 206
            if is_resumed:
                mode = "ab"
            else:
                # Server doesn't support Range or returned full content, start from scratch
                resumed_bytes = 0
                mode = "wb"

            # Determine total size: prefer known total_size, fallback to Content-Range/Content-Length
            effective_total = total_size
            if not effective_total:
                content_range = resp.headers.get("Content-Range", "")
                if content_range:
                    # Content-Range: bytes 1000-9999/10000
                    m = re.search(r"/(\d+)", content_range)
                    if m:
                        effective_total = int(m.group(1))
                if not effective_total:
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        effective_total = int(cl) + resumed_bytes

            downloaded = resumed_bytes
            start_time = time.time()

            with open(tmp_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
                    print_progress(
                        asset_id, filename, downloaded, effective_total, speed
                    )

            # Final progress
            elapsed = time.time() - start_time
            speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
            print_progress(
                asset_id, filename, downloaded, effective_total, speed, finished=True
            )

            tmp_path.rename(filepath)

            resumed_tag = t("resumed") if is_resumed else ""
            return (
                asset_id,
                True,
                t("done").format(resumed_tag, filename, format_size(downloaded)),
            )

        except requests.RequestException as e:
            if attempt < retry:
                wait = 2**attempt
                with _print_lock:
                    print(t("attempt_fail").format(asset_id, attempt, wait, e))
                time.sleep(wait)
            else:
                return asset_id, False, t("fail_retry").format(retry, e)

    return asset_id, False, t("unknown_error")


def _build_local_file_index(download_dir):
    """Build {lowercase_filename: Path} index of all .unitypackage files in download_dir."""
    index = {}
    for f in download_dir.glob("*.unitypackage"):
        index[f.name.lower()] = f
    return index


def _pre_check_downloads(asset_ids, download_dir, cache_dir, info_map):
    """Pre-check which assets are already downloaded locally, without any network request.

    For each asset_id:
      1. If .meta exists and the file exists (size verified) → skip
      2. If no .meta, try to match a local file by product name from info_map → create .meta → skip
      3. Otherwise → needs download

    Returns (skipped: list[(id, filename)], pending: list[id]).
    """
    local_files = _build_local_file_index(download_dir)
    skipped = []
    pending = []

    for aid in asset_ids:
        meta_path = cache_dir / f"{aid}.meta"
        info = info_map.get(aid, {})

        # 1) Check existing .meta cache — .meta + file exists = already downloaded
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_filename = meta.get("filename", "")
            if cached_filename:
                filepath = download_dir / cached_filename
                if filepath.exists():
                    skipped.append((aid, cached_filename))
                    continue

        # 2) No .meta — try to match local file by product name
        product_name = info.get("name", "")
        if product_name:
            name_lower = product_name.lower()
            for fname_lower, fpath in local_files.items():
                if name_lower in fname_lower:
                    # Create .meta cache for future runs
                    meta_path.write_text(
                        json.dumps({"filename": fpath.name}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    skipped.append((aid, fpath.name))
                    break
            else:
                pending.append(aid)
        else:
            pending.append(aid)

    return skipped, pending


def run_downloads(config, ids_path="asset_ids.txt"):
    asset_ids = load_asset_ids(ids_path)
    if not asset_ids:
        print(t("no_valid_ids"))
        return

    download_dir = Path(config.get("download_dir", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)
    max_workers = config.get("max_workers", 3)

    # Migrate old .meta files from download_dir to download_dir/.cache/
    cache_dir = download_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    for old_meta in download_dir.glob(".*.meta"):
        new_name = old_meta.name[1:]  # strip leading dot
        new_path = cache_dir / new_name
        if not new_path.exists():
            old_meta.rename(new_path)
        else:
            old_meta.unlink()

    info_map = load_info_map()

    # Pre-check: skip already downloaded files without any network request
    skipped, pending_ids = _pre_check_downloads(
        asset_ids, download_dir, cache_dir, info_map
    )

    total_known_size = sum(info_map.get(aid, {}).get("size", 0) for aid in asset_ids)
    print(t("total_assets_threads").format(len(asset_ids), max_workers))
    if total_known_size > 0:
        print(t("known_size").format(len(info_map), format_size(total_known_size)))
    print(t("download_dir").format(download_dir.resolve()))

    if skipped:
        print(t("skipped_local").format(len(skipped)))
    if not pending_ids:
        print(t("all_skipped"))
        return

    print(t("pending_download").format(len(pending_ids)))

    success, failed = len(skipped), 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                download_asset,
                aid,
                config,
                download_dir,
                info_map.get(aid, {}).get("size", 0),
            ): aid
            for aid in pending_ids
        }
        for future in as_completed(futures):
            asset_id, ok, msg = future.result()
            status = "OK" if ok else "FAIL"
            with _print_lock:
                print(f"  [{status}] {asset_id} - {msg}")
            if ok:
                success += 1
            else:
                failed += 1

    print(t("download_done").format(success, failed))


# ──────────────────── Main flow ────────────────────


def _fetch_list_page_task(config, page, page_size):
    """Thread pool task: fetch a single list page."""
    page_data = fetch_asset_list_page(config, page, page_size)
    search_data = page_data[0]["data"]["searchMyAssets"]
    return page, {**search_data, "page": page}


def _fetch_detail_batch_task(config, batch, batch_num):
    """Thread pool task: fetch a batch of product details."""
    details = fetch_product_details(config, batch)
    products = []
    for item in details:
        product = item.get("data", {}).get("product")
        if product:
            products.append(product)
    return batch_num, products


def run_fetch_list(config, detail_batch_size=100):
    page_size = 100
    list_path = "asset_list.jsonl"
    info_path = "asset_info.jsonl"
    ids_path = "asset_ids.txt"
    max_workers = config.get("max_workers", 3)
    _file_lock = threading.Lock()

    # ── Phase 1 ──
    print("=" * 50)
    print(t("phase1"))
    print("=" * 50)

    existing_pages = load_existing_list(list_path)
    if existing_pages:
        print(t("existing_pages").format(len(existing_pages)))

    if 0 in existing_pages:
        total = existing_pages[0]["total"]
    else:
        print(t("fetching_page0"))
        first_page = fetch_asset_list_page(config, 0, page_size)
        total = first_page[0]["data"]["searchMyAssets"]["total"]
        record = {**first_page[0]["data"]["searchMyAssets"], "page": 0}
        with open(list_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        existing_pages[0] = record
        print(t("page_written"))

    total_pages = math.ceil(total / page_size)
    missing_pages = [p for p in range(total_pages) if p not in existing_pages]
    print(t("total_pages_missing").format(total, total_pages, len(missing_pages)))

    if missing_pages:
        with open(list_path, "a", encoding="utf-8") as f:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_fetch_list_page_task, config, page, page_size): page
                    for page in missing_pages
                }
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        page_num, record = future.result()
                        with _file_lock:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                            f.flush()
                            existing_pages[page_num] = record
                        with _print_lock:
                            print(t("page_ok").format(
                                page_num, total_pages - 1, len(record.get("results", []))
                            ))
                    except requests.RequestException as e:
                        with _print_lock:
                            print(t("page_fail").format(page, e))

        still_missing = [p for p in range(total_pages) if p not in existing_pages]
        if still_missing:
            print(t("still_missing").format(len(still_missing), still_missing))
            print(t("rerun"))
            return False

    print(t("list_complete").format(len(existing_pages)))

    # ── Phase 2 ──
    print("\n" + "=" * 50)
    print(t("phase2"))
    print("=" * 50)

    all_product_ids = extract_product_ids_from_list(existing_pages)
    already_fetched = load_existing_detail_ids(info_path)
    pending_ids = [pid for pid in all_product_ids if pid not in already_fetched]

    print(t("detail_summary").format(
        len(all_product_ids), len(already_fetched), len(pending_ids)
    ))

    if not pending_ids:
        print(t("detail_done"))
        return True

    existing_ids_in_file = set()
    try:
        with open(ids_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing_ids_in_file.add(line)
    except FileNotFoundError:
        pass
    existing_ids_in_file.update(already_fetched)

    batches = []
    for i in range(0, len(pending_ids), detail_batch_size):
        batches.append(pending_ids[i : i + detail_batch_size])
    total_batches = len(batches)

    info_count = 0

    with (
        open(info_path, "a", encoding="utf-8") as f_info,
        open(ids_path, "a", encoding="utf-8") as f_ids,
    ):
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_detail_batch_task, config, batch, idx + 1): idx + 1
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_num = futures[future]
                try:
                    _, products = future.result()
                    with _file_lock:
                        for product in products:
                            f_info.write(json.dumps(product, ensure_ascii=False) + "\n")
                            pid = str(product["id"])
                            if pid not in existing_ids_in_file:
                                f_ids.write(pid + "\n")
                                existing_ids_in_file.add(pid)
                        f_info.flush()
                        f_ids.flush()
                    info_count += len(products)
                    with _print_lock:
                        print(t("batch_ok").format(batch_num, total_batches, len(products)))
                except requests.RequestException as e:
                    with _print_lock:
                        print(t("batch_fail").format(batch_num, total_batches, e))

    final_detail_count = len(already_fetched) + info_count
    print(t("info_result").format(info_path, info_count, final_detail_count))
    print(t("ids_result").format(ids_path, len(existing_ids_in_file)))
    return True


def main():
    lang_choice = input(LANG_PROMPT).strip()
    set_lang(LANG_MAP.get(lang_choice, "en"))

    config = load_config()

    print(t("title"))
    print("=" * 40)
    print(t("menu_1"))
    print(t("menu_2"))
    print(t("menu_3"))
    print("=" * 40)

    choice = input(t("choose")).strip()

    if choice == "1":
        run_fetch_list(config)
    elif choice == "2":
        run_downloads(config)
    elif choice == "3":
        ok = run_fetch_list(config)
        if ok:
            print("\n")
            run_downloads(config)
    else:
        print(t("invalid_choice"))


if __name__ == "__main__":
    main()
