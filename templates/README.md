# 协作模板

两部分，配合使用：

- **项目模板**（`small` / `large`）：每个任务目录一个 `collab/` 文件夹，`collab/PROGRESS.md` 作为唯一入口，多个 AI 助手共用。
- **NOW 总看板**（`now`）：一个 `NOW.md` 列出所有任务和大方向；本仓库的看板服务（`tools/board`）读取它和各任务的 `PROGRESS.md`，在 http://127.0.0.1:3100 显示。

## 项目模板

多个 AI 助手（Codex、Claude、Antigravity 等）共用同一份项目上下文的文件模板。用户在各个原生应用里分别对话，成员通过这些文件了解现状、决议和分工，并把结论写回。

v3 起：日志单独放在 `collab/日志.md`，PROGRESS 只放当前状态；任务卡用「流转」表记录每次状态变化。

v4 起：项目 → 目标 → 子任务三层。目标卡（`G00x`）写总裁要的结果和批准的流程（阶段、席位、人选、通过条件）；子任务按目标编号（`G003-T1`，目标内从 1 起；零散任务仍用全局 `T001`），卡头写「阶段」，阶段进度由子任务状态推出。多个目标时：同时进行中的不超过 3 个，卡头写「优先」（总裁定）和「依赖」，状态可写「暂停」；会话复用以目标为界；完成的目标经总裁批准后归档。任务卡新增身份、硬约束与可替换的做法。议题改为朝会：总管立题并用派活脚本召集多个成员，第 1 轮并行互不可见，最多 3 轮，发言原文由脚本写入，总管写共识与争议，总裁裁决；两个版本都有。成员表新增能力表（档位、成本，由总裁定），总管按难度选人。

协议按席位组织（大版本 `协议.md`）：总则 → 席位（总裁、总管、执行、验收、与会者各自的职责和守则）→ 流程（目标、派活、状态、验收、上报、收工）→ 朝会 → 记录规范 → 本项目补充。约束的是席位而不是成员。要点：总管的分析和框架须总裁明确同意才能派发；框架内的问题由总管自主解决，只有绝对阻塞才上报；用人不疑，任务不逐个交叉验证，验收在目标层面做，默认由总管按成功标准验，第三方验收须总裁在批准框架时同意；总目标达到即通过，返工和改进须总管同意；同一目标里尽量续接已有会话；「待你决定」只由总管写，放下级上报的阻塞和定框架时需要总裁拍板的点，每条以类型开头（批准框架 / 受阻 / 裁决 / 返工 / 授权）。项目特有的规矩写在 `协议.md` 末尾「本项目补充」，`init.sh protocol` 更新时保留。

核心约定：**协作文件全部放在 `collab/` 下，项目根目录只多一个 `AGENTS.md`；`collab/PROGRESS.md` 是每个项目唯一的入口，也是唯一先加载的文件**；其他文件按需读取。详细推理和执行过程留在各自的原生会话，公共文件只写结论和位置。

## 两个版本

| | 小版本 `small` | 大版本 `large` |
|---|---|---|
| 适用 | 目标和任务少、决议少 | 目标多、任务多、决议多 |
| 文件（都在 `collab/` 下） | `PROGRESS.md` `目标.md` `任务.md` `成员.md` `日志.md` `归档.md` `议题/` | `PROGRESS.md` `日志.md` 协议、成员、决议、议题、目标、任务、经验、归档 |
| 规则 | 写在 PROGRESS.md 末尾（15 条） | 独立的 `collab/协议.md`，各项目通用 |
| 决议 | PROGRESS 里一行一条 | PROGRESS 一行摘要 + `collab/决议.md` 全文 |
| 讨论 | `collab/议题/`，朝会（最多 3 轮）+ 总裁裁决；已裁决的留在 `议题/` | 同左；已裁决的移到 `归档/议题/` |

小版本出现以下情况时升级：进行中的目标超过 3 个、任务超过 5 个、决议超过 8 条。

两个版本都只在项目根目录放 `AGENTS.md`（Codex 等读取），只负责把成员引到 `collab/PROGRESS.md`。Claude Code 通过隐藏的 `.claude/CLAUDE.md`（内容为 `@../AGENTS.md`）导入同一份；项目根目录已有 `CLAUDE.md` 时改为在其中追加 `@AGENTS.md`。

```
项目/
  AGENTS.md          唯一露在根目录的协作文件
  .claude/CLAUDE.md  @../AGENTS.md
  collab/            PROGRESS.md 和全部协作文件
```

## 使用

第一次：把本仓库克隆到固定位置，下文以 `~/.collab` 为例（换别的位置时把下面命令里的路径一起换掉）。

```bash
git clone https://github.com/zhanghongyang857-dot/collab-templates.git ~/.collab
```

先建 NOW 总看板（见下文「NOW 总看板」），之后新建的项目会自动加进它的任务表。

在项目目录里初始化（`NOW_PATH` 指向你的 `NOW.md`，默认 `~/Desktop/NOW.md`）：

```bash
NOW_PATH=~/Collab/NOW.md ~/.collab/templates/init.sh small
```

