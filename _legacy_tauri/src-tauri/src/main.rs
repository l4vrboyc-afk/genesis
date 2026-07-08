// Prevent the extra console window on Windows release builds.
// Harmless in `tauri dev` (where debug_assertions is on anyway).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    desktop_lib::run()
}
