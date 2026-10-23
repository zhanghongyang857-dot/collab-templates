# 本地 AI 公司协作工具

建立一个轻量群聊，连接 Codex、Claude、Antigravity 等本地成熟应用中的真实会话。用户负责战略与最终裁决，AI 成员讨论方案、承担任务，并在原生应用中执行和验收，随后将结果回报群聊。

## 当前阶段

需求已整理；子员工命令行派活、御前看板和 Codex 会话回看已跑通，群聊协调层尚未实现。正在整理成一个可公开的仓库：模板和工具已合在本仓库，统一命令、安装脚本和示例项目还没做。产品名称尚未确定。

## 目录

```
templates/             协作模板：small / large 项目模板、NOW 总看板、班子示例、init.sh
tools/board            看板网页服务：NOW 页和各项目御前看板
tools/dispatch         派活脚本：派活、续接、停止、按流程检查、朝会、写运行记录
tools/collab_parse.py  任务卡、目标卡解析（派活脚本和看板共用）
tools/check.py         collab/ 校验：派活前、Claude 每轮结束（Stop hook）、看板顶部都用它
tools/codex-viewer     Codex 会话只读看板
docs/                  需求、班子设计与子员工调用说明
```

模板用法见 [templates/README.md](templates/README.md)，看板见 [tools/board/README.md](tools/board/README.md)，派活见 [tools/dispatch/README.md](tools/dispatch/README.md)。

## 项目文档

- [需求与管理框架](docs/本地LLM应用群聊协作需求.md)：产品边界、战略委员会、任务负责人制、授权、独立验收、首版范围与技术待定事项。
- [多 Agent 班子设计](docs/多Agent班子设计.md)：总裁、总管、执行、验收各席位的组织思想。
- [子员工调用](docs/子员工调用.md)：用命令行向 DeepSeek Harness、Codex 派活和续接的固定做法与验证结果。

## 开发定位

可以借鉴已有项目并自行开发协调层。保留原生应用的完整工具与会话能力，公共群聊负责顶层讨论、分工和结果反馈；详细执行过程继续保存在各应用中。

已有的最小方案：Claude 用命令行向 Codex、DeepSeek 派活和续接，协作状态记在各项目的 `collab/`，规则来自 `templates/`。群聊协调层的技术栈与接入方式待定。
