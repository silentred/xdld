# xdld — 下载 Twitter/X「点赞」列表里所有视频

遍历某个账号点赞过的推文，把其中**分辨率最高**的视频下载到本地。
支持分页、增量去重，所有指向 X 的请求都走本地代理。

```
xdld run                      # 走完点赞列表，下载视频
xdld run --dry-run            # 只看计划，不下载
xdld check                    # 检查代理 + 登录 + Likes 接口
xdld status                   # 看看已经下载了多少
```

---

## 1. 为什么是这个技术选型（调研结论）

| 需求 | 结论 |
| --- | --- |
| 拉取点赞列表 | X 的官方 API v2 `liked_tweets` 需要付费档位（Basic 及以上），**免费不可用** |
| 抓取网页端 GraphQL | 上游 `d60/twikit` 在 2026 年已大面积失效（`KeyError`、`404`、登录流退役）。本项目使用其维护中的分支 **`PawiX25/twifork`**（2026-10 仍在更新，`pip install twifork`，`import twikit` 完全兼容） |
| 登录 | X 已下线密码登录流（`/i/flow/login` → `/i/jf/onboarding/web`，需要 `$castle_token` / WebAuthn，**任何 HTTP 库都做不到**）。唯一可行方式：**导出浏览器 Cookie**（`auth_token` + `ct0`） |
| 下载视频 | 直接取 `video_info.variants` 里 `bitrate` 最大的 `video/mp4`（X 的 mp4 变体中码率越高分辨率越高），不走 yt-dlp，省一层依赖 |
| 去重 | SQLite 记录 `media_id`（同一条视频在任何推文里出现都只下载一次） |

> 网上没有现成的「下载点赞视频」成品工具，能复用的只有上面这些库。

---

## 2. 安装

```bash
cd /Users/jason/Desktop/projects/xdld
python3.12 -m venv .venv          # 3.12/3.13 都可以，3.14 目前对 Js2Py 不友好
.venv/bin/pip install -e .

# 可选：绕过部分 403（需要浏览器 TLS 指纹）
.venv/bin/pip install -e ".[impersonate]"

# 可选：只有 HLS 流的推文（很少见）
.venv/bin/pip install -e ".[hls]"
```

生成配置文件：

```bash
.venv/bin/xdld init     # 生成 ./xdld.toml
```

---

## 3. 导出 Cookie（唯一的登录方式）

1. 用 Chrome/Edge 正常登录 <https://x.com>
2. 安装浏览器扩展 **Get cookies.txt LOCALLY**（Chrome 商店 / Firefox 附加组件）
3. 在 x.com 页面上点开扩展 → **Export cookies** → 保存为 `cookies.txt`
4. 放到项目根目录（默认就叫 `cookies.txt`，无需改名），然后：

```bash
ls cookies.txt          # 文件名无所谓，格式是 Netscape cookies.txt 就行
```

> 没有扩展？用 `tools/cookies_via_playwright.py` 手动登录一次，工具会直接写 `cookies.txt`。

也支持这两种格式，工具会自动识别：

* **JSON 列表**（EditThisCookie / EditThisCookie 之类的扩展导出）：`[{"name":"auth_token","value":"..."}, ...]`
* **扁平 JSON**：`{"auth_token":"...","ct0":"..."}`
* **纯文本**：`auth_token=xxx; ct0=yyy`

必需的 Cookie 只有两个：`auth_token` 和 `ct0`。`xdld check` 会直接告诉你缺哪个。

---

## 4. 使用

```bash
# 体检：代理通不通、Cookie 能不能登录、Likes 接口能不能用
.venv/bin/xdld check

# 走完整个点赞列表（默认目录 ./downloads）
.venv/bin/xdld run

# 只下载自己账号的点赞；换账号
.venv/bin/xdld run --user elonmusk

# 只看前 200 条点赞，不下载
.venv/bin/xdld run --limit 200 --dry-run

# 边下边看（每页之间随机停顿 1~2.5 秒，避免触发风控）
.venv/bin/xdld run --sleep 2 5

# 续传：从某条推文开始（含这条），一直往更老的推文走
.venv/bin/xdld run --since-tweet 1234567890123456789

# 续传：从第 N 页开始（第 1 页 = 最新的那一页）
.venv/bin/xdld run --from-page 5

# 不显示实时进度行（只保留普通日志）
.venv/bin/xdld run --quiet

# 导出 aria2c 下载清单（下载完用 xdld record 登记）
.venv/bin/xdld export --limit 100
aria2c -c -x 4 -s 4 -j 4 -i downloads/aria2.txt
.venv/bin/xdld record -i downloads/aria2.txt

# 排查问题：打印 query id、user id、每步耗时，失败时打印状态码/响应体/响应头
.venv/bin/xdld check --verbose
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--proxy` | 代理地址，默认 `http://127.0.0.1:7897`（配置在 `xdld.toml` 里也行） |
| `--cookies` | Cookie 文件路径 |
| `--out` | 下载目录 |
| `--limit N` | 最多处理 N 条点赞 |
| `--dry-run` | 只打印计划 |
| `--images` | 连图片一起下载 |
| `--template` | 文件名模板，可选 `{tweet_id} {user} {index} {ext} {date} {media_id}` |
| `--since-tweet ID` | 从这条推文开始（含），往更老的推文走 |
| `--from-page N` | 从第 N 页开始（1 = 最新页） |
| `--quiet` | 关掉实时进度行 |
| `-v, --verbose` | 详细诊断（query id、user id、耗时、失败时的状态码/响应体/响应头/处理建议） |
| `--impersonate chrome124` | 浏览器 TLS 指纹，配合 `[impersonate]` 附加依赖 |

