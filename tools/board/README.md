# 看板网页服务

一个本地网页，两页互相独立：

- **NOW 页**（`/`）：每次打开都调用本仓库 `templates/now/NOW/build.py`（`--now-dir` 指向 NOW 数据目录）当场生成，不另写解析，生成结果只放临时目录；各 Markdown 和 `config.json` 里 `output` 指向的静态文件都不改。NOW 目录只放数据，代码不再复制过去，改样式改模板里的 `template.html`。桌面静态 `看板.html` 和 Stop hook 已于 2026-10-22 停用，这一页就是 NOW 看板；手动运行 `build.py --now-dir <NOW 目录>` 仍能出静态文件（现在生成到 `NOW/备份/看板.html`）。额外做了三件事：有 `collab/` 的任务旁加「看板」入口；点 Markdown 链接用系统默认应用打开；离开再回到此页时自动刷新。
- **御前看板**（`/p/<项目>`）：一个项目从上到下是——
  - 顶部摘要：在推进的目标数、各状态任务数、是否有派活在跑、派活次数和 token 合计。
  - 校验：`tools/check.py` 查出问题时，摘要下面出现「校验」卡，错误标红、提醒标黄，写明文件和行号。有错误时派活脚本不派活，Claude 每轮结束也会被要求先改好。没有目标卡时，目标区提示流程图要从目标卡来。
  - 第一屏：左边「待你决定」（按「编号｜类型：要决定什么｜选项与代价｜总管建议｜验收意见｜提出者」拆成小卡，左侧色条和标签区分类型，标出总管建议，编号可点）；右边「现在」（每个目标一句"等谁做什么"，下面是 ▶ 下一步，现状只露 3 行）。有派活在跑时，下面单独出现「正在运行」。
  - 目标：总览一行一个目标（状态、优先、阶段进度条、现在、待你决定 / 受阻提醒、派活和 token、截止），按「有待你决定 > 受阻 > 进行中 > 待批准 > 暂停」再按优先排。**选目标只在这里**：点一行选中，泳道、任务、派活记录一起切到这个目标，再点取消；不选时任务和记录看全部，泳道展开排第一的目标。选中的目标写在地址栏 `#G00x`，可直接链接。已归档的目标、任务、议题不在这一页，见下面的归档页。
  - 泳道流程图：按席位分道（总裁 / 总管 / 执行 / 第三方验收，只有出现第三方验收时才有那一道），执行按同一阶段里并行的任务分子道，从上到下是时间，按页面宽度铺开；数据来自目标卡流程表和各任务卡「流转」。浅绿完成、浅红受阻或需补充、黄色是现在所在、虚线是还没到的阶段；点格子看那一步的说明和该任务各次派活的用时、token。「看框架和偏离」展开流程表和偏离记录。
  - 任务：按状态分组的列表（空组不显示），一行一个任务，点开在下面看流转和派活记录。任务编号 `G00x-Tn` 和旧的 `T00x` 都认。
  - 记录：「派活记录」和「决议」两个标签。派活按开始日期分组（新的在上，组头写当天次数、合计用时和 token），列为派活、任务、席位·成员、状态、开始、用时、token、结果（去掉 Markdown 符号，最多两行）；点一行看启动语、token 明细和最终回复。
  - 每 5 秒刷新。
  - 用 `--override` 派的活在目标、任务卡和派活记录上都标「偏离」，点开看理由。
  - 议题（朝会）：每个议题显示 第1轮 → 第2轮 → 总结 → 裁决 的进度、与会者和正在发言的人；点开按轮看发言原文、总结和裁决。待裁决的标黄。
  - 顶部标题行有「已归档 N 个目标、N 张任务 →」链接（有归档时才出现），通往归档页；目标流程表里已归档的子任务也链到归档页对应任务。
  - NOW 页「看板」入口后显示摘要，如「看板 · G001 ②/5 · I001 待裁决」，有受阻的标黄。

- **归档页**（`/p/<项目>/archive`）：只看 `归档.md` 或 `归档/目标/`、`归档/任务/`、`归档/议题/` 里的卡，和项目页分开。归档目标一行一个，点一行看它的泳道流程图（降低饱和度）和「看框架和偏离」；归档任务按所属目标分组，零散任务单列，点开看流转和派活记录；归档议题可看各轮发言和裁决。只在打开时加载一次，不轮询，项目页的 5 秒刷新不会因此变重；地址栏 `#G00x` 选目标、`#G00x-Tn` / `#T00x` 直接展开任务。被取代的决议暂不显示。

第一版不显示成员的对话过程；需要时去 Codex 回看看板或 dsh web 看。

## 启动

在项目根目录执行：

```sh
python3 tools/board/server.py
```

打开 http://127.0.0.1:3100

| 参数 | 作用 |
|---|---|
| `--port` | 端口，默认 3100 |
| `--now-dir` | NOW 数据目录（Markdown 和 `config.json`），默认环境变量 `COLLAB_NOW_DIR`，没有时 `~/Collab/NOW`；生成代码用本仓库 `templates/now/NOW/` |
| `--project <目录>` | 额外显示一个不在 NOW 里的项目，可重复 |

## 常驻运行

常驻由两部分组成：一个「御前看板」小应用，和 launchd 用户代理。小应用只负责反复启动 `server.py`（服务退出 3 秒后重启），并持有 macOS 的「文稿」访问权；launchd 负责登录后自动启动它、它退出后拉起。

