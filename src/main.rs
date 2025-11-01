use std::fs;
use std::path::PathBuf;
use std::process::Command;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
struct RepoInfo {
    default_branch: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct TokeiLanguage {
    blanks: u64,
    code: u64,
    comments: u64,
    lines: u64,
    #[serde(default)]
    files: u64,
}

#[derive(Debug, Serialize, Deserialize)]
struct TokeiStats {
    #[serde(rename = "Total")]
    total: TokeiLanguage,
    #[serde(flatten)]
    languages: std::collections::HashMap<String, TokeiLanguage>,
}

struct RepoAnalyzer {
    github_token: String,
    temp_dir: PathBuf,
}

impl RepoAnalyzer {
    fn new(github_token: String) -> Self {
        let temp_dir = PathBuf::from("temp");
        fs::create_dir_all(&temp_dir).expect("Failed to create temp directory");

        Self {
            github_token,
            temp_dir,
        }
    }

    fn get_default_branch(&self, repo: &str) -> Result<String, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}", repo);

        let client = reqwest::blocking::Client::new();
        let response = client
            .get(&url)
            .header("Authorization", format!("token {}", self.github_token))
            .header("User-Agent", "rust-tokei-counter")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to fetch repo info: {}", response.status()).into());
        }

        let repo_info: RepoInfo = response.json()?;
        Ok(repo_info.default_branch)
    }

    fn download_repo(&self, repo: &str, branch: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}/tarball/{}", repo, branch);
        let repo_path = self.temp_dir.join(repo.replace("/", "_"));
        let tarball_path = self.temp_dir.join(format!("{}.tar.gz", repo.replace("/", "_")));

        fs::create_dir_all(&repo_path)?;

        println!("⬇️  Downloading {}...", repo);

        let client = reqwest::blocking::Client::new();
        let response = client
            .get(&url)
            .header("Authorization", format!("token {}", self.github_token))
            .header("User-Agent", "rust-tokei-counter")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to download repo: {}", response.status()).into());
        }

        let bytes = response.bytes()?;
        fs::write(&tarball_path, bytes)?;

        println!("📂 Extracting files...");

        // Extract tarball
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

        // Cleanup tarball
        fs::remove_file(tarball_path)?;

        Ok(repo_path)
    }

    fn run_tokei(&self, path: &PathBuf) -> Result<TokeiStats, Box<dyn std::error::Error>> {
        println!("🔢 Counting lines...");

        let output = Command::new("tokei")
            .arg(path)
            .arg("--output")
            .arg("json")
            .output()?;

        if !output.status.success() {
            return Err("Failed to run tokei".into());
        }

        let json_str = String::from_utf8(output.stdout)?;
        let stats: TokeiStats = serde_json::from_str(&json_str)?;

        Ok(stats)
    }

    fn print_tokei_output(&self, path: &PathBuf) -> Result<(), Box<dyn std::error::Error>> {
        let output = Command::new("tokei")
            .arg(path)
            .output()?;

        if !output.status.success() {
            return Err("Failed to run tokei".into());
        }

        println!("{}", String::from_utf8_lossy(&output.stdout));
        Ok(())
    }

    fn analyze_repo(&self, repo: &str) -> Result<TokeiStats, Box<dyn std::error::Error>> {
        println!("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        println!("📦 Repository: {}", repo);
        println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

        let branch = self.get_default_branch(repo)?;
        println!("Branch: {}", branch);
        println!();

        let repo_path = self.download_repo(repo, &branch)?;

        // Print detailed output
        self.print_tokei_output(&repo_path)?;

        // Get JSON stats
        let stats = self.run_tokei(&repo_path)?;

        println!("\n📊 Summary:");
        println!("   Code lines: {}", stats.total.code);
        println!("   Comments: {}", stats.total.comments);
        println!("   Blanks: {}", stats.total.blanks);
        println!("   Total lines: {}", stats.total.lines);

        // Cleanup
        fs::remove_dir_all(repo_path)?;

        Ok(stats)
    }

    fn cleanup(&self) {
        let _ = fs::remove_dir_all(&self.temp_dir);
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Get GitHub token from environment
    let github_token = std::env::var("GITHUB_TOKEN")
        .expect("GITHUB_TOKEN environment variable not set");

    // Define repositories to analyze
    let repositories = vec![
        "rust-lang/rust",
    ];

    let analyzer = RepoAnalyzer::new(github_token);

    println!("======================================");
    println!("LINE COUNT REPORT");
    println!("Generated: {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));
    println!("======================================");

    let mut total_code = 0u64;
    let mut total_lines = 0u64;
    let mut successful_repos = 0;

    for repo in &repositories {
        match analyzer.analyze_repo(repo) {
            Ok(stats) => {
                total_code += stats.total.code;
                total_lines += stats.total.lines;
                successful_repos += 1;
            }
            Err(e) => {
                eprintln!("❌ Error analyzing {}: {}", repo, e);
            }
        }
    }

    println!("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("📈 TOTALS");
    println!("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    println!("Repositories analyzed: {}", successful_repos);
    println!("Total lines of code: {}", total_code);
    println!("Total lines: {}", total_lines);
    println!("======================================");

    analyzer.cleanup();

    Ok(())
}
