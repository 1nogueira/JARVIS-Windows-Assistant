#[cfg(not(debug_assertions))]
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    thread,
    time::Duration,
};
use std::{process::Command as StdCommand, sync::Mutex};
use tauri::{
    menu::{Menu, MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    Emitter, LogicalSize, Manager, Size, State, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct SessionToken(String);

fn tray_labels(language: &str) -> [&'static str; 5] {
    if language == "en-US" {
        [
            "Open JARVIS",
            "Toggle microphone",
            "Mini mode",
            "Settings",
            "Quit",
        ]
    } else {
        [
            "Abrir JARVIS",
            "Ativar/Desativar microfone",
            "Modo mini",
            "Configurações",
            "Sair",
        ]
    }
}

fn tray_menu<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    language: &str,
) -> tauri::Result<Menu<R>> {
    let labels = tray_labels(language);
    let open = MenuItemBuilder::with_id("open", labels[0]).build(app)?;
    let microphone = MenuItemBuilder::with_id("microphone", labels[1]).build(app)?;
    let mini = MenuItemBuilder::with_id("mini", labels[2]).build(app)?;
    let settings = MenuItemBuilder::with_id("settings", labels[3]).build(app)?;
    let quit = MenuItemBuilder::with_id("quit", labels[4]).build(app)?;
    MenuBuilder::new(app)
        .items(&[&open, &microphone, &mini, &settings, &quit])
        .build()
}

#[tauri::command]
fn set_interface_language(app: tauri::AppHandle, language: String) -> Result<(), String> {
    if language != "pt-BR" && language != "en-US" {
        return Err("Unsupported language".to_owned());
    }
    let tray = app.tray_by_id("jarvis").ok_or("Tray not available")?;
    let menu = tray_menu(&app, &language).map_err(|error| error.to_string())?;
    tray.set_menu(Some(menu))
        .map_err(|error| error.to_string())?;
    let tooltip = if language == "en-US" {
        "JARVIS — Local assistant"
    } else {
        "JARVIS — Assistente local"
    };
    tray.set_tooltip(Some(tooltip))
        .map_err(|error| error.to_string())
}

#[cfg(not(debug_assertions))]
struct OwnedBackend {
    child: CommandChild,
    pid: u32,
}

#[cfg(not(debug_assertions))]
struct BackendProcess(Mutex<Option<OwnedBackend>>);

#[derive(Clone, Copy)]
struct InterfaceState {
    always_on_top: bool,
    minimize_to_tray: bool,
    mini: bool,
}

struct InterfacePreferences(Mutex<InterfaceState>);

impl Default for InterfacePreferences {
    fn default() -> Self {
        Self(Mutex::new(InterfaceState {
            always_on_top: false,
            minimize_to_tray: true,
            mini: false,
        }))
    }
}

#[tauri::command]
fn get_session_token(token: State<'_, SessionToken>) -> String {
    token.0.clone()
}

#[tauri::command]
fn set_mini_mode(
    window: WebviewWindow,
    preferences: State<'_, InterfacePreferences>,
    mini: bool,
) -> Result<(), String> {
    let always_on_top = {
        let mut state = preferences
            .0
            .lock()
            .map_err(|_| "interface lock poisoned")?;
        state.mini = mini;
        state.always_on_top
    };
    apply_mini_mode(window, mini, always_on_top)
}

#[tauri::command]
fn apply_interface_settings(
    window: WebviewWindow,
    preferences: State<'_, InterfacePreferences>,
    always_on_top: bool,
    minimize_to_tray: bool,
) -> Result<(), String> {
    let mini = {
        let mut state = preferences
            .0
            .lock()
            .map_err(|_| "interface lock poisoned")?;
        state.always_on_top = always_on_top;
        state.minimize_to_tray = minimize_to_tray;
        state.mini
    };
    window
        .set_always_on_top(mini || always_on_top)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_start_with_windows(enabled: bool) -> Result<(), String> {
    let key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
    let mut command = StdCommand::new("reg.exe");
    if enabled {
        let executable = std::env::current_exe().map_err(|error| error.to_string())?;
        let value = format!("\"{}\"", executable.display());
        command.args([
            "add", key, "/v", "JARVIS", "/t", "REG_SZ", "/d", &value, "/f",
        ]);
    } else {
        command.args(["delete", key, "/v", "JARVIS", "/f"]);
    }
    let output = command.output().map_err(|error| error.to_string())?;
    if output.status.success() || (!enabled && output.status.code() == Some(1)) {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_owned())
    }
}

fn apply_mini_mode<R: tauri::Runtime>(
    window: WebviewWindow<R>,
    mini: bool,
    configured_always_on_top: bool,
) -> Result<(), String> {
    let size = if mini {
        LogicalSize::new(430.0, 290.0)
    } else {
        LogicalSize::new(1120.0, 760.0)
    };
    window
        .set_size(Size::Logical(size))
        .map_err(|error| error.to_string())?;
    window
        .set_always_on_top(effective_always_on_top(mini, configured_always_on_top))
        .map_err(|error| error.to_string())?;
    window
        .set_resizable(!mini)
        .map_err(|error| error.to_string())?;
    window.center().map_err(|error| error.to_string())?;
    Ok(())
}

fn effective_always_on_top(mini: bool, configured: bool) -> bool {
    mini || configured
}

pub fn run() {
    let push_to_talk = Shortcut::new(Some(Modifiers::CONTROL), Code::Space);
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SessionToken(runtime_session_token()))
        .manage(InterfacePreferences::default());
    #[cfg(not(debug_assertions))]
    let builder = builder.manage(BackendProcess(Mutex::new(None)));
    builder
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut.matches(Modifiers::CONTROL, Code::Space)
                        && event.state() == ShortcutState::Pressed
                    {
                        let _ = app.emit("shortcut://push-to-talk", ());
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            get_session_token,
            set_mini_mode,
            apply_interface_settings,
            set_interface_language,
            set_start_with_windows
        ])
        .setup(move |app| {
            #[cfg(not(debug_assertions))]
            if let Err(error) = start_bundled_backend(app) {
                write_startup_diagnostic(&format!("falha de startup: {error}"));
                return Err(error);
            }
            #[cfg(not(debug_assertions))]
            write_startup_diagnostic("backend autenticado; inicialização concluída");
            app.global_shortcut().register(push_to_talk)?;

            let menu = tray_menu(app.handle(), "pt-BR")?;
            let mut tray_builder = TrayIconBuilder::with_id("jarvis");
            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }
            tray_builder
                .tooltip("JARVIS — Assistente local")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => restore_main(app),
                    "microphone" => {
                        let _ = app.emit("shortcut://push-to-talk", ());
                    }
                    "mini" => {
                        show_main(app);
                        let _ = app.emit("navigate://home", ());
                        set_mini_for_app(app, true);
                    }
                    "settings" => {
                        restore_main(app);
                        let _ = app.emit("navigate://settings", ());
                    }
                    "quit" => {
                        #[cfg(not(debug_assertions))]
                        stop_bundled_backend(app);
                        app.exit(0)
                    }
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let minimize_to_tray = window
                    .app_handle()
                    .state::<InterfacePreferences>()
                    .0
                    .lock()
                    .map(|state| state.minimize_to_tray)
                    .unwrap_or(true);
                if minimize_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    #[cfg(not(debug_assertions))]
                    stop_bundled_backend(&window.app_handle());
                    window.app_handle().exit(0);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("falha ao iniciar a aplicação JARVIS");
}

