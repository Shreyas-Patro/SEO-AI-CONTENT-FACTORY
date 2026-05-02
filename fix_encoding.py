"""
fix_encoding.py — patch every bare open(...) in agents/ and the project root
to use encoding='utf-8'. Idempotent: skips lines that already specify encoding.

Run with: python fix_encoding.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent
TARGETS = [
    ROOT / "agents",
    ROOT / "graph",
    ROOT,
]

# Match open("...") or open("...", "r") with no encoding arg
# Captures up to the closing paren of the open() call
PAT = re.compile(
    r'open\(\s*("[^"]+\.(?:md|txt|json|yaml|yml)")\s*'
    r'(?:,\s*("r[bt]?"|\'r[bt]?\')\s*)?\)',
)

def needs_fix(line: str) -> bool:
    if "encoding=" in line:
        return False
    if "open(" not in line:
        return False
    return bool(PAT.search(line))

def patch_line(line: str) -> str:
    def replace(m):
        path = m.group(1)
        mode = m.group(2)
        if mode:
            return f'open({path}, {mode}, encoding="utf-8")'
        return f'open({path}, encoding="utf-8")'
    return PAT.sub(replace, line)

def patch_file(path: Path) -> int:
    """Returns number of lines patched."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  skip {path.name}: {e}")
        return 0

    new_lines = []
    n_patched = 0
    for line in text.splitlines(keepends=True):
        if needs_fix(line):
            new = patch_line(line)
            if new != line:
                n_patched += 1
                new_lines.append(new)
                continue
        new_lines.append(line)

    if n_patched:
        path.write_text("".join(new_lines), encoding="utf-8")
    return n_patched


total = 0
for target in TARGETS:
    if target.is_file() and target.suffix == ".py":
        files = [target]
    elif target.is_dir():
        files = list(target.glob("*.py"))
    else:
        continue

    for f in files:
        # don't patch this script itself
        if f.resolve() == Path(__file__).resolve():
            continue
        n = patch_file(f)
        if n:
            print(f"  patched {n} line(s) in {f.relative_to(ROOT)}")
            total += n

print(f"\n✓ Done. {total} line(s) patched across the codebase.")
print("\nIf any agent still hits UnicodeDecodeError, send me the file name and I'll patch manually.")