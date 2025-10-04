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
import pandas as pd

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


def count_rust_sloc(repo_path):
    totalR = 0
    total = 0

    isRust = False

    for p in Path(repo_path).rglob("*"):
            if p.is_file():
                isRust = p.suffix == ".rs"
                try:
                    with p.open("r", encoding="utf-8", errors="ignore") as f:
                        block_depth = 0          # supports nested /* ... */
                        in_string = False        # " or '
                        string_quote = None
                        raw_hashes = 0           # number of # in r#" ... "#
                        escape = False

                        for line in f:
                            i = 0
                            code_buf = []
                            in_line_comment = False
                            line_len = len(line)

                            while i < line_len:
                                ch = line[i]
                                nxt = line[i+1] if i + 1 < line_len else ''

                                # Inside a block comment: handle nesting
                                if block_depth > 0:
                                    if ch == '/' and nxt == '*':
                                        block_depth += 1
                                        i += 2
                                        continue
                                    if ch == '*' and nxt == '/':
                                        block_depth -= 1
                                        i += 2
                                        continue
                                    i += 1
                                    continue

                                # Not in block comment

                                # Start of line comment (also covers /// and //! doc comments)
                                if ch == '/' and nxt == '/':
                                    in_line_comment = True
                                    break

                                # Start of block comment (/** and /*! doc comments too)
                                if ch == '/' and nxt == '*':
                                    block_depth += 1
                                    i += 2
                                    continue

                                # Strings -------------------------------------------------
                                if in_string:
                                    code_buf.append(ch)

                                    if string_quote == '"':
                                        if raw_hashes == 0:
                                            # normal string with escapes
                                            if not escape and ch == '\\':
                                                escape = True
                                            elif not escape and ch == '"':
                                                in_string = False
                                            else:
                                                escape = False
                                        else:
                                            # raw string: end when we see " followed by raw_hashes #'s
                                            if ch == '"':
                                                # look ahead for hashes
                                                j = i + 1
                                                k = 0
                                                while k < raw_hashes and j < line_len and line[j] == '#':
                                                    j += 1
                                                    k += 1
                                                if k == raw_hashes:
                                                    in_string = False
                                                    i = j - 1  # consume the hashes
                                    else:  # string_quote == "'"
                                        # treat as char literal; simple escape handling
                                        if not escape and ch == '\\':
                                            escape = True
                                        elif not escape and ch == "'":
                                            in_string = False
                                        else:
                                            escape = False

                                    i += 1
                                    continue

                                # Not in string/comment: maybe starting a string
                                if ch == 'r' and (nxt == '"' or nxt == '#'):
                                    # raw string start: r"..." or r#"... "#
                                    j = i + 1
                                    hashes = 0
                                    if j < line_len and line[j] == '#':
                                        while j < line_len and line[j] == '#':
                                            hashes += 1
                                            j += 1
                                    if j < line_len and line[j] == '"':
                                        in_string = True
                                        string_quote = '"'
                                        raw_hashes = hashes
                                        escape = False
                                        # append the starting delimiter as code
                                        code_buf.append('r')
                                        code_buf.extend('#' * hashes)
                                        code_buf.append('"')
                                        i = j + 1
                                        continue

                                if ch == '"' or ch == "'":
                                    in_string = True
                                    string_quote = ch
                                    raw_hashes = 0
                                    escape = False
                                    code_buf.append(ch)
                                    i += 1
                                    continue

                                # Regular code char
                                code_buf.append(ch)
                                i += 1

                            # end while over line

                            # If we hit a line comment, we ignore the remainder of the line.
                            # Count line if, after stripping whitespace, anything remains.
                            if ''.join(code_buf).strip():
                                if (isRust):
                                    totalR += 1
                                else:
                                    total += 1

                        # If file ended while inside a block comment, we just stop (no crash)
                except OSError:
                    # unreadable file; skip it
                    continue

    return totalR, total

