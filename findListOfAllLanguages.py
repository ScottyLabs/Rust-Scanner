import json
import os
from collections import defaultdict

def get_language_from_filename(filename):
    """Infer language from file extension (basic mapping)."""
    ext_map = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".java": "Java",
        ".cpp": "C++",
        ".c": "C",
        ".cs": "C#",
        ".rb": "Ruby",
        ".go": "Go",
        ".php": "PHP",
        ".rs": "Rust",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".m": "Objective-C",
        ".sh": "Shell",
        ".html": "HTML",
        ".css": "CSS",
        ".json": "JSON",
        ".xml": "XML",
        ".yml": "YAML",
        ".yaml": "YAML",
    }
    _, ext = os.path.splitext(filename.lower())
    return ext_map.get(ext, "Other")

def process_commit_logs(json_files):
    file_counts = defaultdict(int)   # language -> # of files
    repo_counts = defaultdict(set)   # language -> set of repos

    for filepath in json_files:
        with open(filepath, "r") as f:
            data = json.load(f)
        
        repo_name = data.get("repo_name", os.path.basename(filepath))
        
        # Assume commit JSON has a "commits" field, each with a "files" list
        for commit in data.get("commits", []):
            for file in commit.get("files", []):
                filename = file.get("filename") if isinstance(file, dict) else file
                language = get_language_from_filename(filename)
                
                file_counts[language] += 1
                repo_counts[language].add(repo_name)
    
    # Convert to lists of tuples
    file_count_list = [(lang, count) for lang, count in file_counts.items()]
    repo_count_list = [(lang, len(repos)) for lang, repos in repo_counts.items()]
    
    # Sort for readability
    file_count_list.sort(key=lambda x: x[1], reverse=True)
    repo_count_list.sort(key=lambda x: x[1], reverse=True)
    
    return {
        "files_per_language": file_count_list,
        "repos_per_language": repo_count_list
    }


if __name__ == "__main__":
    json_files = ["repo1_commits.json", "repo2_commits.json"]  # replace with your files
    result = process_commit_logs(json_files)
    
    # Save to JSON
    with open("language_summary.json", "w") as out:
        json.dump(result, out, indent=4)