```bash
NOW_PATH=~/Collab/NOW.md ~/.collab/templates/init.sh large
```

其他命令：

```bash
~/.collab/templates/init.sh upgrade
```

```bash
~/.collab/templates/init.sh protocol
```

| 命令 | 作用 |
|---|---|
| `small` / `large [目录] [-n 项目名] [--no-now]` | 新建；目录默认当前目录，项目名默认目录名。同时在 NOW 任务表加一行（状态进行中，分类、等级待填），已有本项目或加 `--no-now` 时不加 |
| `upgrade [目录]` | 小版本在 `collab/` 补齐大版本文件，并打印剩余的手动迁移步骤 |
| `protocol [目录]` | 用最新模板更新 `collab/协议.md`（只动这一个文件，保留「本项目补充」一节） |
| `migrate [目录]` | v1 旧布局（根目录 `PROGRESS.md`、`协作/`）迁到 `collab/`：改写移动文件和项目内指向它们的链接、NOW 里的链接，更新入口说明；需要时用 `NOW_PATH` 指明 NOW 位置 |
| `now [目录]` | 建立 NOW 总看板；目录默认 `~/Desktop`。只建数据文件，生成代码留在本仓库 `now/NOW/` |
| `now-update [目录]` | 旧版 NOW（目录里有 `build.py`）用最新模板覆盖 `build.py` 和 `template.html`；新版 NOW 没有代码，无需更新 |
| `team [班子目录]` | 从示例建立你的班子配置 `班子.json`，默认 `~/.collab-team` |
| `hook [目录]` | 给已有项目装 Claude Code Stop hook（写进 `.claude/settings.json`，已有同一条时不重复加）：每轮结束运行 `tools/check.py --hook`，校验有错就不让这一轮结束。新建项目自动装 |
| `team-sync [目录] [--all] [--force]` | 按班子配置重新生成 `collab/成员.md`；默认只刷当前项目，`--all` 刷 NOW 任务表里的全部项目；不是生成的文件要加 `--force`（原文件备份为 `成员.md.bak`） |

模板更新后：`git -C ~/.collab pull`，再对需要的项目运行 `protocol`，对看板运行 `now-update`。

## 你的班子

模板里的成员表是空框架。你自己的班子（有哪些成员、型号、档位、成本、唤醒和续接命令、权限）写在**班子配置**里，只写一次，各项目共用：

```bash
~/.collab/templates/init.sh team
```

会建立 `~/.collab-team/班子.json`（从 `team-example/班子.json` 复制），按你的成员改写。之后：

- `init.sh small` / `large` 新建项目时，`collab/成员.md` 直接按班子生成，不再是空表；
- 改了班子后，在项目目录运行 `init.sh team-sync` 刷新该项目的 `成员.md`（加 `--all` 刷 NOW 里的全部项目）；
- 生成的 `成员.md` 顶部有标记，除末尾「## 本项目补充」一节外不要手改，那一节在刷新时保留；
- 派活脚本（如果你用）也直接读 `班子.json` 里的命令。

班子目录可用环境变量 `COLLAB_TEAM` 改位置。建议把 `~/.collab-team` 做成你自己的私有仓库：换电脑时拉下模板仓库和班子仓库，班子就能直接用。没有班子配置时，一切照旧。

## NOW 总看板

```bash
~/.collab/templates/init.sh now ~/Collab
```

目录不写时默认 `~/Desktop`。建在 `~/Collab` 的好处是看板服务默认就读 `~/Collab/NOW`，启动时不用带参数。生成的结构：

```
~/Collab/
  NOW.md            任务表 + 大方向表（只写链接和元信息，不写具体安排）
  NOW/
    今天.md          今天各时段做什么
    本月布局.md      四周安排 + 本周三件事
    已完成.md        完成的任务移到这里
    截止.md          近期截止（可在 config.json 换成别的文件）
    config.json      分类与配色、截止来源
    看板.html        生成结果，不要手改
```

生成代码（`build.py`、`template.html`）不复制进来，用本仓库 `now/NOW/` 那一份：`python3 <仓库>/templates/now/NOW/build.py --now-dir <目录>/NOW`，`NOW.md` 里会写好这条命令。改样式改仓库里的 `template.html`。

**数据只在 Markdown 里，HTML 每次从 Markdown 整个重新生成**，所以不存在两份内容需要同步。看板有两个标签页：

- 「今天」：今天的时间轴、近期截止倒计时。
- 「任务」：按大方向分泳道，每个任务一行，带方向内编号、近 14 天活跃度格子和更新天数；顶部汇总空着的方向、超过 4 天没日志的任务和卡点；下面是本月布局与最近完成。

**与项目模板的衔接**：NOW.md 任务表里每一行链接到一个任务的 `collab/PROGRESS.md`（小版本或大版本均可），看板从中读取：

