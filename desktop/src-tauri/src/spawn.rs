//! launcher sidecar 编排：spawn / 探活导航 / 失败可见 / 退出杀进程树。

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::Manager;

static LAUNCHER: Mutex<Option<Child>> = Mutex::new(None);

fn profile() -> String {
    std::env::var("PLATEAU_PROFILE").unwrap_or_else(|_| "default".to_string())
}

/// 与 backend/app/runtime/ports.py::home_dir() 语义严格一致：
/// PLATEAU_HOME 若设置，本身就是 ~/.plateau-dashboard 这个根目录，不能再拼一层。
pub(crate) fn profile_dir() -> Option<PathBuf> {
    let root = match std::env::var("PLATEAU_HOME") {
        Ok(raw) => PathBuf::from(raw),
        Err(_) => dirs::home_dir()?.join(".plateau-dashboard"),
    };
    Some(root.join("profiles").join(profile()))
}

/// launcher.exe 位于资源目录 binaries/launcher/ 下。
/// 打包态走 resource_dir；开发态（cargo build 直接跑）resource_dir 不可靠，
/// 用编译期 CARGO_MANIFEST_DIR（= src-tauri）兜底。
fn launcher_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("binaries").join("launcher").join("launcher.exe"));
    }
    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join("launcher")
            .join("launcher.exe"),
    );
    candidates.into_iter().find(|p| p.is_file())
}

fn run_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{}-{nanos}", std::process::id())
}

fn append_log(path: &Path) -> Stdio {
    match OpenOptions::new().create(true).append(true).open(path) {
        Ok(file) => Stdio::from(file),
        Err(_) => Stdio::null(),
    }
}

pub fn boot(app: &tauri::AppHandle) {
    let id = run_id();
    if let Err(e) = spawn_launcher(app, &id) {
        report_failure(app, &format!("launcher 启动失败：{e}"));
        return;
    }
    wait_and_navigate(app, &id);
}

fn spawn_launcher(app: &tauri::AppHandle, id: &str) -> Result<(), String> {
    let path = launcher_path(app).ok_or_else(|| {
        let res = app
            .path()
            .resource_dir()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "<err>".into());
        let diag = std::env::temp_dir().join("plateau-spawn-diag.txt");
        let _ = std::fs::write(&diag, format!("resource_dir={res}"));
        "launcher.exe 未找到".to_string()
    })?;

    let dir = profile_dir().ok_or("无法确定用户目录")?;
    let logs = dir.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let stdout_log = logs.join("desktop-launcher-stdout.log");
    let stderr_log = logs.join("desktop-launcher-stderr.log");
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&stderr_log) {
        let _ = writeln!(f, "=== run {id} ===");
    }

    let mut cmd = std::process::Command::new(path);
    cmd.env("PLATEAU_PROFILE", profile())
        .env("PLATEAU_RUN_ID", id)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        // 无控制台窗口，stdout/stderr 全部落盘：冻结 launcher 的崩溃栈不再丢失。
        .stdout(append_log(&stdout_log))
        .stderr(append_log(&stderr_log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd
        .spawn()
        .map_err(|e| format!("spawn launcher 失败: {e}"))?;
    *LAUNCHER.lock().unwrap() = Some(child);
    Ok(())
}

/// launcher 提前退出时立即停止等待（try_wait 会回收子进程句柄）。
fn launcher_exited() -> bool {
    let mut guard = LAUNCHER.lock().unwrap();
    match guard.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(Some(_))),
        None => true,
    }
}

/// 只读本次运行的 launcher.ready.json；run_id 不匹配说明是历史残留，忽略。
fn ready_port(dir: &Path, id: &str) -> Option<u16> {
    let text = std::fs::read_to_string(dir.join("launcher.ready.json")).ok()?;
    let v: serde_json::Value = serde_json::from_str(&text).ok()?;
    if v.get("run_id")?.as_str()? != id {
        return None;
    }
    v.get("ports")?.get("dashboard")?.as_u64().map(|p| p as u16)
}

fn probe(port: u16) -> bool {
    reqwest::blocking::Client::new()
        .get(format!("http://127.0.0.1:{port}/health/liveness"))
        .timeout(Duration::from_secs(2))
        .send()
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

/// 失败必须可见：窗口不再无限转圈，同时落盘诊断文件与 stderr 日志路径。
fn report_failure(app: &tauri::AppHandle, msg: &str) {
    let dir = profile_dir();
    let full = match &dir {
        Some(d) => format!(
            "{msg}\n\n诊断日志目录：{}\n（desktop-launcher-stderr.log 崩溃栈 / launcher-*.log 结构化事件）",
            d.join("logs").display()
        ),
        None => msg.to_string(),
    };
    if let Some(d) = &dir {
        let _ = std::fs::create_dir_all(d);
        let _ = std::fs::write(d.join("desktop-error.txt"), &full);
    }
    eprintln!("[plateau] {full}");
    if let Some(w) = app.get_webview_window("main") {
        let arg = serde_json::to_string(&full).unwrap_or_else(|_| "\"\"".into());
        let _ = w.eval(format!("window.__plateauFail && window.__plateauFail({arg});"));
    }
}

/// 最多等 60s：等本次 run 的 ready 标记 + 探活，成功后导航到 dashboard。
fn wait_and_navigate(app: &tauri::AppHandle, id: &str) {
    let dir = profile_dir();
    for _ in 0..120 {
        if launcher_exited() {
            report_failure(app, "launcher 提前退出（单实例锁 / 端口冲突 / 启动异常），服务未拉起。");
            return;
        }
        if let Some(d) = &dir {
            if let Some(port) = ready_port(d, id) {
                if probe(port) {
                    let url = format!("http://127.0.0.1:{port}/console");
                    if let Some(w) = app.get_webview_window("main") {
                        if let Ok(u) = tauri::Url::parse(&url) {
                            let _ = w.navigate(u);
                        }
                    }
                    return;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    report_failure(app, "dashboard 未在 60 秒内就绪。");
}

/// 托盘「打开日志目录」：直接把用户带到本次 profile 的日志目录。
pub fn open_logs_dir(_app: &tauri::AppHandle) {
    let Some(logs) = profile_dir().map(|d| d.join("logs")) else {
        return;
    };
    let _ = std::fs::create_dir_all(&logs);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let mut cmd = std::process::Command::new("explorer");
        cmd.arg(&logs);
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.spawn();
    }
    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        let _ = std::process::Command::new(opener).arg(&logs).spawn();
    }
}

/// 优雅退出：先写 launcher.stop 让 launcher 走 atexit（有序关停 dashboard/cb、
/// 清 lock/ready），等最多 8s；超时才 fallback 到强杀进程树。
pub fn stop_launcher() {
    if let Some(dir) = profile_dir() {
        let _ = std::fs::write(dir.join("launcher.stop"), "1");
    }
    let mut exited = false;
    let deadline = Instant::now() + Duration::from_secs(8);
    while Instant::now() < deadline {
        if launcher_exited() {
            exited = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    if let Some(mut child) = LAUNCHER.lock().unwrap().take() {
        if !exited {
            let _ = std::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &child.id().to_string()])
                .output();
            let _ = child.kill();
        }
        let _ = child.wait();
    }
}
