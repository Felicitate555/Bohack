"""Check staged content without printing secret values or file contents."""
from pathlib import Path
import subprocess
import sys


def git(*args):
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def main():
    config = Path(".env")
    secrets = []
    if config.exists():
        for line in config.read_text(encoding="utf-8-sig").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() in {"SILICONFLOW_API_KEY", "LLM_API_KEY"}:
                value = value.strip().strip('"').strip("'")
                if len(value) >= 8:
                    secrets.append(value.encode("utf-8"))
    paths = [p.decode("utf-8") for p in git("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z").split(b"\0") if p]
    for path in paths:
        name = Path(path).name.lower()
        if name == ".env" or name.startswith(".env.") and name != ".env.example":
            print("Blocked: a private environment file is staged:", path)
            return 1
        content = git("show", ":" + path)
        if any(secret in content for secret in secrets):
            print("Blocked: a staged file contains a configured API key:", path)
            return 1
    print("Staged files passed the local API-key check.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        print("Unable to check staged files. Nothing was pushed.")
        sys.exit(1)