fn runtime_session_token() -> String {
    if let Ok(value) = std::env::var("JARVIS_SESSION_TOKEN") {
        let trimmed = value.trim();
        if (32..=256).contains(&trimmed.len())
            && trimmed
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return trimmed.to_owned();
        }
    }
    let mut bytes = [0_u8; 48];
    getrandom::fill(&mut bytes).expect("não foi possível gerar a credencial local");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(not(debug_assertions))]
fn start_bundled_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let address: SocketAddr = "127.0.0.1:8742".parse()?;
    recover_stale_backend_port(address)?;
    let token = app.state::<SessionToken>().0.clone();
    let (_receiver, child) = app
        .shell()
        .sidecar("jarvis-backend")?
        .env("JARVIS_SESSION_TOKEN", &token)
        .env("JARVIS_HOST", "127.0.0.1")
        .spawn()?;
    let pid = child.pid();
    let mut authenticated = false;
    // A signed PyInstaller one-file sidecar can need more than ten seconds to
    // extract on its first launch. Poll for up to one minute, but proceed as
    // soon as the authenticated identity and process ownership are proven.
    for _ in 0..240 {
        if let Some(identity) = running_backend_identity(address, &token) {
            if backend_identity_matches(&identity, env!("CARGO_PKG_VERSION"), pid) {
                authenticated = true;
                break;
            }
        }
        thread::sleep(Duration::from_millis(250));
    }
    if !authenticated {
        let _ = child.kill();
        return Err("O sidecar iniciado não comprovou identidade, versão e PID esperados.".into());
    }
    *app.state::<BackendProcess>()
        .0
        .lock()
        .expect("backend lock poisoned") = Some(OwnedBackend { child, pid });
    Ok(())
}

