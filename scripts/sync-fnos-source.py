#!/usr/bin/env python3
"""Sync fnos-source (https://github.com/yunchui/fnos-source) to the latest
Hermes Studio FPK release from Fnos-Hermes-Studio.

Usage:
    python3 scripts/sync-fnos-source.py <source_checkout_dir>

Environment:
    GH_TOKEN: GitHub PAT with write access to the fnos-source repo.

This script is invoked by .github/workflows/sync-source.yml after a new FPK
release is built. It reads the latest stable release of
yunchui/Fnos-Hermes-Studio, updates fnpack.json (single-file v2 schema)
inside the given checkout, commits, and leaves the push to the workflow.

NOTE: fnos-source uses the v2 single-file schema. There is no separate
fnpackv2.json. The app entry lives under `apps["hermes-studio"]`.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com/repos/yunchui/Fnos-Hermes-Studio"


def _api(path: str):
    token = os.environ["GH_TOKEN"]
    req = urllib.request.Request(
        f"{API}/{path}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "hermes-studio-source-sync",
        },
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def git(*args, cwd=None):
    subprocess.run(["git", *args], check=True, cwd=cwd)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: sync-fnos-source.py <checkout_dir>")
        return 2
    checkout = sys.argv[1]

    # 1) 最新稳定 release tag：形如 v0.6.x[-y]，排除 prerelease / latest
    rels = _api("releases?per_page=20")
    rel = None
    for r in rels:
        t = r.get("tag_name", "")
        if re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9]+)?$", t) and not r.get("prerelease"):
            rel = r
            break
    if not rel:
        print("::error:: 未找到最新稳定 release")
        return 1
    tag = rel["tag_name"]
    version = tag.lstrip("v")
    print(f"最新版本: {tag}")

    # 2) 该 release 的 FPK asset 元数据
    rel = _api(f"releases/tags/{tag}")
    fpk = None
    for a in rel.get("assets", []):
        if re.match(rf"fnos-hermes-studio_v{re.escape(version)}\.fpk$", a["name"]):
            fpk = a
            break
    if not fpk:
        print(f"::error:: release {tag} 缺少 fnos-hermes-studio_v{version}.fpk")
        return 1
    digest = fpk.get("digest", "")
    sha256 = digest[7:] if digest.startswith("sha256:") else digest
    url = fpk["browser_download_url"]
    size = int(fpk["size"])
    published_at = rel.get("published_at")
    print(f"SHA256={sha256}")
    print(f"SIZE={size}")
    print(f"URL={url}")

    # 3) 更新 fnpack.json（v2 单文件 schema）
    fn1 = os.path.join(checkout, "fnpack.json")
    with open(fn1, encoding="utf-8") as f:
        v2 = json.load(f)

    if v2.get("schema_version") != "2":
        print(f"::error:: fnos-source fnpack.json schema 不是 v2: {v2.get('schema_version')}")
        return 1
    if "hermes-studio" not in v2.get("apps", {}):
        print("::error:: fnpack.json 缺 apps.hermes-studio")
        return 1
    app2 = v2["apps"]["hermes-studio"]

    if published_at:
        # GitHub releases 的 published_at 是 UTC，fnOS 期望本地时间 +08:00
        updated_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        updated_at = updated_at.astimezone(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        updated_str = updated_at.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    else:
        updated_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    # 保留既有 changelog（如果有），否则用默认文案
    prev_release = app2.get("releases", {}).get(version)
    changelog = (
        prev_release.get("changelog")
        if prev_release and prev_release.get("changelog")
        else "跟随官方 GitHub Release 预构建产物自动对齐 hermes-web-ui 版本。"
    )

    app2.setdefault("releases", {})[version] = {
        "changelog": changelog,
        "updated_at": updated_str,
        "os_min_version": prev_release.get("os_min_version", "1.0.0") if prev_release else "1.0.0",
        "packages": {"all": {"download_url": url, "sha256": sha256, "size": size}},
    }

    with open(fn1, "w", encoding="utf-8") as f:
        json.dump(v2, f, ensure_ascii=False, indent=2)
    print(f"fnpack.json releases -> {list(app2['releases'].keys())}")

    # 4) 校验 JSON
    with open(fn1, encoding="utf-8") as f:
        json.load(f)
    print("fnpack.json 合法 ✓")

    # 5) 有变更则提交（推送由 workflow 做）
    git("config", "user.name", "github-actions[bot]", cwd=checkout)
    git("config", "user.email", "github-actions[bot]@users.noreply.github.com", cwd=checkout)
    git("add", "fnpack.json", cwd=checkout)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=checkout)
    if diff.returncode != 0:
        git("commit", "-m", f"chore: 更新 Hermes Studio 至 {version}（自动同步）", cwd=checkout)
        print("已提交变更")
    else:
        print("无变更（已是最新）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
