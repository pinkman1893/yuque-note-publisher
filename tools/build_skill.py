"""Build a clean, installable skill archive from source resources only."""

from pathlib import Path
import zipfile


def build(root):
    skill = root / "skills" / "yuque-note-publisher"
    output = root / "dist" / "yuque-note-publisher-skill-0.1.0.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml"}:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(skill).parts):
                continue
            archive.write(path, path.relative_to(skill.parent).as_posix())
    return output


if __name__ == "__main__":
    print(build(Path(__file__).resolve().parents[1]))
