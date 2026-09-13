use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const READY_PREFIX: &str = "NODEX_READY ";

#[derive(Clone, Serialize)]
struct ApiBase {
    url: String,
    token: String,
}

#[derive(Default)]
struct Engine {
    base: Mutex<Option<ApiBase>>,
    child: Mutex<Option<CommandChild>>,
    failure: Mutex<Option<String>>,
}

/// The engine's address, once the sidecar has announced it. The UI polls this.
#[tauri::command]
fn api_base(state: tauri::State<'_, Engine>) -> Result<Option<ApiBase>, String> {
    if let Some(reason) = state.failure.lock().unwrap().clone() {
        return Err(reason);
    }
    Ok(state.base.lock().unwrap().clone())
}

fn start_engine(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let token = uuid::Uuid::new_v4().simple().to_string();
    let parent = std::process::id().to_string();

    let command = app
        .shell()
        .sidecar("nodex-sidecar")?
        .args(["--port", "0", "--token", &token, "--parent-pid", &parent]);
    let (mut events, child) = command.spawn()?;
    app.state::<Engine>().child.lock().unwrap().replace(child);

    let handle = app.handle().clone();
    tauri::async_runtime::spawn(async move {
        let mut buffer = String::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    buffer.push_str(&String::from_utf8_lossy(&bytes));
                    while let Some(end) = buffer.find('\n') {
                        let line: String = buffer.drain(..=end).collect();
                        let Some(json) = line.trim().strip_prefix(READY_PREFIX) else { continue };
                        let Ok(value) = serde_json::from_str::<serde_json::Value>(json) else { continue };
                        if let Some(port) = value.get("port").and_then(|p| p.as_u64()) {
                            handle.state::<Engine>().base.lock().unwrap().replace(ApiBase {
                                url: format!("http://127.0.0.1:{port}"),
                                token: token.clone(),
                            });
                        }
                    }
                }
                CommandEvent::Terminated(status) => {
                    let engine = handle.state::<Engine>();
                    engine.base.lock().unwrap().take();
                    engine
                        .failure
                        .lock()
                        .unwrap()
                        .replace(format!("The Nodex engine stopped (exit code {:?}).", status.code));
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Engine::default())
        .invoke_handler(tauri::generate_handler![api_base])
        .setup(|app| {
            if let Err(error) = start_engine(app) {
                app.state::<Engine>()
                    .failure
                    .lock()
                    .unwrap()
                    .replace(format!("Could not start the Nodex engine: {error}"));
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Nodex window");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = handle.state::<Engine>().child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