> 为什么不用 launchd 直接启动 Python：实测 launchd 启动的 `/usr/bin/python3` 读 `~/Documents` 会被 macOS 隐私保护拦成 `Operation not permitted`，而且不会弹授权窗口。让应用由系统正常启动一次、总裁点「允许」后，它启动的 Python 子进程继承授权，不需要「完全磁盘访问」。

```sh
bash tools/board/build_app.sh          # 可选：只重建应用，不安装
bash tools/board/service.sh install    # 装应用到 ~/Applications、写 plist、启用
bash tools/board/service.sh status     # 应用 / launchd / 启动器 / 服务 / 接口 / 最近日志
bash tools/board/service.sh restart
bash tools/board/service.sh logs       # logs -f 跟踪，logs 200 看最后 200 行
bash tools/board/service.sh uninstall  # 停用、删 plist/应用/配置，日志保留
```

`install` 会做四件事：

1. 生成（或复用）`tools/board/build/御前看板.app`，拷贝到 `~/Applications/御前看板.app`；
2. 用 `open` 启动应用做一次权限检查——**首次会弹「御前看板想访问文稿文件夹」，请点「允许」**；
3. 停掉 3100 上手动启动的同一个看板进程（其他进程不动）；
4. 写 `~/Library/LaunchAgents/com.local.ai-company-board.plist` 并加载，等接口通。

| 内容 | 位置 |
|---|---|
| 应用 | `~/Applications/御前看板.app` |
| 配置 | `~/Library/Application Support/ai-company-board/config`（SERVER / PORT / NOW_DIR / LOG_DIR） |
| launchd 代理 | `~/Library/LaunchAgents/com.local.ai-company-board.plist` |
| 启动器日志 | `~/Library/Logs/ai-company-board/launcher.log`、`launcher.err.log` |
| 服务日志 | `~/Library/Logs/ai-company-board/board.log`、`board.err.log` |

- 端口默认 3100，安装时可用 `BOARD_PORT=3200 bash tools/board/service.sh install` 改；NOW 目录可用 `BOARD_NOW_DIR=...` 指定；不指定时沿用已安装配置里的值，首次安装默认 `~/Collab/NOW`。改过的值写进配置，之后 `status` / `restart` 以配置为准。
- 只监听 `127.0.0.1`。`KeepAlive` 为真：杀启动器约 5 秒后由 launchd 拉起；杀 Python 由启动器 3 秒后拉起。
- 改 `server.py` 不需要重建应用；应用里只放启动器，每次启动都读取配置里的 `SERVER`，指向当前的 `tools/board/server.py`。项目目录移动后重跑 `install` 会重写配置，应用不用重建。只有改 `launcher.c` / `build_app.sh` 后 `install` 才重建；重建后签名会变，系统可能再问一次「文稿」。
- `build_app.sh` 会做 `plutil -lint` 和 `codesign --verify --deep --strict`；应用是 ad-hoc 签名，不需要开发者证书。
- `install` 要在普通 Terminal 里跑；受限沙箱可能不能写 `~/Library`、`~/Applications`。
- `uninstall` 会删应用、配置和 plist，但 macOS 里给「御前看板」的「文稿」授权还在；要彻底清掉，去 系统设置 → 隐私与安全性 → 文件与文件夹 删除「御前看板」。

### 权限问题排查

1. `bash tools/board/service.sh status`，看「应用」「启动器」「服务进程」「接口」和最近日志。
2. 日志里出现 `Operation not permitted`：
   - 安装时没点「允许」：先 `open -a ~/Applications/御前看板.app` 触发弹窗并点允许，等几秒后重跑 `bash tools/board/service.sh install`（会先停掉这个手动实例）。
   - 已点过拒绝：系统设置 → 隐私与安全性 → 文件与文件夹 → 御前看板 → 打开「文稿文件夹」。
   - 仍不行：`service.sh uninstall` 后重装，让系统重新询问。
3. 端口被别的程序占用：看 `lsof -nP -iTCP:3100 -sTCP:LISTEN`；`install` 只停本看板的 `server.py`，不会杀其他进程。
4. 只想临时看一次：`python3 tools/board/server.py`，用完 Ctrl+C；会影响常驻服务时先 `service.sh uninstall`，看完再 install。

## 数据从哪里来

| 内容 | 来源 |
|---|---|
| 项目列表 | NOW.md「任务」表和 `NOW/已完成.md` 里链接到 `collab/PROGRESS.md` 的行，加 `--project` |
| 下一步、现状、决议、待你决定 | 项目 `collab/PROGRESS.md` |
| 目标卡 | `collab/目标.md`（小版本）或 `collab/目标/G*.md`（大版本），含归档；卡头「状态」和「流程」表 |
| 任务卡 | `collab/任务.md`（小版本）或 `collab/任务/T*.md`（大版本），含归档；卡头「状态：… ｜ 目标：… ｜ 阶段：… ｜ 执行：… ｜ 验收：…」和「流转」表。解析在 `tools/collab_parse.py`，与派活脚本共用 |
| 议题 | `collab/议题/I*.md`（大版本另有 `归档/议题/`）：问题、与会、各轮发言、总结、裁决 |
| 运行中与派活记录 | `collab/运行记录/*.json`，由[派活脚本](../dispatch/README.md)写入 |

只读：不写任何项目文件。唯一的动作是 `POST /api/open` 用 `open` 打开家目录下的文件（限 Markdown、文本、网页、图片、PDF 等），且要求请求头 `X-Board: 1`。只监听 `127.0.0.1`，只用 Python 标准库。