def search_cargo_files(repo_name):
    """Search for all Cargo.toml files in a repository using tree API"""
    try:
        # Get the default branch
        url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}"
        response = requests.get(url, headers=get_github_headers())

        if response.status_code == 403:
            print(f"\n⚠️  Rate limit hit! Waiting 60 seconds...")
            time.sleep(60)
            response = requests.get(url, headers=get_github_headers())

        if response.status_code != 200:
            print(f"[Error {response.status_code}]", end=" ")
            return []

        default_branch = response.json().get('default_branch', 'main')

        # Get the tree recursively
        tree_url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/git/trees/{default_branch}?recursive=1"
        tree_response = requests.get(tree_url, headers=get_github_headers())

        if tree_response.status_code == 403:
            print(f"\n⚠️  Rate limit hit! Waiting 60 seconds...")
            time.sleep(60)
            tree_response = requests.get(tree_url, headers=get_github_headers())

        if tree_response.status_code != 200:
            print(f"[Error {tree_response.status_code}]", end=" ")
            return []

        tree_data = tree_response.json()
        cargo_files = []

        # Find all Cargo.toml files
        for item in tree_data.get('tree', []):
            if item['path'].endswith('Cargo.toml'):
                cargo_files.append(item['path'])

        return cargo_files
    except Exception as e:
        print(f"[Exception: {e}]", end=" ")
        return []

def get_file_content(repo_name, file_path):
    """Fetch any file content from a GitHub repo"""
    url = f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/contents/{file_path}"
    response = requests.get(url, headers=get_github_headers())

    if response.status_code == 200:
        import base64
        content = base64.b64decode(response.json()['content']).decode('utf-8')
        return content
    return None

def parse_cargo_dependencies(cargo_content):
    """Parse dependencies from Cargo.toml content"""
    if not cargo_content:
        return []

    dependencies = []
    in_dependencies = False

    for line in cargo_content.split('\n'):
        line = line.strip()

        if line.startswith('[dependencies]'):
            in_dependencies = True
            continue
        elif line.startswith('[') and in_dependencies:
            break
        elif in_dependencies and '=' in line:
            dep_name = line.split('=')[0].strip()
            if dep_name and not dep_name.startswith('#'):
                dependencies.append(dep_name)

    return dependencies

# ---------------------------
# Get all repos from GitHub
# ---------------------------
def get_all_repos():
    """Fetch all repositories from the organization"""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=get_github_headers())

        if response.status_code != 200:
            print(f"Error fetching repos: {response.status_code}")
            break

        data = response.json()
        if not data:
            break

        repos.extend([repo['name'] for repo in data])
        page += 1

    return repos


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
            "Repository Name": Path(repo_url).stem,
            "Number of Files": total_files,
            "Number of Rust Files": rust_files,
            "Rust Files to Total Files": rust_files / total_files if total_files else 0,
            "Total SLOC": total_sloc,
            "Rust SLOC": rust_sloc,
            "Rust SLOC to Total SLOC": rust_sloc / total_sloc if total_sloc else 0,
            "Languages Used": get_languages(repo_path),
            "'Unique' Crates": parse_cargo(repo_path),
        }
    finally:
        shutil.rmtree(tmpdir)

def main(verbose = False):
    results = {
            "Repository Name": [],
            "Number of Files": [],
            "Number of Rust Files": [],
            "Rust Files to Total Files": [],
            "Total SLOC": [],
            "Rust SLOC": [],
            "Rust SLOC to Total SLOC": [],
            "Languages Used": [],
            "'Unique' Crates": [],
    }
    for repo in org.get_repos():
        if (verbose):
            print(repo.name)
        try:
            stats = analyze_repo(repo.clone_url.replace("https://", f"https://{GITHUB_TOKEN}@"))
            for key,value in stats.items():
                results[key].append(value)
            with open("repos/" + stats['Repository Name'] + ".json", 'a') as f:
                f.seek(0) # Move the file pointer to the beginning
                f.truncate(0) # Truncate the file to zero length
                json.dump(stats, f)
        except Exception as e:
            print(f"Failed on {repo.full_name}: {e}")
    df = pd.DataFrame(results)
    df.to_csv('results.csv', index=False)

if __name__ == "__main__":
    main(True)
