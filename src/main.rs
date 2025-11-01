use std::fs;
use std::path::PathBuf;
use std::process::Command;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
struct RepoInfo {
    default_branch: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct TokeiLanguage {
    blanks: u64,
    code: u64,
    comments: u64,
    #[serde(default)]
    lines: u64,
    #[serde(default)]
    inaccurate: bool,
}

struct RepoAnalyzer {
    github_token: String,
    temp_dir: PathBuf,
    client: reqwest::blocking::Client,
}

impl RepoAnalyzer {
    fn new(github_token: String) -> Result<Self, Box<dyn std::error::Error>> {
        let temp_dir = PathBuf::from("temp");
        fs::create_dir_all(&temp_dir)?;

        let client = reqwest::blocking::Client::builder()
            .user_agent("rust-tokei-counter/1.0")
            .timeout(std::time::Duration::from_secs(300))
            .build()?;

        Ok(Self {
            github_token,
            temp_dir,
            client,
        })
    }

    fn get_default_branch(&self, repo: &str) -> Result<String, Box<dyn std::error::Error>> {
        let url = format!("https://api.github.com/repos/{}", repo);

        println!("🔍 Fetching repo info...");

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

        println!("⬇️  Downloading repository...");

        let response = self.client
            .get(&url)
            .header("Authorization", format!("Bearer {}", self.github_token))
            .header("Accept", "application/vnd.github.v3+json")
            .send()?;

        if !response.status().is_success() {
            return Err(format!("Failed to download repo: {}", response.status()).into());
        }

        let bytes = response.bytes()?;
        println!("   Downloaded {} MB", bytes.len() / 1_000_000);
        fs::write(&tarball_path, bytes)?;

        println!("📂 Extracting files...");

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

    fn get_tokei_stats(&self, path: &PathBuf) -> Result<(u64, u64, u64, u64), Box<dyn std::error::Error>> {
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
        let json: Value = serde_json::from_str(&json_str)?;

        // Extract Total stats
        let total = json.get("Total")
            .ok_or("No Total field in tokei output")?;

        let code = total.get("code")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        let comments = total.get("comments")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        let blanks = total.get("blanks")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        let lines = code + comments + blanks;

        Ok((code, comments, blanks, lines))
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

    fn analyze_repo(&self, repo: &str) -> Result<(u64, u64, u64, u64), Box<dyn std::error::Error>> {
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
        let (code, comments, blanks, lines) = self.get_tokei_stats(&repo_path)?;

        println!("\n📊 Summary:");
        println!("   Code lines: {}", code);
        println!("   Comments: {}", comments);
        println!("   Blanks: {}", blanks);
        println!("   Total lines: {}", lines);

        // Cleanup
        fs::remove_dir_all(repo_path)?;

        Ok((code, comments, blanks, lines))
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
        "tokio-rs/tokio",
        "actix/actix-web",
        "serde-rs/serde",
    ];

    let analyzer = RepoAnalyzer::new(github_token)?;

    println!("======================================");
    println!("LINE COUNT REPORT");
    println!("Generated: {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));
    println!("======================================");

    let mut total_code = 0u64;
    let mut total_comments = 0u64;
    let mut total_blanks = 0u64;
    let mut total_lines = 0u64;
    let mut successful_repos = 0;

    for repo in &repositories {
        match analyzer.analyze_repo(repo) {
            Ok((code, comments, blanks, lines)) => {
                total_code += code;
                total_comments += comments;
                total_blanks += blanks;
                total_lines += lines;
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
    println!("Total comments: {}", total_comments);
    println!("Total blank lines: {}", total_blanks);
    println!("Total lines: {}", total_lines);
    println!("======================================");

    analyzer.cleanup();

    Ok(())
}
