import json
import os
import requests
from pathlib import Path
from collections import Counter
import time

# ---------------------------
# Config
# ---------------------------
GITHUB_ORG = "ScottyLabs"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Set this: export GITHUB_TOKEN=your_token
REPOS_DIR = 'repos'

# ---------------------------
# GitHub API Functions
# ---------------------------
def get_github_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

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

# ---------------------------
# Main Analysis
# ---------------------------
print("Fetching all ScottyLabs repositories from GitHub...")
print("=" * 80)

all_repos = get_all_repos()
print(f"Found {len(all_repos)} total repositories\n")

# Load local JSON data for SLOC info
sloc_data = {}
for json_file in Path(REPOS_DIR).glob('*.json'):
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        sloc_data[data['repo']] = {
            'rust_sloc': data.get('rust_sloc', 0),
            'proportion': data.get('rust_sloc_ratio', 0)
        }
    except Exception as e:
        print(f"Error reading {json_file}: {e}")

rust_repos_local = []
for repo in all_repos:
    if repo in sloc_data and sloc_data[repo]['rust_sloc'] > 0:
        rust_repos_local.append({
            'repo': repo,
            'rust_sloc': sloc_data[repo]['rust_sloc'],
            'proportion': sloc_data[repo]['proportion']
        })

print(f"Repositories with Rust code: {len(rust_repos_local)}")

# Fetch actual dependencies from GitHub
all_crates = Counter()
repo_crates = {}

for repo_data in rust_repos_local:
    repo_name = repo_data['repo']
    print(f"Searching {repo_name}...", end=" ")

    # Search for all Cargo.toml files in the repo
    cargo_files = search_cargo_files(repo_name)

    if cargo_files:
        print(f"found {len(cargo_files)} Cargo.toml file(s)")
        all_deps = set()

        for cargo_path in cargo_files:
            print(f"  - {cargo_path}...", end=" ")
            cargo_content = get_file_content(repo_name, cargo_path)
            if cargo_content:
                deps = parse_cargo_dependencies(cargo_content)
                all_deps.update(deps)
                print(f"({len(deps)} deps)")
            else:
                print("✗")
            time.sleep(1)  # Increased delay to avoid rate limits

        repo_crates[repo_name] = list(all_deps)
        for dep in all_deps:
            all_crates[dep] += 1
    else:
        repo_crates[repo_name] = []
        print("✗ (no Cargo.toml files)")

    time.sleep(1.5)  # Longer delay between repos to avoid rate limits

total_rust_repos = len([r for r in repo_crates.values() if r])

# Infrastructure crates to ignore (used everywhere, not indicative of purpose)
IGNORE_CRATES = {
    # Async runtimes
    'tokio', 'async-std', 'async-trait', 'futures',

    # Serialization
    'serde', 'serde_json', 'serde_derive',

    # Error handling
    'anyhow', 'thiserror',

    # Logging
    'log', 'env_logger', 'tracing',

    # Environment
    'dotenv', 'dotenvy', 'dotenv_codegen',

    # Common utilities
    'chrono', 'uuid', 'regex', 'lazy_static', 'once_cell',

    # HTTP basics
    'http', 'http-body-util',

    # Encoding
    'base64', 'urlencoding',

    # Configuration
    'config',

    # Local dependencies (workspace crates)
    'models', 'path', 'search', 'migration',
}

# Crate purpose mapping
crate_purposes = {
    # Web frameworks
    'axum': 'web server/API framework',
    'actix-web': 'web server/API',
    'rocket': 'web server/API',
    'warp': 'web server/API',
    'tower': 'middleware for network services',
    'tower-http': 'HTTP middleware',
    'tower-sessions': 'session management',
    'tower-oauth2-resource-server': 'OAuth2 resource server',

    # Database
    'sea-orm': 'database ORM',
    'diesel': 'database ORM',
    'sqlx': 'database access',

    # Bot frameworks
    'poise': 'Discord bot',
    'serenity': 'Discord bot',
    'slack-morphism': 'Slack bot',

    # Auth
    'samael': 'SAML authentication',
    'jsonwebtoken': 'JWT authentication',

    # Search/ranking
    'bm25': 'search ranking algorithm',
    'meilisearch-sdk': 'search engine client',

    # WebAssembly
    'wasm-bindgen': 'WebAssembly bindings',
    'wasm-bindgen-futures': 'WebAssembly async',

    # File handling
    'zip': 'ZIP file handling',
    'git2': 'Git operations',

    # HTTP
    'reqwest': 'HTTP client',
    'hyper': 'HTTP implementation',

    # API documentation
    'utoipa': 'OpenAPI documentation',
    'utoipa-axum': 'OpenAPI for Axum',
    'utoipa-swagger-ui': 'Swagger UI integration',

    # Desktop apps
    'tauri': 'desktop app framework',
    'tauri-plugin-opener': 'Tauri file opener plugin',
    'tauri-plugin-deep-link': 'Tauri deep linking',

    # Storage
    'minio': 'S3-compatible object storage',

    # Caching
    'moka': 'in-memory cache',

    # Templating
    'askama': 'type-safe templates',

    # CLI
    'clap': 'command-line argument parser',
    'colored': 'terminal colors',

    # Concurrency
    'rayon': 'data parallelism',
    'async-std': 'async runtime',

    # Logging/tracing
    'tracing-subscriber': 'structured logging',

    # Data structures
    'priority-queue': 'priority queue',
}

# Analysis
print("\n" + "=" * 80)
print("Rust Usage Analysis - ScottyLabs Repositories")
print("=" * 80)

rust_repos_local.sort(key=lambda x: x['proportion'], reverse=True)

for repo_data in rust_repos_local:
    repo_name = repo_data['repo']
    crates = repo_crates.get(repo_name, [])

    # Filter out infrastructure crates and apply usage threshold
    threshold = 2
    filtered_crates = [c for c in crates if c not in IGNORE_CRATES]
    truly_unique = [c for c in filtered_crates if all_crates[c] <= threshold]

    print(f"\n{repo_name}")
    print(f"  Rust SLOC: {repo_data['rust_sloc']} ({repo_data['proportion']*100:.2f}%)")
    print(f"  Total dependencies: {len(crates)}")
    print(f"  After filtering infrastructure: {len(filtered_crates)}")
    print(f"  Truly unique (used in ≤{threshold} repos): {len(truly_unique)}")

    if truly_unique:
        purposes = []
        unknown = []

        for crate in truly_unique:
            if crate in crate_purposes:
                purposes.append(f"{crate} ({crate_purposes[crate]})")
            else:
                unknown.append(crate)

        if purposes:
            print(f"  Purpose indicators:")
            for p in purposes:
                print(f"    - {p}")

        if unknown:
            print(f"  Other unique: {', '.join(unknown[:5])}")
    else:
        print(f"  No unique dependencies (uses common crates)")

# Summary
print("\n" + "=" * 80)
print("\nAll Crates Used Across ScottyLabs (sorted by usage):")
print("-" * 80)
for crate, count in all_crates.most_common():
    percentage = (count / total_rust_repos) * 100 if total_rust_repos > 0 else 0
    purpose = f" - {crate_purposes[crate]}" if crate in crate_purposes else ""
    print(f"  {crate:<30} {count}/{total_rust_repos} repos ({percentage:>5.1f}%){purpose}")
