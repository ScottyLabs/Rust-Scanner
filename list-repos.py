import os
import requests

def list_repos(group: str):
    token = os.getenv("GITHUB_TOKEN")
    headers = {"User-Agent": "python-repo-lister"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    page = 1
    while True:
        url = f"https://api.github.com/orgs/{group}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        repos = response.json()
        if not repos:
            break

        for repo in repos:
            print(f'"{group}/{repo['name']}",')

        page += 1


if __name__ == "__main__":
    # Change this to your organization or group name
    group = "ScottyLabs"
    list_repos(group)
