# Codex 会话只读看板

`codex exec` 跑出的会话不会出现在 Codex 桌面版里。这个本地网页直接读取 Codex 的数据目录，把任意会话按“系统 / 用户 / 上下文 / 助手 / 思考 / 工具”逐行展开，并按轮显示耗时和 token，效果参考 dsh web 的“轨迹”页。

只用 Python 3.9 标准库，不装 Python 依赖、不联网。真实数据里很多会话已被 Codex 压缩成 `.jsonl.zst` 或移入 `archived_sessions/`，看板会自动查找并解压。

## 启动

在项目根目录执行：

```sh
python3 tools/codex-viewer/server.py
```

默认读取 `~/.codex`（即 `state_5.sqlite` 和 `sessions/`），监听 `127.0.0.1:3090`。打开：

http://127.0.0.1:3090

常用参数：

```sh
# 指定数据目录
python3 tools/codex-viewer/server.py --codex-home /path/to/codex-home

# 指定端口
python3 tools/codex-viewer/server.py --port 3100

# 用脱敏样例数据看效果
python3 tools/codex-viewer/server.py \
  --codex-home tools/codex-viewer/fixtures/codex-home \
  --port 3090
```

`--codex-home` 用来指定其他数据目录；不传时固定为 `~/.codex`。服务只监听 `127.0.0.1`，不会对局域网开放。`Ctrl+C` 停止。

## 压缩与归档会话

详情页按下面的顺序定位会话记录：

1. 数据库里的 `rollout_path` 原路径；
2. 原路径加 `.zst`（例如 `rollout-xxx.jsonl.zst`）；
3. 在 `<codex-home>/sessions/` 和 `<codex-home>/archived_sessions/` 下按会话 id 查找 `.jsonl` 或 `.jsonl.zst`。

`.zst` 解压优先调用系统 `zstd -dc`；没有 `zstd` 时调用 `node`，使用 Node.js 22+ 自带的 `zlib.zstdDecompressSync`。两者都没有时，详情页会显示“未找到 zstd 或 node”的提示，不会崩溃。`zstd` / `node` 是可选系统工具，不是 Python 依赖，也不需要联网安装。

## 只读保证

- SQLite 使用 `file:...?mode=ro` 打开，并额外执行 `PRAGMA query_only = ON`；代码里没有任何 `INSERT` / `UPDATE` / `DELETE` / `CREATE`。
- 不创建、不修改、不删除数据目录里的任何文件，包括数据库、`sessions/`、`archived_sessions/` 和日志。
- 会话列表只查 `threads` 表；只有点开某个会话时才读取对应的 `rollout_path`，不会遍历或预读全部会话文件。只有原路径和 `.zst` 都不存在时，才会在 `sessions/`、`archived_sessions/` 下按会话 id 查找。
- `.zst` 解压只读取文件并把结果通过管道交给内存，解压临时结果不落盘；`zstd` / `node` 子进程也不会写入数据目录。
- 解析结果只放在内存里的一个小缓存（最多 8 个会话，按文件修改时间和大小失效），不落盘。
- `session_meta.base_instructions` 不返回给页面；`reasoning.encrypted_content` 不显示，只显示 `summary[].text`。
- 前端所有会话语义文本都用 `textContent` 渲染，不拼接 HTML。
- 页面带有 CSP、`nosniff` 等基础响应头；静态文件走固定白名单，不能通过路径穿越读取其他文件。

## 页面功能

- 左侧按更新时间倒序显示会话：标题、来源（命令行 / 桌面版）、工作目录、时间、token。
- 可按来源筛选，可按标题或工作目录搜索；默认隐藏已归档会话和子 agent，可分别勾选显示。
- 右侧点开后才加载会话文件，按轮分组。每轮显示起始时间、耗时、本轮 token；行首标签为系统、用户、上下文、助手、思考、工具、其他。
- 压缩归档会话会在详情顶部显示“已归档 / zst 压缩”标记和实际记录文件路径。
- 工具调用与输出按 `call_id` 合并为同一行：`function_call` / `custom_tool_call` 配 `function_call_output` / `custom_tool_call_output`。
- `turn_context`、`world_state`、`thread_settings_applied` 和未知类型默认折叠；长内容默认截断，可展开全文。
- 详情顶部可以按标签过滤；`全部展开` / `全部收起` 一次操作所有行。
- 文件缺失、某行 JSON 损坏、未知记录类型都只给出提示或折叠展示，不会让页面崩溃。
- 快捷键：`/` 聚焦搜索，`Esc` 清空搜索。