#[cfg(not(debug_assertions))]
fn port_is_open(address: SocketAddr) -> bool {
    TcpStream::connect_timeout(&address, Duration::from_millis(300)).is_ok()
}

#[cfg(not(debug_assertions))]
fn recover_stale_backend_port(address: SocketAddr) -> Result<(), String> {
    if !port_is_open(address) {
        return Ok(());
    }
    let current_pid = std::process::id();
    let desktop_pids = process_ids_named("jarvis-desktop.exe");
    if desktop_pids.iter().any(|pid| *pid != current_pid) {
        return Err("Outra instância do JARVIS já está em execução.".to_owned());
    }
    let backend_pids = process_ids_named("jarvis-backend.exe");
    if backend_pids.is_empty() {
        return Err(
            "A porta 8742 está ocupada por outro programa; nenhum backend JARVIS pôde ser recuperado."
                .to_owned(),
        );
    }
    write_startup_diagnostic(&format!(
        "backend órfão detectado na porta 8742: PIDs {backend_pids:?}"
    ));
    for pid in backend_pids {
        // PyInstaller keeps a parent and a child with the same executable name.
        // Killing either process tree can therefore remove a PID that is still
        // present in this snapshot. Treat that race as recovered, not as a
        // startup failure.
        if !process_ids_named("jarvis-backend.exe").contains(&pid) {
            continue;
        }
        let mut command = StdCommand::new("taskkill.exe");
        command.args(["/PID", &pid.to_string(), "/T", "/F"]);
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
        let status = command
            .status()
            .map_err(|error| format!("não foi possível encerrar backend órfão {pid}: {error}"))?;
        if !status.success() && process_ids_named("jarvis-backend.exe").contains(&pid) {
            return Err(format!("o backend órfão {pid} não pôde ser encerrado"));
        }
    }
    for _ in 0..40 {
        if !port_is_open(address) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(125));
    }
    Err("A porta 8742 continuou ocupada após encerrar o backend órfão.".to_owned())
}

#[cfg(not(debug_assertions))]
fn process_ids_named(expected_name: &str) -> Vec<u32> {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, INVALID_HANDLE_VALUE},
        System::Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
            TH32CS_SNAPPROCESS,
        },
    };
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Vec::new();
    }
    let mut entry = PROCESSENTRY32W::default();
    entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
    let mut matches = Vec::new();
    unsafe {
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                let length = entry
                    .szExeFile
                    .iter()
                    .position(|value| *value == 0)
                    .unwrap_or(entry.szExeFile.len());
                let name = String::from_utf16_lossy(&entry.szExeFile[..length]);
                if name.eq_ignore_ascii_case(expected_name) {
                    matches.push(entry.th32ProcessID);
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
    }
    matches
}

#[cfg(not(debug_assertions))]
fn write_startup_diagnostic(message: &str) {
    let Ok(executable) = std::env::current_exe() else {
        return;
    };
    let Some(directory) = executable.parent() else {
        return;
    };
    let logs = directory.join("logs");
    if fs::create_dir_all(&logs).is_err() {
        return;
    }
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("startup.log"))
    {
        let _ = writeln!(file, "{message}");
    }
}

#[cfg(not(debug_assertions))]
struct BackendIdentity {
    version: String,
    instance_id: String,
    pid: u32,
}

#[cfg(not(debug_assertions))]
fn backend_identity_matches(identity: &BackendIdentity, version: &str, pid: u32) -> bool {
    identity.version == version
        && process_is_owned_by(identity.pid, pid)
        && !identity.instance_id.is_empty()
}

#[cfg(not(debug_assertions))]
fn process_is_owned_by(candidate_pid: u32, root_pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, INVALID_HANDLE_VALUE},
        System::Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
            TH32CS_SNAPPROCESS,
        },
    };

    if candidate_pid == root_pid {
        return true;
    }
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return false;
    }
    let mut entry = PROCESSENTRY32W::default();
    entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
    let mut parents = Vec::new();
    unsafe {
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                parents.push((entry.th32ProcessID, entry.th32ParentProcessID));
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
    }
    process_tree_contains(&parents, candidate_pid, root_pid)
}

