use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
struct RepoInfo {
    default_branch: String,
}

#[derive(Debug, Deserialize)]
struct CommitInfo {
    sha: String,
    commit: CommitDetails,
}

#[derive(Debug, Deserialize)]
struct CommitDetails {
    author: AuthorInfo,
    message: String,
}

#[derive(Debug, Deserialize)]
struct AuthorInfo {
    name: String,
    date: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct RepoState {
    last_commit_sha: String,
    last_check_time: String,
    line_count: LineStats,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct LineStats {
    code: u64,
    comments: u64,
    blanks: u64,
    total: u64,
    rust_code: u64,
}

#[derive(Debug, Serialize, Deserialize)]
struct CommitHistory {
    repositories: HashMap<String, RepoState>,
}

struct RepoMonitor {
    github_token: String,
    temp_dir: PathBuf,
    client: reqwest::blocking::Client,
    history: CommitHistory,
    force_check: bool,
    repos_with_changes: Vec<String>,
    total_new_commits: usize,
}

impl RepoMonitor {
    fn new(github_token: String) -> Result<Self, Box<dyn std::error::Error>> {
        let temp_dir = PathBuf::from("temp");
        fs::create_dir_all(&temp_dir)?;

        let client = reqwest::blocking::Client::builder()
            .user_agent("rust-tokei-monitor/1.0")
            .timeout(std::time::Duration::from_secs(300))
            .build()?;

        // Load existing history from artifact if it exists
        let history = if PathBuf::from("commit_history.json").exists() {
            let data = fs::read_to_string("commit_history.json")?;
            serde_json::from_str(&data).unwrap_or_else(|_| CommitHistory {
                repositories: HashMap::new(),
            })
        } else {
            CommitHistory {
                repositories: HashMap::new(),
            }
        };

        let force_check = std::env::var("FORCE_CHECK")
            .unwrap_or_default()
            .to_lowercase() == "true";

        Ok(Self {
            github_token,
            temp_dir,
            client,
            history,
            force_check,
            repos_with_changes: Vec::new(),
            total_new_commits: 0,
        })
    }

    fn print_header(&self) {
        println!("┌────────────────────────────────────────────────────────┐");
        println!("│           LIVE REPOSITORY COMMIT MONITOR               │");
        println!("├────────────────────────────────────────────────────────┤");
        println!("│ Time: {}                │", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));
        println!("│ Mode: {}                                    │",
            if self.force_check { "FORCE CHECK" } else { "INCREMENTAL" });
        println!("└────────────────────────────────────────────────────────┘");
        println!();
    }

    fn get_latest_commit(&self, repo: &str) -> Result<CommitInfo, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}/commits?per_page=1", repo);

        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", self.github_token))
            .header("Accept", "application/vnd.github.v3+json")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to fetch commits: {}", response.status()).into());
        }

        let commits: Vec<CommitInfo> = response.json()?;
        commits.into_iter().next()
            .ok_or("No commits found".into())
    }

    fn get_default_branch(&self, repo: &str) -> Result<String, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}", repo);

        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", self.github_token))
            .header("Accept", "application/vnd.github.v3+json")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to fetch repo info: {}", response.status()).into());
        }

        let repo_info: RepoInfo = response.json()?;
        Ok(repo_info.default_branch)
    }

    fn download_repo(&self, repo: &str, branch: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}/tarball/{}", repo, branch);
        let repo_path = self.temp_dir.join(repo.replace('/', "_"));
        let tarball_path = self.temp_dir.join(format!("{}.tar.gz", repo.replace('/', "_")));

        fs::create_dir_all(&repo_path)?;

        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", self.github_token))
            .header("Accept", "application/vnd.github.v3+json")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to download repo: {}", response.status()).into());
        }

        let bytes = response.bytes()?;
        fs::write(&tarball_path, bytes)?;

        let status = Command::new("tar")
            .args(&[
                "-xzf",
                tarball_path.to_str().unwrap(),
                "-C",
                repo_path.to_str().unwrap(),
                "--strip-components=1",
            ])
            .status()?;

        if !status.success() {
            return Err("Failed to extract tarball".into());
        }

        fs::remove_file(tarball_path)?;
        Ok(repo_path)
    }

    fn count_lines(&self, path: &PathBuf) -> Result<LineStats, Box<dyn std::error::Error>> {
        let output = Command::new("tokei")
            .arg(path)
            .arg("--output")
            .arg("json")
            .output()?;

        if !output.status.success() {
            return Err("Failed to run tokei".into());
        }

        let json_str = String::from_utf8(output.stdout)?;
        let json: Value = serde_json::from_str(&json_str)?;

        let total = json.get("Total")
            .ok_or("No Total field in tokei output")?;

        let code = total.get("code").and_then(|v| v.as_u64()).unwrap_or(0);
        let comments = total.get("comments").and_then(|v| v.as_u64()).unwrap_or(0);
        let blanks = total.get("blanks").and_then(|v| v.as_u64()).unwrap_or(0);
        let total_lines = code + comments + blanks;

        // Extract Rust-specific code lines
        let rust_code = json.get("Rust")
            .and_then(|rust| rust.get("code"))
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        Ok(LineStats {
            code,
            comments,
            blanks,
            total: total_lines,
            rust_code,
        })
    }

    fn format_number(n: u64) -> String {
        n.to_string()
            .as_bytes()
            .rchunks(3)
            .rev()
            .map(std::str::from_utf8)
            .collect::<Result<Vec<&str>, _>>()
            .unwrap()
            .join(",")
    }

    fn monitor_repo(&mut self, repo: &str) -> Result<(), Box<dyn std::error::Error>> {
        println!("╔════════════════════════════════════════════════════════╗");
        println!("║ 📦 Repository: {:<42} ║", repo);
        println!("╚════════════════════════════════════════════════════════╝");

        let latest_commit = self.get_latest_commit(repo)?;
        let last_state = self.history.repositories.get(repo);

        // Check if there are new commits
        let has_new_commits = last_state
            .map(|state| state.last_commit_sha != latest_commit.sha)
            .unwrap_or(true);

        if !has_new_commits && !self.force_check {
            println!("✅ No new commits since last check");
            if let Some(state) = last_state {
                println!("   📍 Last commit: {}", &state.last_commit_sha[..7]);
                println!("   📊 Code lines: {}", Self::format_number(state.line_count.code));
                println!("   🕐 Last checked: {}", state.last_check_time);
            }
            println!();
            return Ok(());
        }

        if has_new_commits {
            println!("🆕 NEW COMMIT DETECTED!");
            println!("├─ SHA:     {}", &latest_commit.sha[..10]);
            println!("├─ Author:  {}", latest_commit.commit.author.name);
            println!("├─ Date:    {}", latest_commit.commit.author.date);
            println!("└─ Message: {}", latest_commit.commit.message.lines().next().unwrap_or(""));
            println!();

            self.repos_with_changes.push(repo.to_string());
            self.total_new_commits += 1;
        } else {
            println!("🔄 Force check enabled - analyzing anyway");
            println!();
        }

        println!("⬇️  Downloading repository...");
        let branch = self.get_default_branch(repo)?;
        let repo_path = self.download_repo(repo, &branch)?;

        println!("🔢 Counting lines with tokei...");
        let stats = self.count_lines(&repo_path)?;

        println!();
        println!("┌─────────────── CURRENT STATISTICS ───────────────┐");
        println!("│ Code:     {:>12} lines                      │", Self::format_number(stats.code));
        println!("│ Comments: {:>12} lines                      │", Self::format_number(stats.comments));
        println!("│ Blanks:   {:>12} lines                      │", Self::format_number(stats.blanks));
        println!("│ ─────────────────────────────────────────────── │");
        println!("│ TOTAL:    {:>12} lines                      │", Self::format_number(stats.total));
        if stats.rust_code > 0 {
            println!("│ Rust:     {:>12} lines                      │", Self::format_number(stats.rust_code));
        }
        println!("└───────────────────────────────────────────────────┘");

        // Show change if we have previous data
        if let Some(old_state) = last_state {
            let code_diff = stats.code as i64 - old_state.line_count.code as i64;
            let total_diff = stats.total as i64 - old_state.line_count.total as i64;
            let rust_diff = stats.rust_code as i64 - old_state.line_count.rust_code as i64;

            println!();
            println!("┌──────────────── CHANGES ─────────────────────────┐");
            println!("│ Code lines:  {:>12} ({:>+10})            │",
                Self::format_number(stats.code),
                if code_diff >= 0 { format!("+{}", code_diff) } else { code_diff.to_string() });
            println!("│ Total lines: {:>12} ({:>+10})            │",
                Self::format_number(stats.total),
                if total_diff >= 0 { format!("+{}", total_diff) } else { total_diff.to_string() });
            if stats.rust_code > 0 || old_state.line_count.rust_code > 0 {
                println!("│ Rust lines:  {:>12} ({:>+10})            │",
                    Self::format_number(stats.rust_code),
                    if rust_diff >= 0 { format!("+{}", rust_diff) } else { rust_diff.to_string() });
            }
            println!("└───────────────────────────────────────────────────┘");
        }

        // Update history
        self.history.repositories.insert(
            repo.to_string(),
            RepoState {
                last_commit_sha: latest_commit.sha,
                last_check_time: chrono::Local::now().to_rfc3339(),
                line_count: stats,
            },
        );

        fs::remove_dir_all(repo_path)?;
        println!();
        Ok(())
    }

    fn print_summary(&self) {
        println!("╔════════════════════════════════════════════════════════╗");
        println!("║                    SUMMARY                             ║");
        println!("╠════════════════════════════════════════════════════════╣");
        println!("║ Repositories monitored: {:>3}                            ║", self.history.repositories.len());
        println!("║ New commits found:      {:>3}                            ║", self.total_new_commits);
        println!("║ Repositories changed:   {:>3}                            ║", self.repos_with_changes.len());
        println!("╚════════════════════════════════════════════════════════╝");

        if !self.repos_with_changes.is_empty() {
            println!();
            println!("Repositories with new commits:");
            for repo in &self.repos_with_changes {
                println!("  ✓ {}", repo);
            }
        }
    }

    fn save_history(&self) -> Result<(), Box<dyn std::error::Error>> {
        let json = serde_json::to_string_pretty(&self.history)?;
        fs::write("commit_history.json", json)?;
        println!();
        println!("💾 State saved for next run");
        Ok(())
    }

    fn cleanup(&self) {
        let _ = fs::remove_dir_all(&self.temp_dir);
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let github_token = std::env::var("GITHUB_TOKEN")
        .expect("GITHUB_TOKEN environment variable not set");

    // Repositories to monitor
    let repositories = vec![
        "ScottyLabs/printscottylabs-website",
        "ScottyLabs/cmupy",
        "ScottyLabs/cmurb",
        "ScottyLabs/wdw",
        "ScottyLabs/tartanhacks-old",
        "ScottyLabs/dining-api",
        "ScottyLabs/course-api",
        "ScottyLabs/blog",
        "ScottyLabs/wdw-htmlcss",
        "ScottyLabs/IntroToSwift",
        "ScottyLabs/print-ios",
        "ScottyLabs/print-android",
        "ScottyLabs/print",
        "ScottyLabs/HackerHelp",
        "ScottyLabs/storage-api",
        "ScottyLabs/HELPq",
        "ScottyLabs/pausch-api",
        "ScottyLabs/EmailAutomation",
        "ScottyLabs/ScottyLabs-Email-Automation",
        "ScottyLabs/go",
        "ScottyLabs/TartanHacksRegistrationv2",
        "ScottyLabs/api-website",
        "ScottyLabs/social-action-frontend",
        "ScottyLabs/social-action-backend",
        "ScottyLabs/cmu_lost_and_found",
        "ScottyLabs/shuttle-api",
        "ScottyLabs/wdw-node",
        "ScottyLabs/wdw-react",
        "ScottyLabs/quick-clicks",
        "ScottyLabs/lost-and-found-v2",
        "ScottyLabs/tartanhacks-dashboard-api",
        "ScottyLabs/print-status-map",
        "ScottyLabs/TH-Bot",
        "ScottyLabs/pausch-ui-backend",
        "ScottyLabs/scottypass",
        "ScottyLabs/plane",
        "ScottyLabs/auto-onboard",
        "ScottyLabs/cmueats",
        "ScottyLabs/cmucourses",
        "ScottyLabs/passlink-server",
        "ScottyLabs/passlink",
        "ScottyLabs/web",
        "ScottyLabs/moneyprinter",
        "ScottyLabs/scottylol",
        "ScottyLabs/lost-and-found",
        "ScottyLabs/roomies",
        "ScottyLabs/Go-v2",
        "ScottyLabs/tartanhack_dashboard_v3",
        "ScottyLabs/pdf-23",
        "ScottyLabs/pdf-css",
        "ScottyLabs/pdf-html",
        "ScottyLabs/lend-it-test",
        "ScottyLabs/tartanhacks_dashboard_v4",
        "ScottyLabs/nova",
        "ScottyLabs/SuperStarter",
        "ScottyLabs/nova-sdk-swift",
        "ScottyLabs/nova-sdk-server",
        "ScottyLabs/nova-js-sdk",
        "ScottyLabs/nova-python-sdk",
        "ScottyLabs/cmucal",
        "ScottyLabs/cmumaps",
        "ScottyLabs/cmu-purity-test",
        "ScottyLabs/courses-backend",
        "ScottyLabs/.github",
        "ScottyLabs/akita",
        "ScottyLabs/shepherd",
        "ScottyLabs/documentation",
        "ScottyLabs/governance",
        "ScottyLabs/python-template",
        "ScottyLabs/mcp",
        "ScottyLabs/scottylabs.org",
        "ScottyLabs/cmumaps-rust",
        "ScottyLabs/cmumaps-data-acquisitor",
        "ScottyLabs/beagle",
        "ScottyLabs/mem-cho-cmueats-slack-bot",
        "ScottyLabs/wiki-redirect",
        "ScottyLabs/authentik",
        "ScottyLabs/corgi",
        "ScottyLabs/sp",
        "ScottyLabs/quest",
        "ScottyLabs/applications",
        "ScottyLabs/courses-frontend",
        "ScottyLabs/courses-data",
        "ScottyLabs/terrier-submission",
        "ScottyLabs/mcp-server",
        "ScottyLabs/cmugpt-finetuning",
        "ScottyLabs/dalmatian",
        "ScottyLabs/nova-demo-app-25",
        "ScottyLabs/terrier",
        "ScottyLabs/cmugpt-backend",
        "ScottyLabs/devops-config",
        "ScottyLabs/doberman",
        "ScottyLabs/Rust-Scanner",
        "ScottyLabs/coffee-chats",
    ];

    let mut monitor = RepoMonitor::new(github_token)?;
    monitor.print_header();

    for repo in &repositories {
        match monitor.monitor_repo(repo) {
            Ok(_) => {}
            Err(e) => {
                eprintln!("❌ Error monitoring {}: {}", repo, e);
                eprintln!();
            }
        }
    }

    monitor.print_summary();
    monitor.save_history()?;
    monitor.cleanup();

    println!();
    println!("✅ Monitoring cycle complete!");
    Ok(())
}
