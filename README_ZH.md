# Unity Asset Store 批量下载工具

批量下载你在 Unity Asset Store 购买的所有资源。

## 功能特性

- **获取资源列表** - 通过 GraphQL API 分页获取（每页 100 条）
- **获取产品详情** - 名称、大小、版本、分类等完整信息
- **批量下载** - 线程池并发下载 `.unitypackage` 文件
- **断点续传** - 中断后重新运行自动从上次位置继续下载
- **下载进度** - 实时显示进度条、速度、剩余时间
- **增量获取** - 重启后自动跳过已获取的页面和详情
- **自动重试** - 5xx 错误、超时、连接错误自动指数退避重试

## 环境要求

```bash
pip install requests
```

## 配置

1. 复制示例配置文件：
   ```bash
   cp config.json.example config.json
   ```
2. 在浏览器中登录 [Unity Asset Store](https://assetstore.unity.com)
3. 打开开发者工具（F12）> Network 标签 > 复制任意请求的 `Cookie` 请求头
4. 将 Cookie 粘贴到 `config.json` 的 `cookie` 字段：
![](pics/cookie.png)
```json
{
  "cookie": "在此粘贴完整的cookie字符串",
  "download_dir": "./downloads",
  "max_workers": 3,
  "retry": 3,
  "timeout": 300
}
```

| 字段 | 说明 |
|---|---|
| `cookie` | 浏览器复制的完整 Cookie 字符串 |
| `download_dir` | 下载保存目录 |
| `max_workers` | 线程池并发数（建议 3，过大可能被限流） |
| `retry` | 请求失败重试次数 |
| `timeout` | 请求超时时间（秒） |

## 使用方法

```bash
python asset_store_download.py
```

启动后显示菜单：

```
1. 获取资源列表      - 获取列表 + 详情，写入 JSONL 文件
2. 开始下载          - 根据 asset_ids.txt 下载 .unitypackage 文件
3. 获取列表并下载    - 依次执行以上两步
```

## 输出文件

| 文件 | 说明 |
|---|---|
| `asset_list.jsonl` | 每行一条 JSON，每页的 `searchMyAssets` 数据，含 `page` 字段 |
| `asset_info.jsonl` | 每行一条 JSON，产品详情对象 |
| `asset_ids.txt` | 每行一个产品 ID，作为下载输入 |
| `downloads/` | 下载的 `.unitypackage` 文件 |

## 断点续传机制

- **列表获取**：读取 `asset_list.jsonl`，检测缺失页码，仅获取缺失页
- **详情获取**：读取 `asset_info.jsonl`，跳过已有产品 ID
- **文件下载**：检测 `.tmp` 文件，发送 `Range` 请求头从上次字节位置继续
