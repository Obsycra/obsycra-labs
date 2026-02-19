// src-tauri/src/main.rs
// Quil Tauri shell — Tauri v1 compatible.
// Boots the Python backend sidecar, then starts the OS context monitor
// which silently watches the active window and pings /os/context on switch.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

// ── OS Context Monitor ────────────────────────────────────────────────────────

/// Classify an active window into a Quil context label.
fn classify_window(title: &str, app: &str) -> Option<&'static str> {
    let t = title.to_lowercase();
    let a = app.to_lowercase();

    if a.contains("code")
        || a.contains("cursor")
        || a.contains("xcode")
        || a.contains("intellij")
        || a.contains("fleet")
        || a.contains("nova")
    {
        return Some("coding");
    }
    if a.contains("terminal")
        || a.contains("iterm")
        || a.contains("warp")
        || a.contains("kitty")
        || a.contains("alacritty")
    {
        return Some("terminal");
    }
    if a.contains("zoom")
        || a.contains("teams")
        || a.contains("meet")
        || a.contains("slack")
        || a.contains("discord")
        || a.contains("webex")
    {
        return Some("meeting");
    }
    if a.contains("fantastical")
        || a.contains("ical")
        || a.contains("calendar")
        || t.contains("calendar")
        || a.contains("notion")
    {
        return Some("planning");
    }
    if a.contains("obsidian")
        || a.contains("bear")
        || a.contains("craft")
        || a.contains("ulysses")
        || a.contains("typora")
        || a.contains("pages")
    {
        return Some("writing");
    }
    None
}

/// Escape a string for safe embedding inside a JSON string value.
fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
     .replace('"', "\\\"")
     .replace('\n', "\\n")
     .replace('\r', "\\r")
     .replace('\t', "\\t")
}

/// Fire-and-forget POST to FastAPI /os/context via curl (zero Cargo deps).
fn ping_backend(context: &str, window_title: &str, app_name: &str) {
    let body = format!(
        r#"{{"context":"{}","window_title":"{}","app":"{}"}}"#,
        context,
        json_escape(window_title),
        json_escape(app_name)
    );
    let _ = Command::new("curl")
        .args([
            "-s",
            "-X",
            "POST",
            "http://127.0.0.1:8765/os/context",
            "-H",
            "Content-Type: application/json",
            "-d",
            &body,
            "--max-time",
            "3",
        ])
        .spawn();
}

fn start_os_monitor() {
    thread::spawn(|| {
        let last_context: Arc<Mutex<String>> = Arc::new(Mutex::new(String::new()));
        let last_trigger: Arc<Mutex<Instant>> =
            Arc::new(Mutex::new(Instant::now() - Duration::from_secs(400)));
        let cooldown = Duration::from_secs(300);

        loop {
            thread::sleep(Duration::from_secs(15));

            // Query frontmost app via osascript — works on macOS without extra crates.
            let output = Command::new("osascript")
                .arg("-e")
                .arg(r#"tell application "System Events" to get {name of first process whose frontmost is true, title of front window of first process whose frontmost is true}"#)
                .output();

            if let Ok(out) = output {
                let s = String::from_utf8_lossy(&out.stdout);
                let parts: Vec<&str> = s.trim().splitn(2, ", ").collect();
                if parts.len() == 2 {
                    let app = parts[0].trim();
                    let title = parts[1].trim();

                    if let Some(ctx) = classify_window(title, app) {
                        let mut last_ctx = last_context.lock().unwrap();
                        let mut last_t = last_trigger.lock().unwrap();

                        if *last_ctx != ctx && last_t.elapsed() >= cooldown {
                            *last_ctx = ctx.to_string();
                            *last_t = Instant::now();
                            println!("OS context: {} -> {} ({})", app, ctx, title);
                            ping_backend(ctx, title, app);
                        }
                    }
                }
            }
        }
    });
}

// ── Python Backend Sidecar ────────────────────────────────────────────────────

/// Returns true if something is already listening on 127.0.0.1:8765.
fn backend_already_running() -> bool {
    use std::net::TcpStream;
    TcpStream::connect("127.0.0.1:8765").is_ok()
}

fn spawn_backend() {
    thread::spawn(|| {
        // If dev.sh already started the backend, don't launch a second instance.
        if backend_already_running() {
            println!("Backend already running on :8765 — skipping sidecar launch.");
            return;
        }
        // Resolve backend/ relative to the running executable.
        // In dev:     target/debug/Quil  → ../../.. → project root → backend/
        // In release: Quil.app/Contents/MacOS/Quil → ../Resources/backend/
        let exe_path = std::env::current_exe().unwrap_or_default();
        let exe_dir = exe_path.parent().unwrap_or(std::path::Path::new("."));

        // Try the bundled path first, then fall back to the dev-tree path.
        let bundled = exe_dir.join("../Resources/backend");
        let dev_tree = exe_dir.join("../../../backend"); // target/debug → project root
        let fallback = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../backend");

        let backend_dir = if bundled.join("main.py").exists() {
            bundled.canonicalize().unwrap_or(bundled)
        } else if dev_tree.join("main.py").exists() {
            dev_tree.canonicalize().unwrap_or(dev_tree)
        } else {
            fallback.canonicalize().unwrap_or(fallback)
        };

        let venv_python = backend_dir.join(".venv/bin/python3");
        let python_bin: std::path::PathBuf = if venv_python.exists() {
            venv_python
        } else {
            std::path::PathBuf::from("python3")
        };

        println!("Backend dir: {}", backend_dir.display());
        println!("Python bin:  {}", python_bin.display());

        match Command::new(&python_bin)
            .args(["main.py"])
            .current_dir(&backend_dir)
            .spawn()
        {
            Ok(mut child) => {
                println!("Python backend started (pid={})", child.id());
                let _ = child.wait();
            }
            Err(e) => eprintln!(
                "Failed to start Python backend: {} (tried: {})",
                e,
                python_bin.display()
            ),
        }
    });
}

// ── Main ──────────────────────────────────────────────────────────────────────

fn main() {
    spawn_backend();
    start_os_monitor();

    thread::sleep(Duration::from_millis(1500));

    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Quil");
}
