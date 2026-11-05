"""通过 GitHub API 维护仓库元信息与 Release。

令牌不写死在代码里：从 git credential helper 取（也就是你 git push 用的那个），
没有就退回到环境变量 GITHUB_TOKEN。

用法：
    python scripts/release.py info                 # 查看当前描述、topics、releases
    python scripts/release.py meta                 # 写入描述 / homepage / topics / discussions
    python scripts/release.py release 0.8.2        # 用 CHANGELOG 里对应版本段作为说明创建 Release
    python scripts/release.py attach 0.8.2 dist.zip  # 上传 Release 附件（同名覆盖）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "p-hzc/ha-anjubao"
ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"

DESCRIPTION = (
    "【非官方】安居家园（Anjubao）智能家居接入 Home Assistant｜灯 / 窗帘 / 场景 / 面板按键，"
    "局域网直连优先、云端兜底"
)
TOPICS = [
    "home-assistant",
    "hacs",
    "hacs-integration",
    "homeassistant-integration",
    "custom-integration",
    "anjubao",
    "smart-home",
    "unofficial",
]


def token() -> str:
    env = os.environ.get("GITHUB_TOKEN")
    if env:
        return env.strip()
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as err:
        raise SystemExit(f"取不到 git 凭证: {err}") from err
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("凭证里没有 password，请设置 GITHUB_TOKEN")


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict | list]:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")
        try:
            return err.code, json.loads(body)
        except json.JSONDecodeError:
            return err.code, {"raw": body}


def changelog_section(version: str) -> str:
    """取 CHANGELOG.md 里 `## {version}` 到下一个 `## ` 之间的内容。"""
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"CHANGELOG.md 里没有 {version} 的段落")
    return match.group(1).strip()


def cmd_info() -> None:
    status, repo = api("GET", f"/repos/{REPO}")
    if status != 200:
        raise SystemExit(f"读取仓库失败 {status}: {repo}")
    print(f"描述     : {repo.get('description')}")
    print(f"homepage : {repo.get('homepage') or '(空)'}")
    print(f"topics   : {', '.join(repo.get('topics') or []) or '(空)'}")
    print(f"issues   : {repo.get('has_issues')}   discussions: {repo.get('has_discussions')}")
    print(f"分支     : {repo.get('default_branch')}")
    status, releases = api("GET", f"/repos/{REPO}/releases")
    if status == 200:
        print(f"releases : {[r['tag_name'] for r in releases]}  (#{len(releases)} 个)")


def cmd_meta() -> None:
    status, repo = api(
        "PATCH",
        f"/repos/{REPO}",
        {
            "description": DESCRIPTION,
            "homepage": f"https://github.com/{REPO}",
            "has_issues": True,
            "has_discussions": True,
        },
    )
    print(f"描述/topics 写入: HTTP {status}")
    if status != 200:
        print("  ", repo)
        return
    print("  description:", repo.get("description"))
    print("  discussions:", repo.get("has_discussions"))
    status, body = api("PUT", f"/repos/{REPO}/topics", {"names": TOPICS})
    print(f"topics 写入: HTTP {status} -> {body.get('names') if isinstance(body, dict) else body}")


def cmd_release(version: str) -> None:
    body = changelog_section(version)
    status, existing = api("GET", f"/repos/{REPO}/releases/tags/{version}")
    payload = {
        "tag_name": version,
        "name": version,
        "body": body,
        "draft": False,
        "prerelease": False,
        # GitHub 要求这里是字符串 "true"/"false"/"legacy"，传布尔会 422
        "make_latest": "true",
    }
    if status == 200:
        rel_id = existing["id"]
        status, out = api("PATCH", f"/repos/{REPO}/releases/{rel_id}", payload)
        action = "更新"
    else:
        status, out = api("POST", f"/repos/{REPO}/releases", payload)
        action = "创建"
    if status in (200, 201):
        print(f"Release {action}成功: {out.get('tag_name')}  {out.get('html_url')}")
        print(f"  正文 {len(out.get('body') or '')} 字符")
    else:
        print(f"Release {action}失败 HTTP {status}: {out}")


def cmd_attach(version: str, path: str) -> None:
    """把本地文件作为 Release 附件上传（同名会先删掉再传）。"""
    file = Path(path)
    if not file.is_file():
        raise SystemExit(f"文件不存在: {file}")
    status, rel = api("GET", f"/repos/{REPO}/releases/tags/{version}")
    if status != 200:
        raise SystemExit(f"取不到 Release {version}（HTTP {status}），先跑 release {version}")
    rel_id = rel["id"]

    for asset in rel.get("assets") or []:
        if asset["name"] == file.name:
            api("DELETE", f"/repos/{REPO}/releases/assets/{asset['id']}")
            print(f"已删除同名旧附件: {file.name}")

    url = f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets?name={file.name}"
    req = urllib.request.Request(url, data=file.read_bytes(), method="POST")
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/zip")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode() or "{}")
            print(f"上传成功: {out.get('name')}  {out.get('size')} 字节")
            print(f"  {out.get('browser_download_url')}")
    except urllib.error.HTTPError as err:
        print(f"上传失败 HTTP {err.code}: {err.read().decode(errors='replace')}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "info":
        cmd_info()
    elif cmd == "meta":
        cmd_meta()
    elif cmd == "release":
        if len(argv) < 2:
            raise SystemExit("用法: release <版本号>")
        cmd_release(argv[1])
    elif cmd == "attach":
        if len(argv) < 3:
            raise SystemExit("用法: attach <版本号> <文件路径>")
        cmd_attach(argv[1], argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
