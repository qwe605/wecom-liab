"""提交前检查公开内容，发现疑似隐私时仅报告位置，不回显原值。

这是密钥扫描与人工审阅的补充，不能自动证明业务文本已经获得授权。
仅扫描 Git 暂存区中的已跟踪内容，不会用工作区未暂存的修改掩盖待提交版本。
数据库结构迁移允许保留，数据导出和运行快照不得提交。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


BLOCKED_SUFFIXES = (".zip", ".7z", ".tgz", ".tar", ".tar.gz", ".xlsx", ".pem", ".key",
                    ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".dump", ".jsonl", ".log", ".rec")
BLOCKED_DIRECTORIES = {"local-replica", "local_data", "exports", "backups", "secrets", "node_modules", "dist"}
PATTERNS = {
    "phone-number": re.compile(r"(?<![A-Za-z0-9])1[3-9]\d{9}(?![A-Za-z0-9])"),
    "identity-number": re.compile(r"(?<![A-Za-z0-9])\d{17}[\dXx](?![A-Za-z0-9])"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal-workstation-path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+", re.I),
}
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PLATFORM_ID_PATTERN = re.compile(r"\b(?:ww|wx)[0-9a-f]{16}\b", re.I)


def path_is_blocked(name: str) -> bool:
    """判断仓库路径是否为运行数据、凭据或数据库快照。"""
    path = PurePosixPath(name)
    if set(path.parts) & BLOCKED_DIRECTORIES:
        return True
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return True
    if name.lower().endswith(BLOCKED_SUFFIXES):
        return True
    return name.endswith(".sql") and not name.startswith("backend/database/migrations/")


def text_findings(text: str) -> list[tuple[str, int]]:
    """返回规则名和行号；允许保留示例域和必要的 GitHub noreply 署名。"""
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append((rule, number))
        for match in EMAIL_PATTERN.finditer(line):
            domain = match.group(1).lower()
            if domain not in {"example.com", "example.org", "example.net", "users.noreply.github.com", "noreply.github.com"} and not domain.endswith((".invalid", ".example")):
                findings.append(("personal-email", number))
        for match in PLATFORM_ID_PATTERN.finditer(line):
            if match.group()[2:] != "0" * 16:
                findings.append(("non-example-platform-id", number))
    return findings


def main() -> int:
    """检查全部已跟踪文件，返回 0 表示通过、1 表示需人工处理。"""
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    findings = []
    checked = 0
    for name in filter(None, names):
        checked += 1
        if path_is_blocked(name):
            findings.append(f"{name}: blocked-file")
            continue
        try:
            # 检查真正将上传的 index 内容，而不是可能已被局部修改的工作区版本。
            text = subprocess.check_output(["git", "show", f":{name}"], cwd=root).decode("utf-8")
        except UnicodeDecodeError:
            findings.append(f"{name}: binary-file-needs-review")
            continue
        findings.extend(f"{name}:{line}: {rule}" for rule, line in text_findings(text))
    if findings:
        print("\n".join(findings))
        return 1
    print(f"Public content check passed: {checked} tracked files; no policy findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