### 实时进度

运行时会不断刷新一行进度（终端上原地覆盖，重定向到文件时每几秒输出一行）：

```
1234_alice_0.mp4  2.5 MB/3.0 MB 83%  1.0 MB/s  | tweets 120  videos 34  ok 28  size 1.2 GB  elapsed 03:12
```

爬列表、等接口的间隙也会有心跳提示（`... still working (00:12 elapsed)`），所以长时间没输出也不代表卡死。

---

## 5. 去重与增量

* `xdld.db`（SQLite）记录每条媒体：`media_id`、推文 ID、本地路径、字节数、状态。
* 同一个 `media_id` 只会下载一次——即使它在多条被点赞的推文里出现。
* 再次运行 `xdld run` 时，**已下载的直接跳过**；本地文件被删了会自动重下。
* 如果 `xdld.db` 丢了但视频还在磁盘上，工具会把已有文件登记回数据库（不重新下载）。
* 点赞列表是按时间倒序的，所以连续遇到 50 条已下载的记录后就会自动收工（`stop_after_known`），不用每次都重头爬。
* 下载中断不会留下半截文件：先写 `xxx.mp4.part`，成功后再改名。
* **断点续传**：如果上次留下了 `.part` 文件，下次会用 HTTP `Range` 从断点继续（而不是从头下）；代理掉线也会自动重试 3 次，每次都接着上次的位置。视频再大也不用担心重来一遍。

> ⚠️ `downloads/`、`xdld.db`、`cookies.txt` 都在 `.gitignore` 里（它们是本机状态，不该进仓库）。**别对仓库执行 `git clean -fdx`**——那会把已下载的视频、去重数据库和 Cookie 一起删掉。想清缓存就删 `__pycache__` 和 `.pytest_cache`。

### 断点续传

三种方式，按需要选：

```bash
# 1) 直接重跑：已下载的自动跳过，爬到连续 50 条已知记录就停
.venv/bin/xdld run

# 2) 从某条推文开始（含这条），往更老的推文走
.venv/bin/xdld run --since-tweet 1234567890123456789

# 3) 从第 N 页开始（第 1 页 = 最新的一页）
.venv/bin/xdld run --from-page 5
```

说明：

* `--since-tweet` / `--from-page` 都只是**“从哪里开始”**，往后依然会一直走到列表尽头（除非配合 `--limit`）。
* X 的接口没有“从某条推文直接跳转”的能力，所以 `--since-tweet` 仍然要把前面的页翻过去（只是不下载、不产出），翻到那条为止。
* 用了 `--since-tweet` 时，“连续 N 条已下载就停”这条规则会自动失效，否则会刚开始就结束。
* 限流时可以配合 `--limit`、`--page-size`、`--sleep` 一起用。

---

## 5.5 用 aria2c 下载（导出下载清单）

不想让 xdld 自己拉视频？可以让它把点赞列表导出成 **aria2c 的下载清单**，用 aria2c 下载（多线程、断点续传、支持代理），下完再用 `xdld record` 把这些 id 登记回数据库。

```bash
# 1) 导出清单（默认写到 <下载目录>/aria2.txt）
.venv/bin/xdld export --limit 100

# 2) 用 aria2c 下载（清单里已经带好了 dir/out/代理）
aria2c -c -x 4 -s 4 -j 4 -i downloads/aria2.txt

# 3) 下完之后登记：这些 media_id 从此标记为“已下载”
.venv/bin/xdld record -i downloads/aria2.txt

# 4) 再导出一次 → 已经下过的不会再出现
.venv/bin/xdld export --limit 100
```

清单长这样（aria2c 输入文件格式：URI 一行，接下来的行是这条 URI 的选项，必须缩进）：