## 标签规则

| rollout 内容 | 页面标签 |
| --- | --- |
| `response_item` + `message`，`role=developer/system` | 系统 |
| `response_item` + `message`，`role=user`，正文以 `<` 开头 | 上下文 |
| `response_item` + `message`，`role=user`，其余 | 用户 |
| `response_item` + `message`，`role=assistant` | 助手 |
| `response_item` + `reasoning` | 思考（只取 `summary[].text`） |
| `response_item` + `function_call` / `custom_tool_call` | 工具（与输出合并） |
| `turn_context`、`world_state`、`event_msg/thread_settings_applied` | 上下文（折叠） |
| `event_msg/task_started` 到 `task_complete` | 一轮 |
| `event_msg/item_completed`、`token_usage_record` | 跳过（与上面重复） |
| 其他未知类型 | 其他（折叠，不报错） |

脱敏样例里的 `environment_context` 正文被替换成了占位文字，因此解析器还会检查 `content_item_kinds` 里的 `environment` / `context` 标记，避免把环境上下文误判成用户提问。

每轮 token 取该轮最后一条 `event_msg/token_count` 的 `info`：第一轮用 `total_token_usage`，后续轮用相邻累计值做差，得到本轮实际用量；差值为负时回退到 `last_token_usage`。详情里的 `turns[].tokens` 同时包含 `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`。

## HTTP 接口

- `GET /api/health`：数据目录和数据库是否存在。
- `GET /api/sessions?source=all|exec|vscode|other&q=关键词&archived=0|1&subagents=0|1`
- `GET /api/sessions/<id>`：会话头、按轮分组的行、警告、统计和记录文件信息；`source_file.compressed` 表示是否来自 `.zst`，`source_file.tried` 列出实际尝试过的路径。
- `GET /static/app.css`、`GET /static/app.js`：页面静态资源。

接口只在本机提供，没有鉴权；不要通过端口转发暴露到公网。

## 测试

在项目根目录执行：

```sh
python3 -m unittest discover tools/codex-viewer/tests
```

覆盖：

- 普通样例会话解析出 2 轮、耗时和每轮 token 正确；
- 压缩归档样例（`.jsonl.zst`）也能解析出 2 轮；
- 原路径失效时能按 `.zst` 或会话 id 找到文件；
- zstd / node 都不可用时给出明确提示；
- 工具调用与输出按 `call_id` 配对；
- 系统 / 用户 / 上下文 / 助手 / 思考 / 工具 / 其他分类；
- 未知记录类型、坏 JSON 行不报错；
- 列表接口返回样例会话，来源、搜索、归档、子 agent 筛选；
- SQLite 只读连接拒绝写入，请求前后数据目录文件不变；
- 会话文件缺失时详情接口给出提示。

## 文件结构

```
tools/codex-viewer/
├── server.py              # 数据读取、解析和 HTTP 服务
├── README.md
├── static/
│   ├── index.html
│   ├── app.css
│   └── app.js
├── fixtures/codex-home/   # 脱敏样例（含 archived_sessions/*.zst，只读，不要改）
└── tests/
    ├── test_parser.py
    ├── test_store.py
    └── test_api.py
```

## 常见问题

- **页面提示找不到数据库**：确认 `--codex-home` 指向包含 `state_5.sqlite` 的目录；默认是 `~/.codex`。数据库不存在时服务仍会启动，但列表接口会返回明确错误。
- **端口被占用**：换一个 `--port`，或先停掉占用 3090 的进程。
- **某个会话打开很慢**：只会在第一次打开时解析该文件，之后命中内存缓存；特别大的会话仍可能需要几秒。
- **压缩会话打不开，提示未找到 zstd 或 node**：安装系统 `zstd`，或安装 Node.js 22+（`zlib.zstdDecompressSync`）。看板不会自动联网下载工具。
- **归档会话在列表里看不到**：归档会话默认隐藏，勾选左侧“已归档”后再打开。
- **看不到命令行会话**：确认来源筛选是“全部”或“命令行”，并检查是否勾选了“子 agent / 已归档”把列表过滤掉了。
