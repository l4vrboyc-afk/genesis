//! Genesis Shell — Tauri 2.x desktop wrapper.
//!
//! New in this revision:
//!   - Control Center window (control_center.html) is the landing page.
//!   - Rust commands `launch_bot`, `stop_bot`, `bot_status`, `open_mt5`, `open_dashboard`
//!     spawn / kill the Python `main.py` process, query its health, and open external URLs.
//!   - Dashboard window stays as a second window pointing at `http://127.0.0.1:8000`.
//!   - Ask-Claude panel remains a placeholder window.
//!   - System tray exposes: Open Control Center, Open Dashboard, Open Ask-Claude, Quit.

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::AppHandle;
use tauri::Manager;
use tauri::State;
use tauri::WebviewUrl;
use tauri::WebviewWindowBuilder;

/// Application-wide mutable state.
/// Holds an optional child process handle so that `stop_bot` can kill it.
struct AppState {
    bot_handle: Mutex<Option<Child>>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            bot_handle: Mutex::new(None),
        }
    }
}

/// Resolve the Genesis project root directory.
/// The Tauri binary runs from `desktop/src-tauri/target/…`; project root is
/// two levels above `desktop/` (i.e. the repo root).
fn project_root() -> std::path::PathBuf {
    // Try to derive from the current exe path first.
    if let Ok(exe) = std::env::current_exe() {
        // exe is inside   desktop/src-tauri/target/…/genesis-shell.exe
        // ancestors:  target → src-tauri → desktop → <project-root>
        if let Some(root) = exe.ancestors().nth(3) {
            return root.to_path_buf();
        }
    }
    // Fallback: use the directory of the manifest as a heuristic.
    // `CARGO_MANIFEST_DIR` is set by Cargo during build.
    if let Ok(dir) = std::env::var("CARGO_MANIFEST_DIR") {
        let path = std::path::PathBuf::from(dir);
        // CARGO_MANIFEST_DIR is desktop/src-tauri → go up two levels.
        if let Some(root) = path.ancestors().nth(2) {
            return root.to_path_buf();
        }
    }
    std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
}

