use polars::prelude::*;
use serde::{Deserialize, Serialize};
use clap::{Parser, ValueEnum};
use std::fs::{self, File};
use std::io::{self, Write};
use chrono::Utc;

const YAHOO_AUTH_URL:  &str = "https://api.login.yahoo.com/oauth2/request_auth";
const YAHOO_TOKEN_URL: &str = "https://api.login.yahoo.com/oauth2/get_token";
const YAHOO_API_BASE:  &str = "https://fantasysports.yahooapis.com/fantasy/v2";

// ---------------------------------------------------------------------------
// Config structs — deserialized from config.toml at startup
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
pub struct Config {
    pub yahoo:        YahooConfig,
    pub mlb:          MlbConfig,
    pub park_factors: ParkFactorsConfig,
}

#[derive(Deserialize)]
pub struct YahooConfig {
    pub client_id:     String,
    pub client_secret: String,
    pub league_id:     String,
    pub league_key:    String,
    pub redirect_uri:  String,
    pub token_path:    String,
    pub season:        u32,
}

#[derive(Deserialize)]
pub struct MlbConfig {
    pub base_url:                    String,
    pub probable_starters_days_ahead: u32,
}

#[derive(Deserialize)]
pub struct ParkFactorsConfig {
    pub csv_path: String,
}

// ---------------------------------------------------------------------------
// Token cache — stored as JSON at token_path between runs
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize)]
pub struct TokenCache {
    pub access_token:  String,
    pub refresh_token: String,
    pub expires_at:    i64,   // Unix timestamp — when the access token expires
}

// ---------------------------------------------------------------------------
// MatchupInfo
// ---------------------------------------------------------------------------
pub struct MatchupInfo {
    pub opponent_key: String,
    pub opponent_name: String,
    pub current_week:  u32,
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Parser)]
pub struct Cli {
    #[arg(long, value_enum)]
    pub mode: Mode,

    #[arg(long)]
    pub transactions: Option<String>,

    #[arg(long)]
    pub position: Option<String>,
}

#[derive(ValueEnum, Clone)]
pub enum Mode {
    Full,
    Midweek,
    Adhoc,
}

// ---------------------------------------------------------------------------
// Config loader
// ---------------------------------------------------------------------------

pub fn load_config(path: &str) -> Config {
    let contents = std::fs::read_to_string(path).expect("Could not read config.toml");
    toml::from_str(&contents).expect("Invalid config.toml format")
}

// ---------------------------------------------------------------------------
// Token path helper — expands ~ to $HOME
// ---------------------------------------------------------------------------

fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
        format!("{}/{}", home, &path[2..])
    } else {
        path.to_string()
    }
}

// ---------------------------------------------------------------------------
// Token cache I/O
// ---------------------------------------------------------------------------

fn read_token_cache(path: &str) -> Option<TokenCache> {
    let expanded = expand_tilde(path);
    let contents = fs::read_to_string(&expanded).ok()?;
    serde_json::from_str(&contents).ok()
}

