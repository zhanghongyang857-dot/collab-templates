#!/usr/bin/env python3
"""班子配置：一份 班子.json 描述你的成员（应用、型号、档位、唤醒命令），生成各项目的 collab/成员.md。

用法（一般由 init.sh 调用）：
  team.py init   <班子目录>                         没有 班子.json 时从示例复制一份
  team.py render <班子.json> <成员.md> [--force]     生成成员.md；保留原文件中「## 本项目补充」一节
  team.py projects <NOW.md>                         列出 NOW「任务」表里有 collab/ 的项目目录
只用 Python 标准库。
"""
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / "team-example" / "班子.json"
MARK = "<!-- collab-team:"
EXTRA = "## 本项目补充"


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("成员", [])
    return data


def cell(v) -> str:
    return str(v or "待填").replace("|", "／").replace("\n", " ")


def render(team: dict, src: Path) -> str:
    p = team.get("总裁", {})
    m = team.get("总管", {})
    members = team["成员"]
    disp = team.get("派活脚本", "")
    if disp.startswith("<"):
        disp = ""
    lines = [
        f"{MARK} 由班子配置 {src} 生成，{dt.date.today().isoformat()}。改成员、档位或唤醒方式请改 班子.json 后运行 init.sh team-sync；"
        f"除「{EXTRA[3:]}」一节外不要直接改本文件 -->",
        "# 成员", "",
        "由总裁维护。总裁即用户本人。席位（总管、执行、测试、验收等）随任务或目标流程指定，本表只写常坐的席位。派活前先读接手人那一节的「唤醒」。",
        "",
    ]
    if disp:
        lines += [f"命令行成员用派活脚本派活和续接：`python3 {disp}`（下称 dispatch）。会话 ID 自动记在 `运行记录/`，"
                  "派活前按任务卡和目标流程检查，朝会用 `dispatch meet`。", ""]
    lines += ["| 成员 | 应用 | 常坐席位 |", "|---|---|---|", "| 总裁 | — | 战略、裁决、授权 |"]
    if m:
        lines.append(f"| {cell(m.get('名称'))} | {cell(m.get('应用'))} | 总管 |")
    for x in members:
        lines.append(f"| {cell(x.get('名称'))} | {cell(x.get('应用'))} | {cell(x.get('常坐席位'))} |")
    lines += ["", "## 能力表", "",
              "由总裁定档，总管按「选人」规则使用。同一应用的不同型号各占一行。档位：A 难或关键的事（架构、疑难、对外交付、关键验收）；"
              "B 中等；C 简单（整理、格式、批量、查资料）。成本写相对高低即可。", "",
              "| 成员 | 派活名 | 型号 | 能力档 | 相对成本 | 擅长 | 不适合 | 可用 |", "|---|---|---|---|---|---|---|---|"]
    for x in members:
        lines.append("| " + " | ".join([cell(x.get("名称")), f"`{x['派活名']}`" if x.get("派活名") else "—",
                                         cell(x.get("型号")), cell(x.get("能力档")), cell(x.get("相对成本")),
                                         cell(x.get("擅长")), cell(x.get("不适合")), cell(x.get("可用"))]) + " |")
    lines += ["", "## 总裁",
              f"- 职能：{p.get('职能', '定方向，确认决议，裁决议题，授权超出任务卡范围的事。')}",
              f"- 唤醒：{p.get('唤醒', '在对话里直接问；需要决定的事写进 PROGRESS「卡点 / 待你决定」。')}"]
    if m:
        lines += ["", f"## {m.get('名称', '总管')}"]
        for k in ("职能", "唤醒", "续接", "权限", "回看"):
            if m.get(k):
                lines.append(f"- {k}：{m[k]}")
    for x in members:
        name, did = x.get("名称", "?"), x.get("派活名", "")
        lines += ["", f"## {name}", f"- 职能：{x.get('职能', '待填')}"]
        if did and disp:
            lines += [f"- 唤醒：`dispatch start G00x-Tn <席位> {did} \"<启动语>\"`，总管以后台命令运行。",
                      "- 续接：`dispatch continue G00x-Tn <席位> \"<追问或返工要求>\"`；中途插话先 `dispatch stop <编号>` 再续接。"]
        else:
            lines += [f"- 唤醒：{x.get('唤醒', '待填')}", f"- 续接：{x.get('续接', '待填')}"]
        for k in ("权限", "回看", "备注"):
            if x.get(k):
                lines.append(f"- {k}：{x[k]}")
        if x.get("可用") and not str(x["可用"]).startswith("✓"):
            lines.append(f"- 状态：{x['可用']}")
    return "\n".join(lines) + "\n"


def cmd_render(team_file: Path, out: Path, force: bool) -> int:
    team = load(team_file)
    extra = ""
    if out.exists():
        old = out.read_text(encoding="utf-8")
        if MARK not in old and not force:
            print(f"{out} 不是由班子配置生成的（可能有手写内容），未覆盖。"
                  f"确认后加 --force：原文件备份为 成员.md.bak；要保留的项目专属内容可写进「{EXTRA[3:]}」一节。", file=sys.stderr)
            return 3
        if MARK not in old:
            shutil.copy2(out, out.with_name(out.name + ".bak"))
        i = old.find("\n" + EXTRA)
        if i >= 0:
            extra = old[i:].rstrip() + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(team, team_file) + extra, encoding="utf-8")
    print(f"已生成 {out}")
    return 0


def cmd_init(team_dir: Path) -> int:
    f = team_dir / "班子.json"
    if f.exists():
        print(f"已存在 {f}，未改动")
        return 0
    team_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE, f)
    print(f"已从示例建立 {f}，按你的成员改写后运行 init.sh team-sync")
    return 0


def cmd_projects(now_md: Path) -> int:
    if not now_md.exists():
        print(f"找不到 {now_md}", file=sys.stderr)
        return 1
    text = now_md.read_text(encoding="utf-8")
    sec = re.search(r"(?ms)^## 任务\s*\n(.*?)(?=^## |\Z)", text)
    for m in re.finditer(r"\]\(<?([^)>]+?collab/PROGRESS\.md)>?\)", sec.group(1) if sec else ""):
        p = (now_md.parent / m.group(1)).resolve()
        if p.exists():
            print(p.parent.parent)
    return 0


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "init":
        return cmd_init(Path(argv[1]).expanduser())
    if len(argv) >= 3 and argv[0] == "render":
        return cmd_render(Path(argv[1]).expanduser(), Path(argv[2]).expanduser(), "--force" in argv[3:])
    if len(argv) >= 2 and argv[0] == "projects":
        return cmd_projects(Path(argv[1]).expanduser())
    print(__doc__.strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