#[cfg(any(not(debug_assertions), test))]
fn process_tree_contains(parents: &[(u32, u32)], candidate_pid: u32, root_pid: u32) -> bool {
    let mut current = candidate_pid;
    for _ in 0..8 {
        if current == root_pid {
            return true;
        }
        let Some((_, parent)) = parents.iter().find(|(pid, _)| *pid == current) else {
            return false;
        };
        if *parent == 0 || *parent == current {
            return false;
        }
        current = *parent;
    }
    false
}

#[cfg(not(debug_assertions))]
fn running_backend_identity(address: SocketAddr, token: &str) -> Option<BackendIdentity> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(300)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    stream
        .set_write_timeout(Some(Duration::from_millis(300)))
        .ok()?;
    let request = format!(
        "GET /api/identity HTTP/1.1\r\nHost: 127.0.0.1:8742\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = Vec::new();
    let _ = stream.read_to_end(&mut response);
    if !response.starts_with(b"HTTP/1.1 200") && !response.starts_with(b"HTTP/1.0 200") {
        return None;
    }
    let separator = response.windows(4).position(|part| part == b"\r\n\r\n")? + 4;
    let payload: serde_json::Value = serde_json::from_slice(&response[separator..]).ok()?;
    Some(BackendIdentity {
        version: payload.get("version")?.as_str()?.to_owned(),
        instance_id: payload.get("instance_id")?.as_str()?.to_owned(),
        pid: u32::try_from(payload.get("pid")?.as_u64()?).ok()?,
    })
}

#[cfg(not(debug_assertions))]
fn stop_bundled_backend<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(owned) = app
        .state::<BackendProcess>()
        .0
        .lock()
        .expect("backend lock poisoned")
        .take()
    {
        debug_assert_eq!(owned.pid, owned.child.pid());
        let _ = owned.child.kill();
    }
}

fn show_main<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn set_mini_for_app<R: tauri::Runtime>(app: &tauri::AppHandle<R>, mini: bool) {
    let always_on_top = app
        .state::<InterfacePreferences>()
        .0
        .lock()
        .map(|mut state| {
            state.mini = mini;
            state.always_on_top
        })
        .unwrap_or(false);
    if let Some(window) = app.get_webview_window("main") {
        let _ = apply_mini_mode(window, mini, always_on_top);
    }
}

fn restore_main<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    show_main(app);
    set_mini_for_app(app, false);
}

#[cfg(test)]
mod tests {
    use super::{effective_always_on_top, process_tree_contains};

    #[test]
    fn tray_labels_follow_the_selected_language() {
        assert_eq!(super::tray_labels("en-US")[3], "Settings");
        assert_eq!(super::tray_labels("pt-BR")[3], "Configurações");
    }

    #[test]
    fn mini_mode_has_precedence_over_normal_always_on_top_setting() {
        assert!(!effective_always_on_top(false, false));
        assert!(effective_always_on_top(false, true));
        assert!(effective_always_on_top(true, false));
        assert!(effective_always_on_top(true, true));
    }

    #[test]
    fn bundled_backend_identity_must_belong_to_spawned_process_tree() {
        let process_tree = [(200, 100), (300, 200), (400, 999)];
        assert!(process_tree_contains(&process_tree, 100, 100));
        assert!(process_tree_contains(&process_tree, 300, 100));
        assert!(!process_tree_contains(&process_tree, 400, 100));
        assert!(!process_tree_contains(&process_tree, 1234, 100));
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn occupied_non_jarvis_port_is_rejected_deterministically() {
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").expect("bind fake server");
        let address = listener.local_addr().expect("fake server address");
        assert!(super::recover_stale_backend_port(address).is_err());
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn identity_requires_version_owned_pid_and_instance_nonce() {
        let valid = super::BackendIdentity {
            version: "0.3.6".to_owned(),
            instance_id: "instance-nonce".to_owned(),
            pid: 1234,
        };
        assert!(super::backend_identity_matches(&valid, "0.3.6", 1234));
        assert!(!super::backend_identity_matches(&valid, "0.3.6", 4321));

        let empty_nonce = super::BackendIdentity {
            instance_id: String::new(),
            ..valid
        };
        assert!(!super::backend_identity_matches(
            &empty_nonce,
            "0.3.6",
            1234
        ));
    }
}