fn write_token_cache(path: &str, tokens: &TokenCache) -> anyhow::Result<()> {
    let expanded = expand_tilde(path);

    // Create parent directory if it doesn't exist
    if let Some(parent) = std::path::Path::new(&expanded).parent() {
        fs::create_dir_all(parent)?;
    }

    let json = serde_json::to_string_pretty(tokens)?;
    fs::write(&expanded, json)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// OAuth flow
// ---------------------------------------------------------------------------

/// Step 1 of the OAuth flow — build the URL the user must visit in their browser.
/// Yahoo will redirect them to redirect_uri with ?code=XXXX after they approve.
fn build_auth_url(config: &YahooConfig) -> String {
    format!(
        "{}?client_id={}&redirect_uri={}&response_type=code&language=en-us&scope=fspt-r",
        YAHOO_AUTH_URL,
        config.client_id,
        urlencoding::encode(&config.redirect_uri)
    )
}

/// Step 2 — exchange the authorization code for access + refresh tokens.
/// Yahoo expects Basic auth (base64 of client_id:client_secret) and a
/// form-encoded body. reqwest handles both natively.
async fn exchange_code_for_tokens(
    config: &YahooConfig,
    code: &str,
    client: &reqwest::Client,
) -> anyhow::Result<TokenCache> {
    let response = client
        .post(YAHOO_TOKEN_URL)
        .basic_auth(&config.client_id, Some(&config.client_secret))
        .form(&[
            ("grant_type",   "authorization_code"),
            ("code",         code),
            ("redirect_uri", &config.redirect_uri),
        ])
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    let access_token  = response["access_token"].as_str()
        .ok_or_else(|| anyhow::anyhow!("missing access_token in response"))?.to_string();
    let refresh_token = response["refresh_token"].as_str()
        .ok_or_else(|| anyhow::anyhow!("missing refresh_token in response"))?.to_string();
    let expires_in    = response["expires_in"].as_i64().unwrap_or(3600);
    let expires_at    = Utc::now().timestamp() + expires_in;

    Ok(TokenCache { access_token, refresh_token, expires_at })
}

/// Refresh an expired access token using the stored refresh token.
async fn refresh_tokens(
    config: &YahooConfig,
    refresh_token: &str,
    client: &reqwest::Client,
) -> anyhow::Result<TokenCache> {
    let response = client
        .post(YAHOO_TOKEN_URL)
        .basic_auth(&config.client_id, Some(&config.client_secret))
        .form(&[
            ("grant_type",    "refresh_token"),
            ("refresh_token", refresh_token),
            ("redirect_uri",  &config.redirect_uri),
        ])
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    let access_token  = response["access_token"].as_str()
        .ok_or_else(|| anyhow::anyhow!("missing access_token in refresh response"))?.to_string();
    let new_refresh   = response["refresh_token"].as_str()
        .unwrap_or(refresh_token).to_string();
    let expires_in    = response["expires_in"].as_i64().unwrap_or(3600);
    let expires_at    = Utc::now().timestamp() + expires_in;

    Ok(TokenCache { access_token, refresh_token: new_refresh, expires_at })
}

/// Entry point for all API calls — returns a valid access token.
/// Handles first-run auth, refresh, and cache reads automatically.
async fn get_valid_token(
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<String> {
    let token_path = &config.token_path;

    // Check cache first
    if let Some(cached) = read_token_cache(token_path) {
        let now = Utc::now().timestamp();

        // Token still valid (with 60s buffer)
        if cached.expires_at > now + 60 {
            println!("Using cached token (expires in {}s)", cached.expires_at - now);
            return Ok(cached.access_token);
        }

        // Token expired — refresh it
        println!("Token expired, refreshing...");
        let refreshed = refresh_tokens(config, &cached.refresh_token, client).await?;
        write_token_cache(token_path, &refreshed)?;
        println!("Token refreshed and saved.");
        return Ok(refreshed.access_token);
    }

    // No cache — run first-time authorization flow
    println!("\nNo token cache found. Starting first-time authorization.");
    println!("\nOpen this URL in your browser and approve access:\n");
    println!("{}\n", build_auth_url(config));
    print!("Paste the authorization code here: ");
    io::stdout().flush()?;

    let mut code = String::new();
    io::stdin().read_line(&mut code)?;
    let code = code.trim();

    let tokens = exchange_code_for_tokens(config, code, client).await?;
    write_token_cache(token_path, &tokens)?;
    println!("Token saved to {}", token_path);

    Ok(tokens.access_token)
}

// ---------------------------------------------------------------------------
// Smoke test — fetch league metadata to confirm auth works
// ---------------------------------------------------------------------------

async fn fetch_league_metadata(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<()> {
    let url = format!(
        "{}/league/{}?format=json",
        YAHOO_API_BASE,
        config.league_key
    );

    let response = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    //println!("\nLeague metadata:\n{}", serde_json::to_string_pretty(&response)?);
    Ok(())
}

// ---------------------------------------------------------------------------
// Park factors ingestion
// ---------------------------------------------------------------------------

fn ingest_park_factors(csv_path: &str, parquet_path: &str) -> anyhow::Result<()> {
    let mut df = LazyCsvReader::new(csv_path)
        .with_has_header(true)
        .finish()?
        .collect()?;

    let mut file = File::create(parquet_path)?;
    ParquetWriter::new(&mut file).finish(&mut df)?;

    println!("Park factors written to {}", parquet_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// Pybaseball shim
// ---------------------------------------------------------------------------

fn run_pybaseball_shim(players: &[&str], season: u32, output_path: &str, min_pa: u32, min_bf: u32,) -> anyhow::Result<()> {
    let players_arg = players.join(",");

    let status = std::process::Command::new("python3")
        .arg("pybaseball_shim.py")
        .arg("--players").arg(&players_arg)
        .arg("--season").arg(season.to_string())
        .arg("--output").arg(output_path)
        .arg("--min_pa").arg(min_pa.to_string())
        .arg("--min_bf").arg(min_bf.to_string())
        .status()?;

    if !status.success() {
        anyhow::bail!("pybaseball shim exited with status: {}", status);
    }

    println!("pybaseball shim wrote to {}", output_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// Get my roster
// ---------------------------------------------------------------------------
async fn get_my_team_key(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<String> {
    // Extract game_key from league_key
    let game_key = config.league_key.split('.').next().ok_or_else(|| anyhow::anyhow!("Invalid league_key format"))?;

    let url = format!(
        "{}/users;use_login=1/games;game_keys={}/teams?format=json",
        YAHOO_API_BASE, game_key
    );

    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    // Yahoo nests responses deeply - navigate to the teams object
    let teams = &resp["fantasy_content"]["users"]["0"]["user"][1]["games"]["0"]["game"][1]["teams"];

    let count = teams["count"].as_u64().unwrap_or(0);

    for i in 0..count {
        let team_arr = &teams[i.to_string()]["team"][0];
        if let Some(arr) = team_arr.as_array() {
            for item in arr {
                if let Some(key) = item["team_key"].as_str() {
                    return Ok(key.to_string());
                }
            }
        }
    }

    anyhow::bail!("Could not find team key in Yahoo API response")
}

async fn fetch_my_roster(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<Vec<String>> {
    let team_key = get_my_team_key(token, config, client).await?;
    println!("My team key: {}", team_key);

    let url = format!(
        "{}/team/{}/roster?format=json",
        YAHOO_API_BASE, team_key
    );

    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    let players = &resp["fantasy_content"]["team"][1]["roster"]["0"]["players"];
    let count = players["count"].as_u64().unwrap_or(0);

    let mut player_strings: Vec<String> = Vec::new();

    for i in 0..count {
        let player_info = &players[i.to_string()]["player"][0];

        let mut name       = String::new();
        let mut position   = String::new();
        let mut team_abbr  = String::new();

        if let Some(arr) = player_info.as_array() {
            for item in arr {
                // Player full name
                if let Some(n) = item["name"]["full"].as_str() {
                    name = n.to_string();
                }
                // MLB team abbreviation (e.g. "nym" → stored as "NYM")
                if let Some(a) = item["editorial_team_abbr"].as_str() {
                    team_abbr = a.to_uppercase();
                }
                // First eligible position — enough for batting vs pitching split
                if let Some(positions) = item["eligible_positions"].as_array() {
                    if let Some(first) = positions.first() {
                        if let Some(pos) = first["position"].as_str() {
                            position = pos.to_string();
                        }
                    }
                }
            }
        }

        if !name.is_empty() && !position.is_empty() {
            player_strings.push(format!("{}:{}:{}", name, position, team_abbr));
        }
    }

    println!("Roster ({} players):", player_strings.len());
    Ok(player_strings)
}

// ---------------------------------------------------------------------------
// Fetch and save roster positions to CSV
// ---------------------------------------------------------------------------
async fn fetch_and_save_roster_positions(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
    team_key: &str,
    output_path: &str,
) -> anyhow::Result<()> {
    let url = format!(
        "{}/team/{}/roster?format=json",
        YAHOO_API_BASE, team_key
    );
    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    let players = &resp["fantasy_content"]["team"][1]["roster"]["0"]["players"];
    let count = players["count"].as_u64().unwrap_or(0);

    let mut v_player_key: Vec<String> = Vec::new();
    let mut v_name:       Vec<String> = Vec::new();
    let mut v_positions:  Vec<String> = Vec::new();

    for i in 0..count {
        let player_info = &players[i.to_string()]["player"][0];

        let mut player_key = String::new();
        let mut name       = String::new();
        let mut positions: Vec<String> = Vec::new();

        if let Some(arr) = player_info.as_array() {
            for item in arr {
                if let Some(k) = item["player_key"].as_str() {
                    player_key = k.to_string();
                }
                if let Some(n) = item["name"]["full"].as_str() {
                    name = n.to_string();
                }
                if let Some(pos_arr) = item["eligible_positions"].as_array() {
                    for p in pos_arr {
                        if let Some(pos) = p["position"].as_str() {
                            if pos != "BN" && pos != "DL" && pos != "NA" {
                                positions.push(pos.to_string());
                            }
                        }
                    }
                }
            }
        }

        if !name.is_empty() {
            v_player_key.push(player_key);
            v_name.push(name);
            v_positions.push(positions.join("|"));
        }
    }

    let mut file = File::create(output_path)?;
    writeln!(file, "player_key,name,eligible_positions")?;
    for i in 0..v_name.len() {
        writeln!(file, "\"{}\",\"{}\",\"{}\"",
            v_player_key[i], v_name[i], v_positions[i])?;
    }

    println!("Roster positions: {} players written to {}", v_name.len(), output_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// Get free agent pool
// ---------------------------------------------------------------------------
async fn fetch_free_agents(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<Vec<String>> {
    let page_size: u64 = 25;
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut combined: Vec<String> = Vec::new();

    // --- Pool 1: Top 75 batters by overall rank ---
    // No position filter — exclude P entries in code
    println!("\nFetching batter pool (top 75 by overall rank)...");
    let mut start: u64 = 0;
    let mut batter_count = 0;
    while batter_count < 75 {
        let url = format!(
            "{}/league/{}/players;status=FA;sort=OR;start={};count={}?format=json",
            YAHOO_API_BASE, config.league_key, start, page_size
        );
        let resp = client.get(&url).bearer_auth(token).send().await?
            .error_for_status()?.json::<serde_json::Value>().await?;

        let players = &resp["fantasy_content"]["league"][1]["players"];
        let mut players_on_page: u64 = 0;
        for i in 0..page_size {
            if players[i.to_string()].is_null() { break; }
            players_on_page += 1;
        }
        if players_on_page == 0 { break; }

        for i in 0..players_on_page {
            let player_info = &players[i.to_string()]["player"][0];
            let mut name      = String::new();
            let mut position  = String::new();
            let mut status    = String::new();
            let mut team_abbr = String::new();

            if let Some(arr) = player_info.as_array() {
                for item in arr {
                    if let Some(n) = item["name"]["full"].as_str() {
                        name = n.to_string();
                    }
                    if let Some(s) = item["status"].as_str() {
                        status = s.to_string();
                    }
                    if let Some(a) = item["editorial_team_abbr"].as_str() {
                        team_abbr = a.to_uppercase();
                    }
                    if let Some(positions) = item["eligible_positions"].as_array() {
                        if let Some(first) = positions.first() {
                            if let Some(pos) = first["position"].as_str() {
                                position = pos.to_string();
                            }
                        }
                    }
                }
            }

            let is_active = status.is_empty() || status == "active";
            let is_pitcher = position == "P";

            if !name.is_empty() && !position.is_empty() && is_active && !is_pitcher {
                if seen.insert(name.clone()) {
                    println!("  [BAT] {} | {} | {}", name, position, team_abbr);
                    combined.push(format!("{}:{}:{}", name, position, team_abbr));
                    batter_count += 1;
                }
            }
        }

        if players_on_page < page_size { break; }
        start += page_size;
    }
    println!("  Batter pool: {} players", batter_count);

    // --- Pool 2: Top 50 pitchers by innings pitched ---
    println!("\nFetching SP pool (top 50 by innings pitched)...");
    start = 0;
    let mut sp_count = 0;
    while sp_count < 50 {
        let url = format!(
            "{}/league/{}/players;status=FA;position=P;sort=S_IP;start={};count={}?format=json",
            YAHOO_API_BASE, config.league_key, start, page_size
        );
        let resp = client.get(&url).bearer_auth(token).send().await?
            .error_for_status()?.json::<serde_json::Value>().await?;

        let players = &resp["fantasy_content"]["league"][1]["players"];
        let mut players_on_page: u64 = 0;
        for i in 0..page_size {
            if players[i.to_string()].is_null() { break; }
            players_on_page += 1;
        }
        if players_on_page == 0 { break; }

        for i in 0..players_on_page {
            let player_info = &players[i.to_string()]["player"][0];
            let mut name      = String::new();
            let mut position  = String::new();
            let mut status    = String::new();
            let mut team_abbr = String::new();

            if let Some(arr) = player_info.as_array() {
                for item in arr {
                    if let Some(n) = item["name"]["full"].as_str() {
                        name = n.to_string();
                    }
                    if let Some(s) = item["status"].as_str() {
                        status = s.to_string();
                    }
                    if let Some(a) = item["editorial_team_abbr"].as_str() {
                        team_abbr = a.to_uppercase();
                    }
                    if let Some(positions) = item["eligible_positions"].as_array() {
                        if let Some(first) = positions.first() {
                            if let Some(pos) = first["position"].as_str() {
                                position = pos.to_string();
                            }
                        }
                    }
                }
            }

            let is_active = status.is_empty() || status == "active";

            if !name.is_empty() && !position.is_empty() && is_active {
                if seen.insert(name.clone()) {
                    println!("  [SP] {} | {} | {}", name, position, team_abbr);
                    combined.push(format!("{}:{}:{}", name, position, team_abbr));
                    sp_count += 1;
                }
            }
        }

        if players_on_page < page_size { break; }
        start += page_size;
    }
    println!("  SP pool: {} players", sp_count);

    // --- Pool 3: Top 25 pitchers by saves ---
    println!("\nFetching closer pool (top 25 by saves)...");
    start = 0;
    let mut closer_count = 0;
    while closer_count < 25 {
        let url = format!(
            "{}/league/{}/players;status=FA;position=P;sort=S_SV;start={};count={}?format=json",
            YAHOO_API_BASE, config.league_key, start, page_size
        );
        let resp = client.get(&url).bearer_auth(token).send().await?
            .error_for_status()?.json::<serde_json::Value>().await?;

        let players = &resp["fantasy_content"]["league"][1]["players"];
        let mut players_on_page: u64 = 0;
        for i in 0..page_size {
            if players[i.to_string()].is_null() { break; }
            players_on_page += 1;
        }
        if players_on_page == 0 { break; }

        for i in 0..players_on_page {
            let player_info = &players[i.to_string()]["player"][0];
            let mut name      = String::new();
            let mut position  = String::new();
            let mut status    = String::new();
            let mut team_abbr = String::new();

            if let Some(arr) = player_info.as_array() {
                for item in arr {
                    if let Some(n) = item["name"]["full"].as_str() {
                        name = n.to_string();
                    }
                    if let Some(s) = item["status"].as_str() {
                        status = s.to_string();
                    }
                    if let Some(a) = item["editorial_team_abbr"].as_str() {
                        team_abbr = a.to_uppercase();
                    }
                    if let Some(positions) = item["eligible_positions"].as_array() {
                        if let Some(first) = positions.first() {
                            if let Some(pos) = first["position"].as_str() {
                                position = pos.to_string();
                            }
                        }
                    }
                }
            }

            let is_active = status.is_empty() || status == "active";

            if !name.is_empty() && !position.is_empty() && is_active {
                if seen.insert(name.clone()) {
                    println!("  [CL] {} | {} | {}", name, position, team_abbr);
                    combined.push(format!("{}:{}:{}", name, position, team_abbr));
                    closer_count += 1;
                }
            }
        }

        if players_on_page < page_size { break; }
        start += page_size;
    }
    println!("  Closer pool: {} players", closer_count);

    println!("\nFree agent pool: {} total after dedup", combined.len());
    Ok(combined)
}

// ---------------------------------------------------------------------------
// Fetch and save FA pool positions to CSV
// ---------------------------------------------------------------------------
async fn fetch_and_save_fa_positions(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
    output_path: &str,
) -> anyhow::Result<()> {
    let page_size: u64 = 25;
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut v_player_key: Vec<String> = Vec::new();
    let mut v_name:       Vec<String> = Vec::new();
    let mut v_positions:  Vec<String> = Vec::new();

    let pools: [(String, usize); 3] = [
        (
            format!("{}/league/{}/players;status=FA;sort=OR", YAHOO_API_BASE, config.league_key),
            75,
        ),
        (
            format!("{}/league/{}/players;status=FA;position=P;sort=S_IP", YAHOO_API_BASE, config.league_key),
            50,
        ),
        (
            format!("{}/league/{}/players;status=FA;position=P;sort=S_SV", YAHOO_API_BASE, config.league_key),
            25,
        ),
    ];

    for (base_url, max) in &pools {
        let mut start: u64 = 0;
        let mut count: usize = 0;

        while count < *max {
            let url = format!("{};start={};count={}?format=json", base_url, start, page_size);
            let resp = client.get(&url).bearer_auth(token).send().await?
                .error_for_status()?.json::<serde_json::Value>().await?;

            let players = &resp["fantasy_content"]["league"][1]["players"];
            let mut on_page: u64 = 0;
            for i in 0..page_size {
                if players[i.to_string()].is_null() { break; }
                on_page += 1;
            }
            if on_page == 0 { break; }

            for i in 0..on_page {
                let player_info = &players[i.to_string()]["player"][0];
                let mut player_key = String::new();
                let mut name       = String::new();
                let mut positions: Vec<String> = Vec::new();
                let mut status     = String::new();

                if let Some(arr) = player_info.as_array() {
                    for item in arr {
                        if let Some(k) = item["player_key"].as_str() {
                            player_key = k.to_string();
                        }
                        if let Some(n) = item["name"]["full"].as_str() {
                            name = n.to_string();
                        }
                        if let Some(s) = item["status"].as_str() {
                            status = s.to_string();
                        }
                        if let Some(pos_arr) = item["eligible_positions"].as_array() {
                            for p in pos_arr {
                                if let Some(pos) = p["position"].as_str() {
                                    if pos != "BN" && pos != "DL" && pos != "NA" {
                                        positions.push(pos.to_string());
                                    }
                                }
                            }
                        }
                    }
                }

                let is_active = status.is_empty() || status == "active";
                if !name.is_empty() && is_active && seen.insert(name.clone()) {
                    v_player_key.push(player_key);
                    v_name.push(name);
                    v_positions.push(positions.join("|"));
                    count += 1;
                }
            }

            if on_page < page_size { break; }
            start += page_size;
        }
    }

    let mut file = File::create(output_path)?;
    writeln!(file, "player_key,name,eligible_positions")?;
    for i in 0..v_name.len() {
        writeln!(file, "\"{}\",\"{}\",\"{}\"",
            v_player_key[i], v_name[i], v_positions[i])?;
    }

    println!("FA positions: {} players written to {}", v_name.len(), output_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// Get opponent's roster
// ---------------------------------------------------------------------------
async fn fetch_current_opponent_key(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
    my_team_key: &str,
) -> anyhow::Result<MatchupInfo> {
    let url = format!(
        "{}/team/{}/matchups?format=json",
        YAHOO_API_BASE, my_team_key
    );

    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    // Find the current week matchup and extract opponent team key
    let matchups = &resp["fantasy_content"]["team"][1]["matchups"];
    let count = matchups["count"].as_u64()
        .or_else(|| matchups["count"].as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0);

    for i in 0..count {
        let matchup = &matchups[i.to_string()]["matchup"];

        // Only process the matchup that contains today's date
        let today = chrono::Utc::now().date_naive();
        let week_start = matchup["week_start"].as_str()
            .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok());
        let week_end = matchup["week_end"].as_str()
            .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok());

        match (week_start, week_end) {
            (Some(start), Some(end)) if today >= start && today <= end => {
                println!("  Current matchup: week {} ({} to {})",
                    i+1, start, end);
                let teams = &matchup["0"]["teams"];
                let team_count = teams["count"].as_u64().unwrap_or(0);
                for j in 0..team_count {
                        let team_arr = teams[j.to_string()]["team"][0]
                            .as_array();

                        let team_key = team_arr
                            .and_then(|arr| arr.iter().find(|item| item["team_key"].is_string()))
                            .and_then(|item| item["team_key"].as_str())
                            .unwrap_or("");

                        let team_name = team_arr
                            .and_then(|arr| arr.iter().find(|item| item["name"].is_string()))
                            .and_then(|item| item["name"].as_str())
                            .unwrap_or("unknown");

                        if !team_key.is_empty() && team_key != my_team_key {
                            println!("Current matchup: week {} ({} to {})", i + 1, start, end);
                            println!("Opponent this week: \"{}\" (key: {})", team_name, team_key);
                            return Ok(MatchupInfo {
                                opponent_key:  team_key.to_string(),
                                opponent_name: team_name.to_string(),
                                current_week:  (i + 1) as u32,
                            });
                        }
                    }
                }
            _ => continue,
        }
    }

    anyhow::bail!("Could not find current week matchup in schedule")
}

async fn fetch_opponent_roster(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
    my_team_key: &str,
) -> anyhow::Result<(Vec<String>, MatchupInfo)> {
    let matchup = fetch_current_opponent_key(token, config, client, my_team_key).await?;
    let opponent_key = &matchup.opponent_key;

    // Reuse the same roster parsing logic — just a different team key
    let url = format!(
        "{}/team/{}/roster?format=json",
        YAHOO_API_BASE, opponent_key
    );

    let resp = client
        .get(&url)
        .bearer_auth(token)
        .send()
        .await?
        .error_for_status()?
        .json::<serde_json::Value>()
        .await?;

    let players = &resp["fantasy_content"]["team"][1]["roster"]["0"]["players"];
    let count = players["count"].as_u64()
        .or_else(|| players["count"].as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0);

    let mut player_strings: Vec<String> = Vec::new();

    for i in 0..count {
        let player_info = &players[i.to_string()]["player"][0];
        let mut name      = String::new();
        let mut position  = String::new();
        let mut team_abbr = String::new();

        if let Some(arr) = player_info.as_array() {
            for item in arr {
                if let Some(n) = item["name"]["full"].as_str() {
                    name = n.to_string();
                }
                if let Some(a) = item["editorial_team_abbr"].as_str() {
                    team_abbr = a.to_uppercase();
                }
                if let Some(positions) = item["eligible_positions"].as_array() {
                    if let Some(first) = positions.first() {
                        if let Some(pos) = first["position"].as_str() {
                            position = pos.to_string();
                        }
                    }
                }
            }
        }

        if !name.is_empty() && !position.is_empty() {
            player_strings.push(format!("{}:{}:{}", name, position, team_abbr));
        }
    }

    println!("Opponent roster ({} players):", player_strings.len());
    Ok((player_strings, matchup))
}

// ---------------------------------------------------------------------------
// fecth_stat_categories / fetch_scoreboard_history to get historical team data
// ---------------------------------------------------------------------------
async fn fetch_stat_categories(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
) -> anyhow::Result<std::collections::HashMap<String, (String, bool)>> {
    let url = format!(
        "{}/league/{}/settings?format=json",
        YAHOO_API_BASE, config.league_key
    );
    let resp = client.get(&url).bearer_auth(token).send().await?
        .error_for_status()?.json::<serde_json::Value>().await?;

    // settings is a JSON array; find the element containing stat_categories
    let settings_arr = &resp["fantasy_content"]["league"][1]["settings"];
    let stats = settings_arr
        .as_array()
        .and_then(|arr| arr.iter().find(|item| !item["stat_categories"].is_null()))
        .map(|item| &item["stat_categories"]["stats"]);

    let mut categories = std::collections::HashMap::new();

    if let Some(stats_arr) = stats.and_then(|s| s.as_array()) {
        for entry in stats_arr {
            let stat = &entry["stat"];
            if stat.is_null() { continue; }

            let stat_id = match stat["stat_id"].as_u64() {
                Some(id) => id.to_string(),
                None => continue,
            };
            let display_name = stat["display_name"].as_str().unwrap_or("").to_string();
            // sort_order "1" = higher is better; "0" = lower is better (ERA, WHIP)
            let lower_is_better = stat["sort_order"].as_str().unwrap_or("1") == "0";

            if !display_name.is_empty() {
                categories.insert(stat_id, (display_name, lower_is_better));
            }
        }
    }

    println!("Stat categories loaded: {} tracked", categories.len());
    Ok(categories)
}

async fn fetch_scoreboard_history(
    token: &str,
    config: &YahooConfig,
    client: &reqwest::Client,
    current_week: u32,
) -> anyhow::Result<()> {
    let cache_path = "data/scoreboard_history.parquet";
    let stat_cats = fetch_stat_categories(token, config, client).await?;

    // Find max completed week already cached
    let mut max_cached_week: u32 = 0;
    let mut existing_rows: Option<DataFrame> = None;

    if std::path::Path::new(cache_path).exists() {
        let file = File::open(cache_path)?;
        let df = ParquetReader::new(file).finish()?;
        // Strip current-week rows — always re-fetch live data
        let completed = df.lazy()
            .filter(col("is_current_week").eq(lit(false)))
            .collect()?;
        if completed.height() > 0 {
            let max_w = completed.column("week")?.i32()?.max().unwrap_or(0);
            max_cached_week = max_w.max(0) as u32;
        }
        existing_rows = Some(completed);
    }

    let fetch_from = (max_cached_week + 1).max(1);
    println!("\nFetching scoreboard weeks {} to {}...", fetch_from, current_week);

    // Column vecs for new rows
    let mut v_week:        Vec<i32>    = Vec::new();
    let mut v_matchup_id:  Vec<i32>    = Vec::new();
    let mut v_team_key:    Vec<String> = Vec::new();
    let mut v_team_name:   Vec<String> = Vec::new();
    let mut v_opp_key:     Vec<String> = Vec::new();
    let mut v_opp_name:    Vec<String> = Vec::new();
    let mut v_stat_name:   Vec<String> = Vec::new();
    let mut v_stat_value:  Vec<f64>    = Vec::new();
    let mut v_lower:       Vec<bool>   = Vec::new();
    let mut v_week_start:  Vec<String> = Vec::new();
    let mut v_week_end:    Vec<String> = Vec::new();
    let mut v_is_current:  Vec<bool>   = Vec::new();

    for week in fetch_from..=current_week {
        let is_current = week == current_week;
        let url = format!(
            "{}/league/{}/scoreboard;week={}?format=json",
            YAHOO_API_BASE, config.league_key, week
        );

        let resp = client.get(&url).bearer_auth(token).send().await?
            .error_for_status()?.json::<serde_json::Value>().await?;

        let matchups_node = &resp["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"];
        let matchup_count = matchups_node["count"].as_u64()
            .or_else(|| matchups_node["count"].as_str().and_then(|s| s.parse().ok()))
            .unwrap_or(0);

        for m in 0..matchup_count {
            let matchup   = &matchups_node[m.to_string()]["matchup"];
            let week_start = matchup["week_start"].as_str().unwrap_or("").to_string();
            let week_end   = matchup["week_end"].as_str().unwrap_or("").to_string();
            let teams_node = &matchup["0"]["teams"];
            let team_count = teams_node["count"].as_u64().unwrap_or(2);

            // First pass: collect both teams
            let mut teams: Vec<(String, String, Vec<(String, f64, bool)>)> = Vec::new();

            for t in 0..team_count {
                let team = &teams_node[t.to_string()]["team"];
                let info = team[0].as_array();

                let key = info.and_then(|arr| arr.iter()
                    .find(|item| item["team_key"].is_string()))
                    .and_then(|item| item["team_key"].as_str())
                    .unwrap_or("").to_string();

                let name = info.and_then(|arr| arr.iter()
                    .find(|item| item["name"].is_string()))
                    .and_then(|item| item["name"].as_str())
                    .unwrap_or("").to_string();

                let stats_node = &team[1]["team_stats"]["stats"];
                let mut parsed: Vec<(String, f64, bool)> = Vec::new();
                if let Some(stats_arr) = stats_node.as_array() {
                    for entry in stats_arr {
                        let stat = &entry["stat"];
                        if stat.is_null() { continue; }
                        // stat_id arrives as a JSON string ("7"), not an integer
                        let stat_id = stat["stat_id"].as_str()
                            .map(|s| s.to_string())
                            .or_else(|| stat["stat_id"].as_u64().map(|n| n.to_string()))
                            .unwrap_or_default();
                        let value: f64 = stat["value"].as_str()
                            .and_then(|v| v.parse().ok()).unwrap_or(0.0);
                        if let Some((sname, lower)) = stat_cats.get(&stat_id) {
                            parsed.push((sname.clone(), value, *lower));
                        }
                    }
                }
                teams.push((key, name, parsed));
            }

            if teams.len() < 2 { continue; }

            // Second pass: write a row per team per stat, with opponent info
            for (ti, (t_key, t_name, t_stats)) in teams.iter().enumerate() {
                let (o_key, o_name, _) = &teams[1 - ti];
                for (sname, sval, lower) in t_stats {
                    v_week.push(week as i32);
                    v_matchup_id.push(m as i32);
                    v_team_key.push(t_key.clone());
                    v_team_name.push(t_name.clone());
                    v_opp_key.push(o_key.clone());
                    v_opp_name.push(o_name.clone());
                    v_stat_name.push(sname.clone());
                    v_stat_value.push(*sval);
                    v_lower.push(*lower);
                    v_week_start.push(week_start.clone());
                    v_week_end.push(week_end.clone());
                    v_is_current.push(is_current);
                }
            }
        }
        println!("  Week {}: {} matchups fetched", week, matchup_count);
    }

    let mut new_df = DataFrame::new(vec![
        Series::new("week".into(),            v_week).into(),
        Series::new("matchup_id".into(),      v_matchup_id).into(),
        Series::new("team_key".into(),        v_team_key).into(),
        Series::new("team_name".into(),       v_team_name).into(),
        Series::new("opponent_key".into(),    v_opp_key).into(),
        Series::new("opponent_name".into(),   v_opp_name).into(),
        Series::new("stat_name".into(),       v_stat_name).into(),
        Series::new("stat_value".into(),      v_stat_value).into(),
        Series::new("lower_is_better".into(), v_lower).into(),
        Series::new("week_start".into(),      v_week_start).into(),
        Series::new("week_end".into(),        v_week_end).into(),
        Series::new("is_current_week".into(), v_is_current).into(),
    ])?;

    let mut final_df = if let Some(mut existing) = existing_rows {
        if new_df.height() > 0 {
            existing.vstack_mut(&new_df)?;
        }
        existing
    } else {
        new_df
    };

    let mut file = File::create(cache_path)?;
    ParquetWriter::new(&mut file).finish(&mut final_df)?;
    println!("Scoreboard history: {} rows saved to {}", final_df.height(), cache_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// build_opponent_history / build_league_benchmarks analyze opponent and league data
// ---------------------------------------------------------------------------
fn build_opponent_history(my_team_key: &str, opponent_key: &str) -> anyhow::Result<()> {
    let cache_path  = "data/scoreboard_history.parquet";
    let output_path = "data/opponent_history.parquet";

    if !std::path::Path::new(cache_path).exists() {
        println!("No scoreboard history yet — skipping opponent history.");
        return Ok(());
    }

    let file = File::open(cache_path)?;
    let df   = ParquetReader::new(file).finish()?;

    // My rows for weeks I played this opponent
    let my_rows = df.clone().lazy()
        .filter(
            col("team_key").eq(lit(my_team_key))
            .and(col("opponent_key").eq(lit(opponent_key)))
        )
        .select([
            col("week"), col("week_start"), col("week_end"),
            col("stat_name"), col("stat_value").alias("my_value"),
            col("lower_is_better"), col("is_current_week"),
        ])
        .collect()?;

    // Opponent rows for the same weeks
    let opp_rows = df.lazy()
        .filter(
            col("team_key").eq(lit(opponent_key))
            .and(col("opponent_key").eq(lit(my_team_key)))
        )
        .select([
            col("week"), col("stat_name"),
            col("stat_value").alias("opp_value"),
        ])
        .collect()?;

    let mut joined = my_rows.join(
        &opp_rows,
        ["week", "stat_name"],
        ["week", "stat_name"],
        JoinArgs::new(JoinType::Left),
        None,
    )?;

    let mut file = File::create(output_path)?;
    ParquetWriter::new(&mut file).finish(&mut joined)?;
    println!("Opponent history: {} rows saved to {}", joined.height(), output_path);
    Ok(())
}

fn build_league_benchmarks() -> anyhow::Result<()> {
    let cache_path  = "data/scoreboard_history.parquet";
    let output_path = "data/league_benchmarks.parquet";

    if !std::path::Path::new(cache_path).exists() {
        println!("No scoreboard history yet — skipping league benchmarks.");
        return Ok(());
    }

    let file = File::open(cache_path)?;
    let df   = ParquetReader::new(file).finish()?;

    // Only completed weeks for benchmarks
    let completed = df.lazy()
        .filter(col("is_current_week").eq(lit(false)))
        .collect()?;

    let weeks_col = completed.column("week")?.i32()?;
    let mid_col   = completed.column("matchup_id")?.i32()?;
    let tk_col    = completed.column("team_key")?.str()?;
    let tn_col    = completed.column("team_name")?.str()?;
    let sn_col    = completed.column("stat_name")?.str()?;
    let sv_col    = completed.column("stat_value")?.f64()?;
    let lb_col    = completed.column("lower_is_better")?.bool()?;

    // Group matchups: (week, matchup_id, stat_name) → both teams
    let mut groups: std::collections::HashMap<
        (i32, i32, String),
        Vec<(String, String, f64, bool)>  // (team_key, team_name, value, lower)
    > = std::collections::HashMap::new();

    // Season avg accumulator: (team_key, team_name, stat_name) → (sum, count)
    let mut avg_acc: std::collections::HashMap<
        (String, String, String), (f64, u32)
    > = std::collections::HashMap::new();

    for i in 0..completed.height() {
        let week = weeks_col.get(i).unwrap_or(0);
        let mid  = mid_col.get(i).unwrap_or(0);
        let tk   = tk_col.get(i).unwrap_or("").to_string();
        let tn   = tn_col.get(i).unwrap_or("").to_string();
        let sn   = sn_col.get(i).unwrap_or("").to_string();
        let sv   = sv_col.get(i).unwrap_or(0.0);
        let lb   = lb_col.get(i).unwrap_or(false);

        groups.entry((week, mid, sn.clone()))
            .or_default()
            .push((tk.clone(), tn.clone(), sv, lb));

        let e = avg_acc.entry((tk, tn, sn)).or_insert((0.0, 0));
        e.0 += sv;
        e.1 += 1;
    }

    // Compute win/loss/tie per (team_key, stat_name)
    let mut win_tracker: std::collections::HashMap<
        (String, String), (u32, u32, u32)  // (wins, losses, ties)
    > = std::collections::HashMap::new();

    for ((_w, _m, stat), teams) in &groups {
        if teams.len() != 2 { continue; }
        let (t1k, _, t1v, lower) = &teams[0];
        let (t2k, _, t2v, _)     = &teams[1];

        let (t1_wins, t2_wins) = if *lower {
            (t1v < t2v, t2v < t1v)
        } else {
            (t1v > t2v, t2v > t1v)
        };

        let e1 = win_tracker.entry((t1k.clone(), stat.clone())).or_insert((0, 0, 0));
        if t1_wins { e1.0 += 1; } else if t2_wins { e1.1 += 1; } else { e1.2 += 1; }

        let e2 = win_tracker.entry((t2k.clone(), stat.clone())).or_insert((0, 0, 0));
        if t2_wins { e2.0 += 1; } else if t1_wins { e2.1 += 1; } else { e2.2 += 1; }
    }

    // Build output rows
    let mut out_tk:  Vec<String> = Vec::new();
    let mut out_tn:  Vec<String> = Vec::new();
    let mut out_sn:  Vec<String> = Vec::new();
    let mut out_avg: Vec<f64>    = Vec::new();
    let mut out_wr:  Vec<f64>    = Vec::new();
    let mut out_gp:  Vec<i32>    = Vec::new();

    for ((tk, tn, sn), (sum, count)) in &avg_acc {
        let avg = if *count > 0 { sum / *count as f64 } else { 0.0 };
        let (wins, losses, ties) = win_tracker
            .get(&(tk.clone(), sn.clone())).copied().unwrap_or((0, 0, 0));
        let total = wins + losses + ties;
        let wr = if total > 0 { wins as f64 / total as f64 } else { 0.0 };

        out_tk.push(tk.clone());
        out_tn.push(tn.clone());
        out_sn.push(sn.clone());
        out_avg.push(avg);
        out_wr.push(wr);
        out_gp.push(*count as i32);
    }

    // League average per stat = average of all teams' season averages
    let mut league_sum: std::collections::HashMap<String, (f64, u32)> = std::collections::HashMap::new();
    for (i, sn) in out_sn.iter().enumerate() {
        let e = league_sum.entry(sn.clone()).or_insert((0.0, 0));
        e.0 += out_avg[i];
        e.1 += 1;
    }
    let out_league_avg: Vec<f64> = out_sn.iter().map(|sn| {
        let (s, c) = league_sum.get(sn).copied().unwrap_or((0.0, 0));
        if c > 0 { s / c as f64 } else { 0.0 }
    }).collect();

    let mut result = DataFrame::new(vec![
        Series::new("team_key".into(),     out_tk).into(),
        Series::new("team_name".into(),    out_tn).into(),
        Series::new("stat_name".into(),    out_sn).into(),
        Series::new("season_avg".into(),   out_avg).into(),
        Series::new("league_avg".into(),   out_league_avg).into(),
        Series::new("win_rate".into(),     out_wr).into(),
        Series::new("weeks_played".into(), out_gp).into(),
    ])?;

    let mut file = File::create(output_path)?;
    ParquetWriter::new(&mut file).finish(&mut result)?;
    println!("League benchmarks: {} rows saved to {}", result.height(), output_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// MLB API calls to get upcoming probable starter and schedules
// ---------------------------------------------------------------------------
async fn fetch_mlb_team_map(
    config: &MlbConfig,
    client: &reqwest::Client,
) -> anyhow::Result<std::collections::HashMap<u64, (String, String)>> {
    let url = format!("{}/teams?sportId=1", config.base_url);
    let resp = client.get(&url).send().await?
        .error_for_status()?.json::<serde_json::Value>().await?;

    let mut map = std::collections::HashMap::new();
    if let Some(teams) = resp["teams"].as_array() {
        for team in teams {
            let id   = team["id"].as_u64().unwrap_or(0);
            let name = team["name"].as_str().unwrap_or("").to_string();
            let abbr = team["abbreviation"].as_str().unwrap_or("").to_string();
            if id > 0 && !abbr.is_empty() {
                map.insert(id, (name, abbr));
            }
        }
    }
    println!("MLB team map loaded: {} teams", map.len());
    Ok(map)
}

async fn fetch_probable_starters(
    config: &MlbConfig,
    client: &reqwest::Client,
    team_map: &std::collections::HashMap<u64, (String, String)>,
) -> anyhow::Result<()> {
    let today    = chrono::Utc::now().date_naive();
    let end_date = today + chrono::Duration::days(config.probable_starters_days_ahead as i64);
    let output_path = format!("data/probable_starters_{}.parquet", today.format("%Y%m%d"));

    let url = format!(
        "{}/schedule?sportId=1&startDate={}&endDate={}&hydrate=probablePitcher&gameType=R",
        config.base_url,
        today.format("%Y-%m-%d"),
        end_date.format("%Y-%m-%d"),
    );

    let resp = client.get(&url).send().await?
        .error_for_status()?.json::<serde_json::Value>().await?;

    let mut v_date:            Vec<String>        = Vec::new();
    let mut v_game_pk:         Vec<i64>           = Vec::new();
    let mut v_home_team:       Vec<String>        = Vec::new();
    let mut v_home_abbr:       Vec<String>        = Vec::new();
    let mut v_away_team:       Vec<String>        = Vec::new();
    let mut v_away_abbr:       Vec<String>        = Vec::new();
    let mut v_home_pitcher:    Vec<Option<String>> = Vec::new();
    let mut v_home_pitcher_id: Vec<Option<i64>>   = Vec::new();
    let mut v_away_pitcher:    Vec<Option<String>> = Vec::new();
    let mut v_away_pitcher_id: Vec<Option<i64>>   = Vec::new();
    let mut v_venue:           Vec<String>        = Vec::new();

    if let Some(dates) = resp["dates"].as_array() {
        for date_obj in dates {
            let date = date_obj["date"].as_str().unwrap_or("").to_string();
            if let Some(games) = date_obj["games"].as_array() {
                for game in games {
                    let game_pk = game["gamePk"].as_i64().unwrap_or(0);

                    let home_id = game["teams"]["home"]["team"]["id"].as_u64().unwrap_or(0);
                    let away_id = game["teams"]["away"]["team"]["id"].as_u64().unwrap_or(0);

                    let (home_name, home_abbr) = team_map.get(&home_id)
                        .cloned().unwrap_or_default();
                    let (away_name, away_abbr) = team_map.get(&away_id)
                        .cloned().unwrap_or_default();

                    let home_pitcher = game["teams"]["home"]["probablePitcher"]["fullName"]
                        .as_str().map(|s| s.to_string());
                    let home_pitcher_id = game["teams"]["home"]["probablePitcher"]["id"]
                        .as_i64();
                    let away_pitcher = game["teams"]["away"]["probablePitcher"]["fullName"]
                        .as_str().map(|s| s.to_string());
                    let away_pitcher_id = game["teams"]["away"]["probablePitcher"]["id"]
                        .as_i64();

                    let venue = game["venue"]["name"].as_str().unwrap_or("").to_string();

                    v_date.push(date.clone());
                    v_game_pk.push(game_pk);
                    v_home_team.push(home_name);
                    v_home_abbr.push(home_abbr);
                    v_away_team.push(away_name);
                    v_away_abbr.push(away_abbr);
                    v_home_pitcher.push(home_pitcher);
                    v_home_pitcher_id.push(home_pitcher_id);
                    v_away_pitcher.push(away_pitcher);
                    v_away_pitcher_id.push(away_pitcher_id);
                    v_venue.push(venue);
                }
            }
        }
    }

    let mut df = DataFrame::new(vec![
        Series::new("date".into(),            v_date).into(),
        Series::new("game_pk".into(),         v_game_pk).into(),
        Series::new("home_team".into(),       v_home_team).into(),
        Series::new("home_abbr".into(),       v_home_abbr).into(),
        Series::new("away_team".into(),       v_away_team).into(),
        Series::new("away_abbr".into(),       v_away_abbr).into(),
        Series::new("home_pitcher".into(),    v_home_pitcher).into(),
        Series::new("home_pitcher_id".into(), v_home_pitcher_id).into(),
        Series::new("away_pitcher".into(),    v_away_pitcher).into(),
        Series::new("away_pitcher_id".into(), v_away_pitcher_id).into(),
        Series::new("venue".into(),           v_venue).into(),
    ])?;

    let mut file = File::create(&output_path)?;
    ParquetWriter::new(&mut file).finish(&mut df)?;
    println!("Probable starters: {} games written to {}", df.height(), output_path);
    Ok(())
}

async fn fetch_schedules(
    config: &MlbConfig,
    client: &reqwest::Client,
    team_map: &std::collections::HashMap<u64, (String, String)>,
) -> anyhow::Result<()> {
    let today    = chrono::Utc::now().date_naive();
    let end_date = today + chrono::Duration::days(config.probable_starters_days_ahead as i64);
    let output_path = format!("data/schedule_{}.parquet", today.format("%Y%m%d"));

    let url = format!(
        "{}/schedule?sportId=1&startDate={}&endDate={}&gameType=R",
        config.base_url,
        today.format("%Y-%m-%d"),
        end_date.format("%Y-%m-%d"),
    );

    let resp = client.get(&url).send().await?
        .error_for_status()?.json::<serde_json::Value>().await?;

    let mut v_date:      Vec<String> = Vec::new();
    let mut v_game_pk:   Vec<i64>   = Vec::new();
    let mut v_home_team: Vec<String> = Vec::new();
    let mut v_home_abbr: Vec<String> = Vec::new();
    let mut v_away_team: Vec<String> = Vec::new();
    let mut v_away_abbr: Vec<String> = Vec::new();
    let mut v_venue:     Vec<String> = Vec::new();
    let mut v_game_time: Vec<String> = Vec::new();
    let mut v_status:    Vec<String> = Vec::new();

    if let Some(dates) = resp["dates"].as_array() {
        for date_obj in dates {
            let date = date_obj["date"].as_str().unwrap_or("").to_string();
            if let Some(games) = date_obj["games"].as_array() {
                for game in games {
                    let game_pk   = game["gamePk"].as_i64().unwrap_or(0);
                    let game_time = game["gameDate"].as_str().unwrap_or("").to_string();
                    let status    = game["status"]["detailedState"]
                        .as_str().unwrap_or("").to_string();

                    let home_id = game["teams"]["home"]["team"]["id"].as_u64().unwrap_or(0);
                    let away_id = game["teams"]["away"]["team"]["id"].as_u64().unwrap_or(0);

                    let (home_name, home_abbr) = team_map.get(&home_id)
                        .cloned().unwrap_or_default();
                    let (away_name, away_abbr) = team_map.get(&away_id)
                        .cloned().unwrap_or_default();

                    let venue = game["venue"]["name"].as_str().unwrap_or("").to_string();

                    v_date.push(date.clone());
                    v_game_pk.push(game_pk);
                    v_home_team.push(home_name);
                    v_home_abbr.push(home_abbr);
                    v_away_team.push(away_name);
                    v_away_abbr.push(away_abbr);
                    v_venue.push(venue);
                    v_game_time.push(game_time);
                    v_status.push(status);
                }
            }
        }
    }

    let mut df = DataFrame::new(vec![
        Series::new("date".into(),      v_date).into(),
        Series::new("game_pk".into(),   v_game_pk).into(),
        Series::new("home_team".into(), v_home_team).into(),
        Series::new("home_abbr".into(), v_home_abbr).into(),
        Series::new("away_team".into(), v_away_team).into(),
        Series::new("away_abbr".into(), v_away_abbr).into(),
        Series::new("venue".into(),     v_venue).into(),
        Series::new("game_time".into(), v_game_time).into(),
        Series::new("status".into(),    v_status).into(),
    ])?;

    let mut file = File::create(&output_path)?;
    ParquetWriter::new(&mut file).finish(&mut df)?;
    println!("Schedule: {} games written to {}", df.height(), output_path);
    Ok(())
}


// ---------------------------------------------------------------------------
// Patch scoreboard_history.parquet with manual counting stats from CSV
// ---------------------------------------------------------------------------
fn patch_scoreboard_midweek(
    my_team_key: &str,
    opponent_key: &str,
    current_week: u32,
) -> anyhow::Result<()> {
    let cache_path = "data/scoreboard_history.parquet";
    let csv_path   = "data/midweek_matchup_state.csv";

    if !std::path::Path::new(csv_path).exists() {
        println!("No midweek_matchup_state.csv found — skipping midweek patch.");
        return Ok(());
    }

    let csv_df = LazyCsvReader::new(csv_path)
        .with_has_header(true)
        .finish()?
        .filter(col("week").cast(DataType::Int64).eq(lit(current_week as i64)))
        .collect()?;

    if csv_df.height() == 0 {
        println!("No row for week {} in midweek_matchup_state.csv — skipping patch.", current_week);
        return Ok(());
    }

    fn get_f64(df: &DataFrame, col_name: &str) -> f64 {
        df.column(col_name)
            .ok()
            .and_then(|s| s.cast(&DataType::Float64).ok())
            .and_then(|s| s.f64().ok().and_then(|ca| ca.get(0)))
            .unwrap_or(0.0)
    }

    let mut patch: std::collections::HashMap<(String, String), f64> =
        std::collections::HashMap::new();

    let mine_map = [
        ("R", "r_mine"), ("HR", "hr_mine"), ("RBI", "rbi_mine"),
        ("SB", "sb_mine"), ("W", "w_mine"), ("SV", "sv_mine"), ("K", "k_mine"),
    ];
    let opp_map = [
        ("R", "r_opp"), ("HR", "hr_opp"), ("RBI", "rbi_opp"),
        ("SB", "sb_opp"), ("W", "w_opp"), ("SV", "sv_opp"), ("K", "k_opp"),
    ];

    for (stat, col_name) in &mine_map {
        patch.insert(
            (my_team_key.to_string(), stat.to_string()),
            get_f64(&csv_df, col_name),
        );
    }
    for (stat, col_name) in &opp_map {
        patch.insert(
            (opponent_key.to_string(), stat.to_string()),
            get_f64(&csv_df, col_name),
        );
    }

    let file = File::open(cache_path)?;
    let df   = ParquetReader::new(file).finish()?;

    let weeks_col  = df.column("week")?.i32()?;
    let tk_col     = df.column("team_key")?.str()?;
    let sn_col     = df.column("stat_name")?.str()?;
    let sv_col     = df.column("stat_value")?.f64()?;
    let is_cur_col = df.column("is_current_week")?.bool()?;

    let new_values: Vec<f64> = (0..df.height()).map(|i| {
        let w   = weeks_col.get(i).unwrap_or(0) as u32;
        let tk  = tk_col.get(i).unwrap_or("");
        let sn  = sn_col.get(i).unwrap_or("");
        let sv  = sv_col.get(i).unwrap_or(0.0);
        let cur = is_cur_col.get(i).unwrap_or(false);

        if cur && w == current_week {
            if let Some(&v) = patch.get(&(tk.to_string(), sn.to_string())) {
                return v;
            }
        }
        sv
    }).collect();

    let mut result = df.clone();
    result.replace("stat_value", Series::new("stat_value".into(), new_values))?;

    let mut file = File::create(cache_path)?;
    ParquetWriter::new(&mut file).finish(&mut result)?;
    println!("Scoreboard patched with midweek counting stats for week {}.", current_week);
    Ok(())
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = load_config("./config.toml");
    let cli    = Cli::parse();
    let today  = chrono::Utc::now().format("%Y%m%d").to_string();

    let client = reqwest::Client::new();
    let token  = get_valid_token(&config.yahoo, &client).await?;

    match cli.mode {
        Mode::Full => {
            println!("=== FULL RUN (Sunday) ===");
            ingest_park_factors(&config.park_factors.csv_path, "data/park_factors.parquet")?;
            fetch_league_metadata(&token, &config.yahoo, &client).await?;
            let my_team_key = get_my_team_key(&token, &config.yahoo, &client).await?;
            let roster      = fetch_my_roster(&token, &config.yahoo, &client).await?;
            fetch_and_save_roster_positions(
                &token, &config.yahoo, &client, &my_team_key,
                &format!("data/roster_positions_{}.csv", today),
            ).await?;
            let free_agents = fetch_free_agents(&token, &config.yahoo, &client).await?;
            let (opponent, matchup_info) =
                fetch_opponent_roster(&token, &config.yahoo, &client, &my_team_key).await?;
            fetch_scoreboard_history(
                &token, &config.yahoo, &client, matchup_info.current_week,
            ).await?;
            build_opponent_history(&my_team_key, &matchup_info.opponent_key)?;
            build_league_benchmarks()?;
            let roster_refs:   Vec<&str> = roster.iter().map(|s| s.as_str()).collect();
            // opponent only — do not chain roster here, or the opponent parquet will contain your own players
            let opponent_refs: Vec<&str> = opponent.iter().map(|s| s.as_str()).collect();
            let fa_refs:       Vec<&str> = free_agents.iter().map(|s| s.as_str()).collect();
            run_pybaseball_shim(
                &roster_refs, config.yahoo.season,
                &format!("data/pybaseball_roster_{}.parquet", today), 0, 0,
            )?;
            run_pybaseball_shim(
                &opponent_refs, config.yahoo.season,
                &format!("data/pybaseball_opponent_roster_{}.parquet", today), 0, 0,
            )?;
            run_pybaseball_shim(
                &fa_refs, config.yahoo.season,
                &format!("data/pybaseball_fa_{}.parquet", today), 20, 20,
            )?;
            let team_map = fetch_mlb_team_map(&config.mlb, &client).await?;
            fetch_probable_starters(&config.mlb, &client, &team_map).await?;
            fetch_schedules(&config.mlb, &client, &team_map).await?;
            println!("=== FULL RUN COMPLETE ===");
        }

        Mode::Midweek => {
            println!("=== MIDWEEK RUN (Wednesday) ===");
            fetch_league_metadata(&token, &config.yahoo, &client).await?;
            let my_team_key = get_my_team_key(&token, &config.yahoo, &client).await?;
            let roster      = fetch_my_roster(&token, &config.yahoo, &client).await?;
            let free_agents = fetch_free_agents(&token, &config.yahoo, &client).await?;
            let (opponent, matchup_info) =
                fetch_opponent_roster(&token, &config.yahoo, &client, &my_team_key).await?;
            fetch_scoreboard_history(
                &token, &config.yahoo, &client, matchup_info.current_week,
            ).await?;
            patch_scoreboard_midweek(
                &my_team_key, &matchup_info.opponent_key, matchup_info.current_week,
            )?;
            let roster_refs:   Vec<&str> = roster.iter().map(|s| s.as_str()).collect();
            // opponent only — do not chain roster here, or the opponent parquet will contain your own players
            let opponent_refs: Vec<&str> = opponent.iter().map(|s| s.as_str()).collect();
            let fa_refs:       Vec<&str> = free_agents.iter().map(|s| s.as_str()).collect();
            run_pybaseball_shim(
                &roster_refs, config.yahoo.season,
                &format!("data/pybaseball_roster_{}.parquet", today), 0, 0,
            )?;
            run_pybaseball_shim(
                &opponent_refs, config.yahoo.season,
                &format!("data/pybaseball_opponent_roster_{}.parquet", today), 0, 0,
            )?;
            run_pybaseball_shim(
                &fa_refs, config.yahoo.season,
                &format!("data/pybaseball_fa_{}.parquet", today), 20, 20,
            )?;
            let team_map = fetch_mlb_team_map(&config.mlb, &client).await?;
            fetch_probable_starters(&config.mlb, &client, &team_map).await?;
            fetch_schedules(&config.mlb, &client, &team_map).await?;
            println!("=== MIDWEEK RUN COMPLETE ===");
        }

        Mode::Adhoc => {
            println!("=== ADHOC RUN (injury replacement) ===");
            match &cli.position {
                Some(pos) => println!("Finding replacements eligible at position: {}", pos),
                None      => println!("No --position flag provided. Results will not be filtered by position."),
            }
            fetch_and_save_fa_positions(
                &token, &config.yahoo, &client,
                &format!("data/fa_positions_{}.csv", today),
            ).await?;
            let free_agents = fetch_free_agents(&token, &config.yahoo, &client).await?;
            let fa_refs: Vec<&str> = free_agents.iter().map(|s| s.as_str()).collect();
            run_pybaseball_shim(
                &fa_refs, config.yahoo.season,
                &format!("data/pybaseball_fa_{}.parquet", today), 20, 20,
            )?;
            println!("=== ADHOC RUN COMPLETE ===");
        }
    }

    Ok(())
}
