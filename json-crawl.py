import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from collections import Counter
import requests
import tomllib  # Python 3.11+ (for parsing Cargo.toml)
from github import Github  # pip install PyGithub
import json

# ---------------------------
# Config
# ---------------------------
GITHUB_ORG = "ScottyLabs"  # change this
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # personal access token
COMMON_CRATES = {"tokio", "serde", "rand", "clap", "anyhow", "thiserror"}  # not unique
# ---------------------------

g = Github(GITHUB_TOKEN)
org = g.get_organization(GITHUB_ORG)

def count_lines(file_path):
    """Return the number of source lines (ignores blank lines and comments)."""
    count = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("//"):
                    count += 1
    except Exception:
        pass
    return count

def get_languages(repo_path):
    """Use linguist-style heuristic: look at extensions of all files."""
    exts = Counter()
    for p in Path(repo_path).rglob("*"):
        if p.is_file():
            exts[p.suffix.lower()] += 1
    # crude mapping
    lang_map = {
        ".rs": "Rust", ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".go": "Go", ".java": "Java", ".cpp": "C++", ".c": "C", ".h": "C Header",
        ".rb": "Ruby", ".swift": "Swift", ".php": "PHP"
    }
    return list({lang_map.get(ext, ext) for ext in exts})

def parse_cargo(repo_path):
    """Parse Cargo.toml for dependencies, filter out common crates."""
    cargo_file = Path(repo_path) / "Cargo.toml"
    if not cargo_file.exists():
        return []
    try:
        with open(cargo_file, "rb") as f:
            data = tomllib.load(f)
        deps = set(data.get("dependencies", {}).keys())
        # also include workspace-specific crates
        for table in data.keys():
            if isinstance(data[table], dict) and "dependencies" in data[table]:
                deps |= set(data[table]["dependencies"].keys())
        unique = deps - COMMON_CRATES
        return sorted(unique)
    except Exception:
        return []

def analyze_repo(repo_url):
    tmpdir = tempfile.mkdtemp()
    repo_path = os.path.join(tmpdir, "repo")
    try:
        # shallow clone
        subprocess.run(["git", "clone", "--depth", "1", repo_url, repo_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        total_files, rust_files = 0, 0
        total_sloc, rust_sloc = 0, 0

        for p in Path(repo_path).rglob("*"):
            if p.is_file():
                total_files += 1
                sloc = count_lines(p)
                total_sloc += sloc
                if p.suffix == ".rs":
                    rust_files += 1
                    rust_sloc += sloc

        return {
            "repo": Path(repo_url).stem,
            "total_files": total_files,
            "rust_files": rust_files,
            "rust_file_ratio": rust_files / total_files if total_files else 0,
            "total_sloc": total_sloc,
            "rust_sloc": rust_sloc,
            "rust_sloc_ratio": rust_sloc / total_sloc if total_sloc else 0,
            "languages": get_languages(repo_path),
            "unique_crates": parse_cargo(repo_path),
        }
    finally:
        shutil.rmtree(tmpdir)

def main():
    results = []
    for repo in org.get_repos():
        try:
            stats = analyze_repo(repo.clone_url.replace("https://", f"https://{GITHUB_TOKEN}@"))
            results.append(stats)
            with open("repos/" + stats['repo'] + ".json", 'a') as f:
                json.dump(stats, f)
        except Exception as e:
            print(f"Failed on {repo.full_name}: {e}")

if __name__ == "__main__":
    main()