```
# xdld - aria2c input file
# generated: 2026-10-21 18:43:40
# files: 2
# usage: aria2c -c -x 4 -s 4 -j 4 -i <this file>
# after the download: xdld record <this file>

# xdld-meta {"tweet_id": "2112022615082832063", "media_id": "2112018771363971072", "kind": "video", "author": "EasonnLl", "created_at": "..."}
https://video.twimg.com/amplify_video/2112018771363971072/vid/avc1/1280x718/WZ_GIeXYyRqo1Tda.mp4?tag=29
  dir=/Users/me/xdld/downloads
  out=2112022615082832063_EasonnLl_0.mp4
  header=User-Agent: Mozilla/5.0 ...
  all-proxy=http://127.0.0.1:7897
  continue=true
  auto-file-renaming=false
  allow-overwrite=true
```

要点：

* **输出文件名带 tweet id**：`out=2112022615082832063_EasonnLl_0.mp4`，文件名模板可以用 `--template` 改（可用占位符：`{tweet_id} {user} {index} {ext} {date} {media_id}`）。
* **`dir=` 是绝对路径**，aria2c 会把文件放到那里；`--dir` 可以把下载目录换成别的。
* **`# xdld-meta` 注释里存了元数据**（tweet_id / media_id / 作者 / 时间），`xdld record` 就是靠它把下载结果写回数据库的。注释对 aria2c 无影响。
* `xdld record` 会检查文件是否真的存在，缺失的会列出来（`missing: ...`），不会误标记；确实想要“不管在不在都记上”可以加 `--assume-done`。
* `xdld record -i -` 可以从标准输入读清单。

> ⚠️ aria2c 不支持在输入文件里写全局选项（开头一行 `split=4` 会被当成 URI 报错），所以代理等设置是**逐条**写在每个下载下面的。

---

## 6. 代理

`xdld.toml` 里的 `proxy` 会同时作用于：

* GraphQL / v1.1 接口请求（twikit client）
* `video.twimg.com` 的视频下载（httpx）
* 查询 GraphQL query id 时拉取的前端 js bundle

没配代理时访问 x.com 会超时（实测本机直连 `x.com` 不通），所以代理是**必填项**。

---

## 7. 排障

| 现象 | 处理 |
| --- | --- |
| `cookies rejected by X` | Cookie 过期了，重新导出（有效期通常几天） |
| `likes: FAIL ... status: 404` | X 轮换了 GraphQL query id。执行 `xdld refresh-ops` 让它去前端 bundle 里找新的 id 并打补丁 |
| `status: 422 ... cannot be null ... userId` | 没读到账号 id（登录探测失败），重导 Cookie；`--verbose` 能看到完整响应体 |
| `status: 403`（HTML 页面） | 装 `[impersonate]` 附加依赖，然后 `--impersonate chrome124` |
| `status: 429` | 调大 `--sleep 2 5`，并降低 `--page-size` |
| 视频下载 403 | 视频 CDN 也走代理了；确认代理支持 HTTPS CONNECT |
| 视频下载中途 `ConnectError` | 代理抽风。程序会自动重试并从断点继续；持续失败就换个代理节点 |
| `AccountLocked` / 验证码 | X 风控。降低频率、换 IP、养几天号 |

`xdld check --verbose` 会额外打印：当前 GraphQL query id、读到的 user id、每一步耗时；一旦失败还会打印 HTTP 状态码、X 返回的原始响应体、有用的响应头（`x-rate-limit-*`、`cf-ray`、`x-client-transaction-id`）以及针对 401/403/404/422/429 的处理建议。

`xdld refresh-ops` 做的事：抓取登录后的 x.com 页面 → 找到前端 js bundle → 正则出 `Likes` 操作的新 query id → 在当前进程里打补丁并重试。

---

## 8. 目录结构

```
xdld/
├── xdld/
│   ├── cli.py        # 命令行入口
│   ├── config.py     # 配置：默认值 < 配置文件 < 环境变量 < 命令行
│   ├── cookies.py    # Cookie 解析（json / cookies.txt / 纯文本）
│   ├── client.py     # 构建 twikit client（代理 + Cookie）、体检
│   ├── likes.py      # 点赞列表分页
│   ├── media.py      # 挑选最高码率（=最高分辨率）变体
│   ├── download.py   # 流式下载、.part 断点续传、HLS 兜底
│   ├── progress.py   # 实时进度行 / 心跳
│   ├── diag.py       # 失败诊断（状态码 / 响应体 / 响应头 / 处理建议）
│   ├── manifest.py   # aria2c 下载清单（导出 / 回写元数据）
│   ├── store.py      # SQLite 去重 / 状态
│   ├── discover.py   # 404 时自动找回 GraphQL query id
│   └── runner.py     # 主流程编排
└── tests/            # pytest（72 个用例，mock 掉 X 客户端）
```

---

## 9. 说明

仅供个人备份自己点赞过的公开内容使用，请遵守 X 的服务条款与当地法律。