| 读什么 | 来自 PROGRESS.md 的哪里 |
|---|---|
| 目标、截止、状态 | 顶部 `目标：… ｜ 截止：… ｜ 状态：…` |
| 下一步 | `## ▶ 下一步` 下的内容（鼠标悬停显示） |
| 卡点数 | `## 卡点` 开头的小节里 `- ` 开头的行 |
| 活跃度、更新天数 | 同目录 `日志.md` 中每个 `### YYYY-MM-DD` 标题及其条目数（没有 `日志.md` 时读 PROGRESS 的 `## 日志`） |

任务按 NOW.md 任务表的「分类」归到大方向：分类和方向名都按 `config.json` 里 `categories` 的关键词匹配，匹配到同一个分类就在同一条泳道。每个分类用一种颜色（`blue` `green` `orange` `purple` `gray` `teal` `pink` `red`）。

**config.json 字段**

| 字段 | 含义 |
|---|---|
| `output` | 看板生成位置，默认 `NOW/看板.html`；相对路径从 `NOW/` 算，可用 `~`。例如设为 `~/Desktop/看板.html`，让桌面只放看板；用常驻网页时设为 `备份/看板.html` |
| `categories` | `[{name, keywords, color}]`，顺序即匹配优先级 |
| `rest_keywords` | 今天.md 里表示休息、不计时的关键词 |
| `deadlines.file` | 截止来源文件，相对 `NOW/` 或绝对路径，可用 `~` |
| `deadlines.groups` | 只显示这些分组（`## ` 标题的第一个词），空表示全部 |
| `deadlines.category` | 截止分组名用哪个分类的颜色 |
| `deadlines.final_keyword` | 事项名含这个词时单独列为期末 |
| `deadlines.note` | 截止卡片右上角链接的文字 |
| `done_show` | 「最近完成」显示几条 |

**代码和数据分开放**：`build.py --now-dir <NOW 目录>`（或环境变量 `NOW_DATA_DIR`）读指定目录的数据和 `config.json`，`template.html` 用脚本同目录那份。这样 NOW 目录只放数据，代码只留模板仓库一份，不用 `now-update`。看板网页服务就是这样调用的。

**NOW 放在别处时**：`init.sh now <目录>` 建在任意目录；之后建任务时用 `NOW_PATH=<目录>/NOW.md init.sh small`，让 PROGRESS 顶部的「← NOW」链接指对位置。下面 hook 里的路径也换成对应的 `build.py`。

**自动刷新（可选）**：在 `~/.claude/settings.json` 加一个 Stop hook，Claude Code 每轮回答结束后检查相关文件，有变化才重新生成：

```json
"hooks": {
  "Stop": [{ "hooks": [{ "type": "command",
    "command": "python3 <仓库>/templates/now/NOW/build.py --now-dir ~/Desktop/NOW --if-changed >/dev/null 2>&1 || true",
    "timeout": 20 }] }]
}
```

只在 Claude Code 里生效；在其他应用或手动改了文件，下一次 Claude 回答结束时补上，或手动运行 `NOW.md` 里写的生成命令。

**常驻网页（推荐，替代静态文件和 hook）**：协作工具包的 `tools/board` 是一个本地网页服务（默认 http://127.0.0.1:3100），每次打开都调用 `NOW/build.py` 当场生成 NOW 页，生成结果只放临时目录，另有各项目的御前看板，见它的 README。用它时：

- 把 `NOW.md` 索引和 `今天.md`、`本月布局.md`、`已完成.md` 第 2 行的「看板」链接改为 `http://127.0.0.1:3100`；
- `config.json` 的 `output` 改到不常看的位置（如 `备份/看板.html`，相对 `NOW/`），只在手动运行 `build.py` 时生成；
- 不再需要 Stop hook。

## 稳定性约定

- 脚本除 `protocol`、`now-update`、`migrate` 外从不覆盖已有文件；已存在 `collab/PROGRESS.md` 时直接退出。
- `now-update` 只覆盖代码（`build.py`、`template.html`），从不动 Markdown 数据和 `config.json`；`config.json` 新增字段时，代码对缺省值兼容。
- 已有的 `AGENTS.md` / `CLAUDE.md` 只追加入口说明，重复运行不会重复追加。
- `PROGRESS.md` 顶部注释标明模板版本（如 `large v2`；v2 起协作文件放在 `collab/`）。协议有不兼容的修改时升主版本号。
- 默认 NOW 路径为 `~/Desktop/NOW.md`，可用环境变量 `NOW_PATH` 覆盖。
- `team-sync` 只改 `collab/成员.md`，且只覆盖带生成标记的文件（或加 `--force`，先备份）；「本项目补充」一节始终保留。

## 目录

```
init.sh  team.py（班子配置 → 成员.md）
team-example/ 班子.json（班子配置示例）
small/   AGENTS.md  .claude/CLAUDE.md
         collab/ PROGRESS.md 目标.md 任务.md 成员.md 日志.md 归档.md 议题/_模板.md
large/   AGENTS.md  .claude/CLAUDE.md
         collab/ PROGRESS.md 日志.md 协议.md 成员.md 决议.md 经验.md 议题/_模板.md 目标/_模板.md 任务/_模板.md 归档/
now/     NOW.md
         NOW/ 今天.md 本月布局.md 已完成.md 截止.md config.json build.py template.html（代码不复制给用户）
```