/// Resolve the absolute path to the Python interpreter inside `.venv`.
fn python_exe_path() -> std::path::PathBuf {
    let root = project_root();
    let venv_python = if cfg!(target_os = "windows") {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    venv_python
}

/// Resolve the absolute path to `main.py`.
fn main_py_path() -> std::path::PathBuf {
    project_root().join("main.py")
}

// ── Tauri Commands ────────────────────────────────────────────────

/// Launch the Genesis bot in a new process.
/// `profile` is passed to the Python environment as `GENESIS_PROFILE`.
#[tauri::command]
fn launch_bot(profile: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut handle_lock = state.bot_handle.lock().map_err(|_| "Mutex poisoned".to_string())?;

    // If already running, refuse to start again.
    if let Some(ref mut child) = *handle_lock {
        match child.try_wait() {
            Ok(None) => return Err("Bot is already running".to_string()),
            _ => {} // previous run exited; will be overwritten
        }
    }

    let python = python_exe_path();
    let main_py = main_py_path();

    if !python.exists() {
        return Err(format!(
            "Python interpreter not found at: {}.  Please ensure .venv is set up.",
            python.display()
        ));
    }
    if !main_py.exists() {
        return Err(format!("main.py not found at: {}.", main_py.display()));
    }

    let mut cmd = Command::new(&python);
    cmd.arg(&main_py)
        .current_dir(project_root())
        .env("GENESIS_PROFILE", profile)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // On Windows hide the console window so the user doesn't see a flashing cmd.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn bot: {e}"))?;

    *handle_lock = Some(child);
    Ok(())
}

/// Terminate the running bot process.
#[tauri::command]
fn stop_bot(state: State<'_, AppState>) -> Result<(), String> {
    let mut handle_lock = state.bot_handle.lock().map_err(|_| "Mutex poisoned".to_string())?;

    if let Some(mut child) = handle_lock.take() {
        let _ = child.kill();
        let _ = child.wait();
    }

    Ok(())
}

/// Query whether the bot is currently running.
#[tauri::command]
fn bot_status(state: State<'_, AppState>) -> Result<String, String> {
    let mut handle_lock = state
        .bot_handle
        .lock()
        .map_err(|_| "Mutex poisoned".to_string())?;

    if let Some(ref mut child) = *handle_lock {
        match child.try_wait() {
            Ok(None) => return Ok("Running".to_string()),
            _ => {
                // Process exited — clean up the stale handle.
                *handle_lock = None;
                return Ok("Stopped".to_string());
            }
        }
    }

    Ok("Stopped".to_string())
}

/// Open the dashboard URL in the OS default browser.
#[tauri::command]
fn open_dashboard(port: u16) {
    let url = format!("http://127.0.0.1:{port}");
    let _ = open::that(&url);
}

/// Open MetaTrader 5 Terminal.
/// Reads the path from `.env` / `bot/config/settings.py` by invoking Python.
#[tauri::command]
fn open_mt5() -> Result<(), String> {
    let python = python_exe_path();
    if !python.exists() {
        return Err("Python not found — cannot resolve MT5 path".to_string());
    }
    let script = r#"
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from bot.config.settings import settings
print(settings.mt5_path)
"#;
    let output = Command::new(&python)
        .args(["-c", script])
        .current_dir(project_root())
        .output()
        .map_err(|e| format!("Failed to query MT5 path: {e}"))?;

    let path = String::from_utf8(output.stdout)
        .map_err(|e| format!("Invalid UTF-8 from Python: {e}"))?
        .trim()
        .to_string();

    if path.is_empty() || !std::path::Path::new(&path).exists() {
        return Err(format!("MetaTrader 5 not found at: {path}.  Please set the correct path in settings."));
    }

    std::process::Command::new(&path)
        .spawn()
        .map_err(|e| format!("Failed to launch MetaTrader 5: {e}"))?;

    Ok(())
}

// ── Window helpers ──────────────────────────────────────────────

fn focus_window(app: &AppHandle, label: &str) {
    if let Some(window) = app.get_webview_window(label) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn show_dashboard(app: &AppHandle) {
    focus_window(app, "dashboard");
}

fn show_control_center(app: &AppHandle) {
    focus_window(app, "control-center");
}

fn open_ask_claude(app: &AppHandle) {
    if app.get_webview_window("ask-claude").is_some() {
        focus_window(app, "ask-claude");
        return;
    }

    let url = WebviewUrl::App("ask_claude.html".into());
    let _ = WebviewWindowBuilder::new(app, "ask-claude", url)
        .title("Genesis — Ask Claude")
        .inner_size(480.0, 720.0)
        .min_inner_size(360.0, 480.0)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(true)
        .decorations(true)
        .build();
}

// ── Tauri entry point ───────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_control_center(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            launch_bot,
            stop_bot,
            bot_status,
            open_dashboard,
            open_mt5
        ])
        .setup(|app| {
            use tauri::menu::{Menu, MenuItem};
            use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

            // Tray menu items
            let open_cc = MenuItem::with_id(
                app,
                "open-control-center",
                "Open Control Center",
                true,
                None::<&str>,
            )?;
            let open_dash = MenuItem::with_id(
                app,
                "open-dashboard",
                "Open Dashboard",
                true,
                None::<&str>,
            )?;
            let open_ask = MenuItem::with_id(
                app,
                "open-ask-claude",
                "Open Ask-Claude Panel",
                true,
                None::<&str>,
            )?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>,)?;
            let menu = Menu::with_items(app, &[&open_cc, &open_dash, &open_ask, &quit])?;

            let tray_icon = app
                .default_window_icon()
                .cloned()
                .ok_or("default window icon missing — check tauri.conf.json bundle.icon")?;

            let _tray = TrayIconBuilder::with_id("main")
                .icon(tray_icon)
                .tooltip("Genesis Trading Bot")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open-control-center" => show_control_center(app),
                    "open-dashboard" => show_dashboard(app),
                    "open-ask-claude" => open_ask_claude(app),
                    "quit" => app.exit(0),
                    other => log::warn!("unhandled tray menu id: {other}"),
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("control-center") {
                            let _ = window.unminimize();
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
