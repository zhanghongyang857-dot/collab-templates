# collab-templates

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

模板更新后：`git -C ~/.collab-templates pull`，再对需要的项目运行 `protocol`。

## 稳定性约定

- 脚本除 `protocol` 外从不覆盖已有文件；已存在 `PROGRESS.md` 时直接退出。
- 已有的 `AGENTS.md` / `CLAUDE.md` 只追加入口说明，重复运行不会重复追加。
- `PROGRESS.md` 顶部注释标明模板版本（如 `large v1`）。协议有不兼容的修改时升主版本号。
- 默认 NOW 路径为 `~/Desktop/NOW.md`，可用环境变量 `NOW_PATH` 覆盖。

## 目录

```
init.sh
small/   PROGRESS.md  任务.md  归档.md  AGENTS.md  CLAUDE.md
large/   PROGRESS.md  AGENTS.md  CLAUDE.md
         协作/ 协议.md 成员.md 决议.md 经验.md 议题/_模板.md 任务/_模板.md 归档/
```
