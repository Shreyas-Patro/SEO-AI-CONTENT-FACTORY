import os
import json
from datetime import datetime

# Folders/files to ignore
EXCLUDE = {
    ".git", "__pycache__", "venv", "node_modules",
    ".idea", ".vscode", ".DS_Store"
}

# Toggle timestamped filenames
USE_TIMESTAMP = False


def get_filename(base, ext):
    if USE_TIMESTAMP:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{base}_{ts}.{ext}"
    return f"{base}.{ext}"


# ------------------ JSON STRUCTURE ------------------ #
def build_tree(path):
    tree = {
        "name": os.path.basename(path) or path,
        "type": "folder",
        "children": []
    }

    try:
        items = sorted(os.listdir(path))
    except PermissionError:
        return tree

    for item in items:
        if item in EXCLUDE:
            continue

        full_path = os.path.join(path, item)

        if os.path.isdir(full_path):
            tree["children"].append(build_tree(full_path))
        else:
            tree["children"].append({
                "name": item,
                "type": "file"
            })

    return tree


# ------------------ TREE TEXT ------------------ #
def write_tree(start_path, file, prefix=""):
    try:
        items = sorted(os.listdir(start_path))
    except PermissionError:
        return

    filtered = [i for i in items if i not in EXCLUDE]

    for i, item in enumerate(filtered):
        path = os.path.join(start_path, item)
        connector = "└── " if i == len(filtered) - 1 else "├── "
        line = prefix + connector + item + "\n"
        file.write(line)

        if os.path.isdir(path):
            extension = "    " if i == len(filtered) - 1 else "│   "
            write_tree(path, file, prefix + extension)


# ------------------ MAIN ------------------ #
if __name__ == "__main__":
    root_path = "."

    # JSON output
    json_filename = get_filename("repo_map", "json")
    structure = build_tree(root_path)

    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2)

    # Tree output
    txt_filename = get_filename("repo_structure", "txt")

    with open(txt_filename, "w", encoding="utf-8") as f:
        write_tree(root_path, f)

    print(f"Saved JSON → {json_filename}")
    print(f"Saved Tree → {txt_filename}")