#!/usr/bin/env python3
"""Regenerate managed note navigation, discovering user-defined domains."""

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

try:
    from .runtime import configure_stdio
except ImportError:
    from runtime import configure_stdio


START = "<!-- BEGIN AUTO-GENERATED:COMPLETE-NAV -->"
END = "<!-- END AUTO-GENERATED:COMPLETE-NAV -->"
LINK_RE = re.compile(r"(?<!!)\[([^]]+)\]\(([^)]+\.md(?:#[^)]*)?)\)")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
LABEL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")


def parse_readme_links(directory):
    readme = directory / "README.md"
    if not readme.exists():
        return []

    links = []
    for label, raw_target in LINK_RE.findall(readme.read_text(encoding="utf-8")):
        target = urllib.parse.unquote(raw_target.split("#", 1)[0])
        if "://" in target:
            continue
        try:
            resolved = (directory / target).resolve()
        except OSError:
            continue
        if resolved.is_relative_to(directory.resolve()) and resolved.exists():
            links.append((resolved, LABEL_DATE_RE.sub("", label.strip())))
    return links


def notes_under(directory):
    return sorted(
        (path for path in directory.rglob("*.md")
         if DATE_RE.match(path.name)
         and not any(part.startswith(".") or part == "assets"
                     for part in path.relative_to(directory).parts)),
        key=lambda path: path.as_posix(),
    )


def encode_link(path, root):
    return path.relative_to(root).as_posix().replace(" ", "%20")


def fallback_label(path):
    match = DATE_RE.match(path.name)
    return match.group(2) if match else path.stem


def ordered_entries(directory):
    links = parse_readme_links(directory)
    labels = {path: label for path, label in links}
    order = {path: index for index, (path, _) in enumerate(links)}

    direct_notes = [
        path for path in directory.glob("*.md") if DATE_RE.match(path.name)
    ]
    direct_notes.sort(
        key=lambda path: (
            0 if path.resolve() in order else 1,
            order.get(path.resolve(), 0),
            "" if path.resolve() in order else path.name,
        )
    )
    linked = [path for path in direct_notes if path.resolve() in order]
    unlinked = sorted(
        (path for path in direct_notes if path.resolve() not in order),
        key=lambda path: path.name,
        reverse=True,
    )

    children = [
        child
        for child in directory.iterdir()
        if child.is_dir() and notes_under(child)
    ]
    children.sort(
        key=lambda child: (
            0 if (child / "README.md").resolve() in order else 1,
            order.get((child / "README.md").resolve(), 0),
            child.name,
        )
    )
    return linked + unlinked, children, labels


def emit_directory(directory, root, level=3):
    direct_notes, children, labels = ordered_entries(directory)
    lines = []

    if direct_notes:
        if children:
            lines.extend(["#" * level + " 本主题", ""])
        for note in direct_notes:
            match = DATE_RE.match(note.name)
            date = match.group(1) if match else "日期未知"
            label = labels.get(note.resolve(), fallback_label(note))
            lines.append(
                "- `{}` [{}]({})".format(date, label, encode_link(note, root))
            )
        lines.append("")

    for child in children:
        count = len(notes_under(child))
        readme = child / "README.md"
        if readme.exists():
            label = labels.get(readme.resolve(), child.name)
            heading = "[{}]({})".format(label, encode_link(readme, root))
        else:
            heading = child.name
        lines.extend(["#" * level + " {}（{}）".format(heading, count), ""])
        lines.extend(emit_directory(child, root, min(level + 1, 6)))

    return lines


def build_navigation(root, domain_order=None):
    lines = []
    total = 0
    configured = domain_order or []
    discovered = [p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and notes_under(p)]
    names = [name for name in configured if name in discovered]
    names += sorted(set(discovered) - set(names))
    for domain_name in names:
        domain = root / domain_name
        if not domain.exists():
            continue
        count = len(notes_under(domain))
        total += count
        readme = domain / "README.md"
        if readme.exists():
            heading = "[{}]({})".format(domain_name, encode_link(readme, root))
        else:
            heading = domain_name
        lines.extend(["### {}（{}）".format(heading, count), ""])
        lines.extend(emit_directory(domain, root, level=4))
    return "\n".join(lines).rstrip(), total


def render(readme, generated):
    text = readme.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise RuntimeError("README complete-navigation markers are missing or duplicated")
    before, remainder = text.split(START, 1)
    _, after = remainder.split(END, 1)
    return before + START + "\n" + generated + "\n" + END + after


def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--domain", action="append", default=[], help="preferred domain order; unlisted domains are still included")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    readme = root / "README.md"
    generated, total = build_navigation(root, args.domain)
    updated = render(readme, generated)
    current = readme.read_text(encoding="utf-8")

    if args.check:
        if updated != current:
            print("Homepage navigation is out of date", file=sys.stderr)
            return 1
    elif updated != current:
        temporary = readme.with_suffix(".md.tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(readme)

    print("complete_navigation_notes={}".format(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
