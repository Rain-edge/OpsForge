# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：二开 diff 统计——按目录聚合 upstream 基线到 HEAD 的行数。
"""OpsForge 二开统计：按目录聚合 upstream 基线到 HEAD 的 diff 行数。

用法：
    python scripts/oss-diff.py              # 默认基线 upstream/master
    python scripts/oss-diff.py v4.0.1       # 指定基线（tag/commit/分支均可）

数据源 = `git diff --numstat <base>...HEAD`（三点 diff，merge-base 为基线，
统计的就是 OpsForge 二开增量）。仅标准库，无第三方依赖。
"""

import argparse
import subprocess
import sys
import unicodedata
from collections import OrderedDict

# 二开目录 → 展示名（顺序即输出顺序；未命中前缀的文件归入"其他"逐项列出）
GROUPS = OrderedDict(
    [
        ("spug_api/apps/ansible_ops/", "后端二开 app（ansible_ops）"),
        ("lab/web-overlay/", "前端二开页面（web-overlay）"),
        ("lab/playbooks/", "Playbook 库"),
        ("lab/net/", "网络设备实验（FRR）"),
        ("lab/nodes/", "被管节点实验环境"),
        ("lab/docker/", "平台镜像构建"),
        ("lab/scripts/", "实验/验收脚本"),
        ("tests/acceptance/", "验收测试套件"),
        ("docs/opsforge/", "项目文档（RUNBOOK/ADR/experiments）"),
    ]
)


def display_width(text):
    """按 East Asian Width 计显示宽度（全角=2，半角=1），保证中英文列对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text, width):
    return text + " " * (width - display_width(text))


def git_numstat(base):
    """跑 git diff --numstat，返回 (added, deleted, path) 三元组列表。"""
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--numstat", f"{base}...HEAD"]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        sys.exit(
            f"git diff 失败（基线 {base} 是否存在？remote 是否已 fetch？）:\n{e.stderr}"
        )
    except FileNotFoundError:
        sys.exit("找不到 git 命令，请确认 git 在 PATH 中。")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if added == "-":  # 二进制文件无行数，跳过
            continue
        rows.append((int(added), int(deleted), path))
    return rows


def main():
    ap = argparse.ArgumentParser(description="OpsForge 二开 diff 统计（按目录聚合）")
    ap.add_argument(
        "base",
        nargs="?",
        default="upstream/master",
        help="diff 基线（默认 upstream/master，可用 tag/commit）",
    )
    args = ap.parse_args()

    rows = git_numstat(args.base)

    groups = OrderedDict((name, [0, 0, 0]) for name in GROUPS.values())
    others = OrderedDict()
    for added, deleted, path in rows:
        for prefix, name in GROUPS.items():
            if path.startswith(prefix):
                g = groups[name]
                g[0] += 1
                g[1] += added
                g[2] += deleted
                break
        else:
            g = others.setdefault(path, [0, 0, 0])
            g[0] += 1
            g[1] += added
            g[2] += deleted

    name_w = (
        max(
            [38]
            + [display_width(name) for name in groups]
            + [display_width("  └ " + p) for p in others]
        )
        + 1
    )
    print(
        f"OpsForge 二开统计（基线 {args.base} ... HEAD，共 {len(rows)} 个文本文件）\n"
    )
    print(f"{pad('分类', name_w)}{'文件':>6}{'新增':>10}{'删除':>8}")
    print("-" * (name_w + 24))
    total = [0, 0, 0]
    for name, (files, added, deleted) in groups.items():
        if files:
            print(f"{pad(name, name_w)}{files:>6}{added:>10}{deleted:>8}")
            total[0] += files
            total[1] += added
            total[2] += deleted
    for path, (files, added, deleted) in others.items():
        print(f"{pad('  └ ' + path, name_w)}{files:>6}{added:>10}{deleted:>8}")
        total[0] += files
        total[1] += added
        total[2] += deleted
    print("-" * (name_w + 24))
    print(f"{pad('合计', name_w)}{total[0]:>6}{total[1]:>10}{total[2]:>8}")


if __name__ == "__main__":
    main()
