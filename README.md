# collab-templates

两部分，配合使用：

- **项目模板**（`small` / `large`）：每个任务目录一个 `PROGRESS.md` 作为唯一入口，多个 AI 助手共用。
- **NOW 总看板**（`now`）：一个 `NOW.md` 列出所有任务和大方向，`build.py` 读取各任务的 `PROGRESS.md` 生成可视化的 `看板.html`。

## 项目模板

多个 AI 助手（Codex、Claude、Antigravity 等）共用同一份项目上下文的文件模板。用户在各个原生应用里分别对话，成员通过这些文件了解现状、决议和分工，并把结论写回。

核心约定：**`PROGRESS.md` 是每个项目唯一的入口，也是唯一先加载的文件**；其他文件按需读取。详细推理和执行过程留在各自的原生会话，公共文件只写结论和位置。

## 两个版本

| | 小版本 `small` | 大版本 `large` |
|---|---|---|
| 适用 | 任务少、主要一两个成员、不需要多方讨论 | 多成员分别发表观点、任务多、决议多 |
| 文件 | `PROGRESS.md` `任务.md` `归档.md` | `PROGRESS.md` + `协作/`（协议、成员、决议、议题、任务、经验、归档） |
| 规则 | 写在 PROGRESS.md 末尾（8 条） | 独立的 `协作/协议.md`，各项目通用 |
| 决议 | PROGRESS 里一行一条 | PROGRESS 一行摘要 + `协作/决议.md` 全文 |
| 讨论 | 在原生会话里进行，只记结论 | `协作/议题/`，两轮发言 + 用户裁决 |

小版本出现以下情况时升级：需要多个成员分别发表观点、任务超过 5 个、决议超过 8 条。

两个版本都会在项目根目录放 `AGENTS.md`（Codex 等读取）和 `CLAUDE.md`（内容为 `@AGENTS.md`，Claude Code 通过导入读取同一份），只负责把成员引到 `PROGRESS.md`。

## 使用

第一次：克隆到固定位置。

```bash
gh repo clone zhanghongyang857-dot/collab-templates ~/.collab-templates
```

在项目目录里初始化：

```bash
~/.collab-templates/init.sh small
```

```bash
~/.collab-templates/init.sh large
```

其他命令：

```bash
~/.collab-templates/init.sh upgrade
```

```bash
~/.collab-templates/init.sh protocol
```

| 命令 | 作用 |
|---|---|
| `small` / `large [目录] [-n 项目名]` | 新建；目录默认当前目录，项目名默认目录名 |
| `upgrade [目录]` | 小版本补齐 `协作/`，并打印剩余的手动迁移步骤 |
| `protocol [目录]` | 用最新模板覆盖 `协作/协议.md`（只动这一个文件） |
| `now [目录]` | 建立 NOW 总看板；目录默认 `~/Desktop` |
| `now-update [目录]` | 用最新模板覆盖 `NOW/build.py` 和 `NOW/template.html`，不动数据和 `config.json` |

模板更新后：`git -C ~/.collab-templates pull`，再对需要的项目运行 `protocol`，对看板运行 `now-update`。

## NOW 总看板

```bash
~/.collab-templates/init.sh now
```

生成的结构：

```
~/Desktop/
  NOW.md            任务表 + 大方向表（只写链接和元信息，不写具体安排）
  NOW/
    今天.md          今天各时段做什么
    本月布局.md      四周安排 + 本周三件事
    已完成.md        完成的任务移到这里
    截止.md          近期截止（可在 config.json 换成别的文件）
    config.json      分类与配色、截止来源
    build.py         生成脚本
    template.html    页面样式与布局
    看板.html        生成结果，不要手改
```

**数据只在 Markdown 里，HTML 每次从 Markdown 整个重新生成**，所以不存在两份内容需要同步。看板有两个标签页：

- 「今天」：今天的时间轴、近期截止倒计时。
- 「任务」：按大方向分泳道，每个任务一行，带方向内编号、近 14 天活跃度格子和更新天数；顶部汇总空着的方向、超过 4 天没日志的任务和卡点；下面是本月布局与最近完成。

