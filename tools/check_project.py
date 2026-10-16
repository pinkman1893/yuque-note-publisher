"""Check tracked release sources without reading credentials or runtime state."""

import argparse
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix-whitespace", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    files = [Path(name) for name in raw.decode("utf-8").split("\0") if name]
    errors = []
    for relative in files:
        path = root / relative
        if relative.suffix.lower() == ".png":
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                errors.append((relative, "invalid PNG signature"))
            continue
        text = path.read_text(encoding="utf-8")
        normalized = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"
        if text != normalized:
            if args.fix_whitespace:
                path.write_text(normalized, encoding="utf-8", newline="\n")
            else:
                errors.append((relative, "trailing whitespace or extra EOF lines"))
        if re.search(r"[A-Za-z]:[\\/]Users[\\/]\d+", text):
            errors.append((relative, "personal absolute path"))
        if re.search(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{24,})", text):
            errors.append((relative, "possible secret; value withheld"))
        if relative.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if target.startswith(("https://", "http://", "#")):
                    continue
                target = unquote(target.split("#", 1)[0])
                if target and not (path.parent / target).exists():
                    errors.append((relative, "broken local documentation link: " + target))
    for path, reason in errors:
        print(str(path) + ": " + reason)
    print("checked_files=" + str(len(files)))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
