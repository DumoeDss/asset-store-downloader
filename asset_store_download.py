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


# ──────────────────── 工具函数 ────────────────────


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


# ──────────────────── 获取列表 & 详情（逐页写入） ────────────────────


def request_with_retry(method, url, retry, **kwargs):
    """带重试的 HTTP 请求，遇到 5xx / 超时 / 连接错误自动重试"""
    for attempt in range(1, retry + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code >= 500 and attempt < retry:
                wait = 2**attempt
                print(
                    f"    服务器错误({resp.status_code})，{wait}秒后第{attempt}次重试..."
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retry:
                wait = 2**attempt
                print(f"    网络错误，{wait}秒后第{attempt}次重试: {e}")
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
    """加载已有的 list jsonl，返回 {page: data} 字典"""
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
    """加载已有的详情 jsonl，返回已获取的 product id 集合"""
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
    """将一页的 searchMyAssets 数据（含 page 字段）追加写入 jsonl"""
    search_data = page_data[0]["data"]["searchMyAssets"]
    record = {**search_data, "page": page_num}
    f_list.write(json.dumps(record, ensure_ascii=False) + "\n")
    f_list.flush()


def append_detail_batch(details, f_info, f_ids, existing_ids):
    """将一批详情结果追加写入 jsonl 和 ids 文件，返回写入条数"""
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
    """从已有的 list 数据中提取所有 product id（保持顺序）"""
    seen = set()
    result = []
    for page_num in sorted(existing_pages.keys()):
        for item in existing_pages[page_num].get("results", []):
            pid = str(item["product"]["id"])
            if pid not in seen:
                seen.add(pid)
                result.append(pid)
    return result


# ──────────────────── 下载 ────────────────────


def load_size_map(info_path="asset_info.jsonl"):
    """从 asset_info.jsonl 加载 {product_id: downloadSize} 映射"""
    size_map = {}
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                product = json.loads(line)
                pid = str(product.get("id", ""))
                ds = product.get("downloadSize")
                if pid and ds:
                    size_map[pid] = int(ds)
    except FileNotFoundError:
        pass
    return size_map


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


# 用于多线程安全打印进度
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
            f"    已下载 {format_size(downloaded)}  {format_size(speed)}/s"
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

    for attempt in range(1, retry + 1):
        try:
            # 先发 HEAD/无Range 请求获取文件名，再决定断点续传
            # 但为了减少请求，直接带 Range 发请求
            # 如果 tmp 文件存在，尝试断点续传
            tmp_path = None
            resumed_bytes = 0

            # 先尝试不带 Range 请求来获取文件名（用已知的 tmp 文件匹配）
            # 查找已有的 tmp 文件
            existing_tmps = list(download_dir.glob(f"*.unitypackage.tmp"))
            # 也需要检查完成文件
            # 先发请求获取文件名
            req_headers = dict(headers)

            # 检查是否有该 asset_id 的 tmp 文件（通过元数据文件记录映射）
            meta_path = download_dir / f".{asset_id}.meta"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                cached_filename = meta.get("filename", "")
                if cached_filename:
                    filepath = download_dir / cached_filename
                    if filepath.exists():
                        return asset_id, True, f"已存在，跳过: {cached_filename}"
                    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
                    if tmp_path.exists():
                        resumed_bytes = tmp_path.stat().st_size

            if resumed_bytes > 0:
                req_headers["Range"] = f"bytes={resumed_bytes}-"

            resp = requests.get(url, headers=req_headers, stream=True, timeout=timeout)

            if resp.status_code == 401:
                return asset_id, False, "Cookie已过期或无效(401)"
            if resp.status_code == 403:
                return asset_id, False, "无权下载此资源(403)"
            if resp.status_code == 404:
                return asset_id, False, "资源不存在(404)"

            # 416 Range Not Satisfiable — 文件已完整下载
            if resp.status_code == 416:
                filename = parse_filename(resp, asset_id)
                filepath = download_dir / filename
                if tmp_path and tmp_path.exists():
                    tmp_path.rename(filepath)
                    meta_path.unlink(missing_ok=True)
                    return asset_id, True, f"续传完成(已满): {filename}"

            resp.raise_for_status()

            filename = parse_filename(resp, asset_id)
            filepath = download_dir / filename

            # 保存文件名映射，供断点续传使用
            meta_path = download_dir / f".{asset_id}.meta"
            meta_path.write_text(
                json.dumps({"filename": filename}, ensure_ascii=False),
                encoding="utf-8",
            )

            if filepath.exists():
                meta_path.unlink(missing_ok=True)
                return asset_id, True, f"已存在，跳过: {filename}"

            tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")

            # 判断是否为续传响应
            is_resumed = resp.status_code == 206
            if is_resumed:
                mode = "ab"
            else:
                # 服务器不支持 Range 或返回完整内容，从头开始
                resumed_bytes = 0
                mode = "wb"

            # 确定总大小: 优先用已知的 total_size，其次用 Content-Range/Content-Length
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

            # 最终进度
            elapsed = time.time() - start_time
            speed = (downloaded - resumed_bytes) / elapsed if elapsed > 0 else 0
            print_progress(
                asset_id, filename, downloaded, effective_total, speed, finished=True
            )

            tmp_path.rename(filepath)
            meta_path.unlink(missing_ok=True)

            resumed_tag = " (续传)" if is_resumed else ""
            return (
                asset_id,
                True,
                f"完成{resumed_tag}: {filename} ({format_size(downloaded)})",
            )

        except requests.RequestException as e:
            if attempt < retry:
                wait = 2**attempt
                with _print_lock:
                    print(f"  [{asset_id}] 第{attempt}次失败，{wait}秒后重试: {e}")
                time.sleep(wait)
            else:
                return asset_id, False, f"失败(重试{retry}次): {e}"

    return asset_id, False, "未知错误"


def run_downloads(config, ids_path="asset_ids.txt"):
    asset_ids = load_asset_ids(ids_path)
    if not asset_ids:
        print("asset_ids.txt 中没有有效的ID")
        return

    download_dir = Path(config.get("download_dir", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)
    max_workers = config.get("max_workers", 3)

    size_map = load_size_map()
    known = sum(1 for aid in asset_ids if aid in size_map)
    total_known_size = sum(size_map.get(aid, 0) for aid in asset_ids)

    print(f"\n共 {len(asset_ids)} 个资源，线程数: {max_workers}")
    if known > 0:
        print(f"已知大小: {known} 个，总计 {format_size(total_known_size)}")
    print(f"下载目录: {download_dir.resolve()}\n")

    success, failed = 0, 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                download_asset, aid, config, download_dir, size_map.get(aid, 0)
            ): aid
            for aid in asset_ids
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

    print(f"\n下载完成: 成功 {success}, 失败 {failed}")


# ──────────────────── 主流程 ────────────────────


def _fetch_list_page_task(config, page, page_size):
    """线程池任务: 获取单页列表"""
    page_data = fetch_asset_list_page(config, page, page_size)
    search_data = page_data[0]["data"]["searchMyAssets"]
    return page, {**search_data, "page": page}


def _fetch_detail_batch_task(config, batch, batch_num):
    """线程池任务: 获取一批详情"""
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

    # ── 阶段1: 补全列表（并发） ──
    print("=" * 50)
    print("阶段1: 获取资源列表")
    print("=" * 50)

    existing_pages = load_existing_list(list_path)
    if existing_pages:
        print(f"已有 {len(existing_pages)} 页列表数据")

    if 0 in existing_pages:
        total = existing_pages[0]["total"]
    else:
        print("正在获取第 0 页以确定总数...")
        first_page = fetch_asset_list_page(config, 0, page_size)
        total = first_page[0]["data"]["searchMyAssets"]["total"]
        record = {**first_page[0]["data"]["searchMyAssets"], "page": 0}
        with open(list_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        existing_pages[0] = record
        print(f"  第 0 页已写入")

    total_pages = math.ceil(total / page_size)
    missing_pages = [p for p in range(total_pages) if p not in existing_pages]
    print(f"共 {total} 个资源，{total_pages} 页，缺失 {len(missing_pages)} 页\n")

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
                            print(
                                f"  第 {page_num}/{total_pages - 1} 页已写入 ({len(record.get('results', []))} 条)"
                            )
                    except requests.RequestException as e:
                        with _print_lock:
                            print(f"  第 {page} 页获取失败: {e}")

        still_missing = [p for p in range(total_pages) if p not in existing_pages]
        if still_missing:
            print(f"\n仍有 {len(still_missing)} 页缺失: {still_missing}")
            print("请重新运行以补全列表")
            return False

    print(f"列表完整: {len(existing_pages)} 页")

    # ── 阶段2: 获取详情（并发，跳过已有） ──
    print("\n" + "=" * 50)
    print("阶段2: 获取资源详情")
    print("=" * 50)

    all_product_ids = extract_product_ids_from_list(existing_pages)
    already_fetched = load_existing_detail_ids(info_path)
    pending_ids = [pid for pid in all_product_ids if pid not in already_fetched]

    print(
        f"共 {len(all_product_ids)} 个产品，已有详情 {len(already_fetched)} 个，待获取 {len(pending_ids)} 个\n"
    )

    if not pending_ids:
        print("详情数据已完整，无需获取")
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

    # 分批
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
                        print(
                            f"  批次 {batch_num}/{total_batches} 写入 {len(products)} 条详情"
                        )
                except requests.RequestException as e:
                    with _print_lock:
                        print(f"  批次 {batch_num}/{total_batches} 获取失败: {e}")

    final_detail_count = len(already_fetched) + info_count
    print(f"\n详情数据: {info_path} (本次 +{info_count}，共 {final_detail_count} 条)")
    print(f"ID文件: {ids_path} (共 {len(existing_ids_in_file)} 个)")
    return True


def main():
    config = load_config()

    print("Unity Asset Store 批量下载工具")
    print("=" * 40)
    print("  1. 获取资源列表")
    print("  2. 开始下载")
    print("  3. 获取列表并下载")
    print("=" * 40)

    choice = input("请选择操作 [1/2/3]: ").strip()

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
        print("无效选择")


if __name__ == "__main__":
    main()