**与项目模板的衔接**：NOW.md 任务表里每一行链接到一个任务的 `PROGRESS.md`（小版本或大版本均可），看板从中读取：

| 读什么 | 来自 PROGRESS.md 的哪里 |
|---|---|
| 目标、截止、状态 | 顶部 `目标：… ｜ 截止：… ｜ 状态：…` |
| 下一步 | `## ▶ 下一步` 下的内容（鼠标悬停显示） |
| 卡点数 | `## 卡点` 开头的小节里 `- ` 开头的行 |
| 活跃度、更新天数 | `## 日志` 下每个 `### YYYY-MM-DD` 标题及其条目数 |

任务按 NOW.md 任务表的「分类」归到大方向：分类和方向名都按 `config.json` 里 `categories` 的关键词匹配，匹配到同一个分类就在同一条泳道。每个分类用一种颜色（`blue` `green` `orange` `purple` `gray` `teal` `pink` `red`）。

**config.json 字段**

| 字段 | 含义 |
|---|---|
| `output` | 看板生成位置，默认 `NOW/看板.html`；例如设为 `~/Desktop/看板.html`，让桌面只放看板、数据放别处 |
| `categories` | `[{name, keywords, color}]`，顺序即匹配优先级 |
| `rest_keywords` | 今天.md 里表示休息、不计时的关键词 |
| `deadlines.file` | 截止来源文件，相对 `NOW/` 或绝对路径，可用 `~` |
| `deadlines.groups` | 只显示这些分组（`## ` 标题的第一个词），空表示全部 |
| `deadlines.category` | 截止分组名用哪个分类的颜色 |
| `deadlines.final_keyword` | 事项名含这个词时单独列为期末 |
| `deadlines.note` | 截止卡片右上角链接的文字 |
| `done_show` | 「最近完成」显示几条 |

**NOW 放在别处时**：`init.sh now <目录>` 建在任意目录；之后建任务时用 `NOW_PATH=<目录>/NOW.md init.sh small`，让 PROGRESS 顶部的「← NOW」链接指对位置。下面 hook 里的路径也换成对应的 `build.py`。

**自动刷新（可选）**：在 `~/.claude/settings.json` 加一个 Stop hook，Claude Code 每轮回答结束后检查相关文件，有变化才重新生成：

```json
"hooks": {
  "Stop": [{ "hooks": [{ "type": "command",
    "command": "python3 ~/Desktop/NOW/build.py --if-changed >/dev/null 2>&1 || true",
    "timeout": 20 }] }]
}
```

只在 Claude Code 里生效；在其他应用或手动改了文件，下一次 Claude 回答结束时补上，或手动运行 `python3 ~/Desktop/NOW/build.py`。

## 稳定性约定

- 脚本除 `protocol`、`now-update` 外从不覆盖已有文件；已存在 `PROGRESS.md` 时直接退出。
- `now-update` 只覆盖代码（`build.py`、`template.html`），从不动 Markdown 数据和 `config.json`；`config.json` 新增字段时，代码对缺省值兼容。
- 已有的 `AGENTS.md` / `CLAUDE.md` 只追加入口说明，重复运行不会重复追加。
- `PROGRESS.md` 顶部注释标明模板版本（如 `large v1`）。协议有不兼容的修改时升主版本号。
- 默认 NOW 路径为 `~/Desktop/NOW.md`，可用环境变量 `NOW_PATH` 覆盖。

## 目录

```
init.sh
small/   PROGRESS.md  任务.md  归档.md  AGENTS.md  CLAUDE.md
large/   PROGRESS.md  AGENTS.md  CLAUDE.md
         协作/ 协议.md 成员.md 决议.md 经验.md 议题/_模板.md 任务/_模板.md 归档/
now/     NOW.md
         NOW/ 今天.md 本月布局.md 已完成.md 截止.md config.json build.py template.html
```
