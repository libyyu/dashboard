//! 自动更新：检查 → 用户确认 → 签名校验下载 → 停服务 → NSIS 静默安装。
//!
//! 几条不能破的约束：
//! - 检查完全在后台，绝不进入 `spawn::boot()` 链路；失败只写 `desktop-update.log`，
//!   不能让「更新服务器没开」把 dashboard 首屏变成失败页。
//! - 不覆盖 `updater_builder()` 默认的 `on_before_exit`（它负责 `cleanup_before_exit`）。
//!   改成具名三段式：`download()`（内部已 `verify_signature`）→ `stop_launcher()` → `install()`。
//!   这样只有签名合法、且用户已确认的包才会停掉本地服务。
//! - Windows 上 `install()` 内部会 `ShellExecuteW(setup.exe /P /R)` 并 `exit(0)`，
//!   NSIS 的 `/R` 负责拉起新版本，不要再手动 `app.restart()`。
//! - `last-update-check` 写在 profile 目录（安装目录之外），升级覆盖不会把它清掉。

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::UpdaterExt;

use crate::spawn;

/// 自动检查间隔。托盘「检查更新」是 force，会跳过节流。
const CHECK_INTERVAL_SECS: u64 = 24 * 60 * 60;
/// 启动后延迟，避开 launcher 冷启动窗口。
const AUTO_CHECK_DELAY: Duration = Duration::from_secs(10);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn log_line(dir: &Path, msg: &str) {
    let logs = dir.join("logs");
    if fs::create_dir_all(&logs).is_err() {
        return;
    }
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("desktop-update.log"))
    {
        let _ = writeln!(f, "{} {}", now_secs(), msg);
    }
}

/// 展开完整错误源链。顶层 reqwest 错误往往只有一句 "error sending request"，
/// 根因（DNS 解析失败 / TCP 超时 / TLS 拒绝）在 source 链里，写进日志才能定位。
fn error_chain(e: &dyn std::error::Error) -> String {
    let mut chain = vec![e.to_string()];
    let mut cur = e.source();
    while let Some(src) = cur {
        chain.push(src.to_string());
        cur = src.source();
    }
    chain.join(" <- ")
}

/// 办公网 iOA 对未签名进程访问 80/443/8080 等 Web 端口回明文 403（code 190001）：https 下
/// 读到非 TLS 数据报 0x80090308（SEC_E_INVALID_TOKEN）/ InvalidContentType，http 下是
/// 非 2xx → ReleaseNotFound。命中这些特征时给出可操作的提示，而不是让用户猜。
fn interception_hint(detail: &str) -> &'static str {
    let tls_garbage = detail.contains("-2146893048")
        || detail.contains("0x80090308")
        || detail.contains("InvalidContentType")
        || detail.contains("corrupt message");
    let no_release = detail.contains("Could not fetch a valid release JSON");
    if tls_garbage || no_release {
        "\n疑似被 iOA 软件联网管控拦截（未签名程序访问 80/443 等 Web 端口受限）。\
         可在 iOA → 安全 → 软件访问详情 → 添加文件夹，放行 Plateau 安装目录后重试。"
    } else {
        ""
    }
}

fn disabled() -> bool {
    matches!(
        std::env::var("PLATEAU_UPDATE_DISABLE").as_deref(),
        Ok("1") | Ok("true")
    )
}

/// 后续自行部署时，改 tauri.conf.json 或设这个环境变量即可切换端点。
fn endpoint_override() -> Option<String> {
    std::env::var("PLATEAU_UPDATE_ENDPOINT")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn due(dir: &Path, force: bool) -> bool {
    if force {
        return true;
    }
    let Ok(text) = fs::read_to_string(dir.join("last-update-check")) else {
        return true;
    };
    let Ok(last) = text.trim().parse::<u64>() else {
        return true;
    };
    now_secs().saturating_sub(last) >= CHECK_INTERVAL_SECS
}

/// 只在成功连上端点并拿到响应后打点；失败不打点，下次启动继续试。
fn mark_checked(dir: &Path) {
    let _ = fs::write(dir.join("last-update-check"), now_secs().to_string());
}

pub fn schedule(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(AUTO_CHECK_DELAY);
        let _ = tauri::async_runtime::block_on(check(app, false));
    });
}

pub fn check_now(app: AppHandle) {
    std::thread::spawn(move || {
        let _ = tauri::async_runtime::block_on(check(app, true));
    });
}

fn notify(app: &AppHandle, title: &str, message: &str) {
    let _ = app
        .dialog()
        .message(message)
        .title(title)
        .buttons(MessageDialogButtons::Ok)
        .blocking_show();
}

async fn check(app: AppHandle, force: bool) -> Result<(), String> {
    let dir = spawn::profile_dir().ok_or("无法确定用户目录")?;
    if disabled() {
        log_line(&dir, "检查跳过：PLATEAU_UPDATE_DISABLE 已设置");
        return Ok(());
    }
    if !due(&dir, force) {
        return Ok(());
    }

    let result = check_remote(&app).await;
    if result.is_ok() {
        mark_checked(&dir);
    }
    let update = match result {
        Ok(update) => update,
        Err(e) => {
            log_line(&dir, &format!("检查失败：{e}"));
            if force {
                notify(&app, "Plateau 更新", &format!("检查更新失败：\n{e}\n\n服务不受影响，可稍后重试。"));
            }
            return Err(e);
        }
    };

    let Some(update) = update else {
        log_line(&dir, "已是最新版本");
        if force {
            notify(&app, "Plateau 更新", "当前已是最新版本。");
        }
        return Ok(());
    };

    let notes = update.body.as_deref().unwrap_or("（无更新说明）").trim();
    let message = format!(
        "发现新版本 {new}（当前 {current}）。\n\n更新说明：\n{notes}\n\n\
         安装会临时停止本地服务，完成后自动重启 Plateau；用户数据不会丢失。",
        new = update.version,
        current = update.current_version,
    );
    log_line(&dir, &format!("发现新版本 {} (当前 {})", update.version, update.current_version));

    // 后台线程里跑，符合 blocking_show「不要在主线程上下文调用」的要求。
    let confirmed = app
        .dialog()
        .message(&message)
        .title("Plateau 更新")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "立即安装".into(),
            "稍后".into(),
        ))
        .blocking_show();
    if !confirmed {
        log_line(&dir, "用户选择稍后安装");
        return Ok(());
    }

    // download() 末尾会 verify_signature；只有签名通过才往下走。
    let mut downloaded: u64 = 0;
    let bytes = update
        .download(
            |chunk, _total| downloaded += chunk as u64,
            || {},
        )
        .await
        .map_err(|e| {
            log_line(&dir, &format!("下载/签名校验失败：{}", error_chain(&e)));
            format!("下载或签名校验失败：{e}")
        })?;
    log_line(&dir, &format!("下载完成 {} bytes（累计 {}），签名校验通过", bytes.len(), downloaded));

    // 释放安装目录里 launcher.exe / dashboard.exe / cb.exe 的文件锁。
    spawn::stop_launcher();
    log_line(&dir, "本地服务已有序关停，准备安装");

    match update.install(bytes) {
        Ok(()) => {
            // Windows 正常路径不会走到这里（install 内部已 exit(0)）。
            log_line(&dir, "安装调用返回，等待 NSIS 重启应用");
            Ok(())
        }
        Err(e) => {
            let msg = format!("安装失败：{e}");
            log_line(&dir, &msg);
            // 服务已经停了，尽量恢复成可用状态，避免用户留在「转圈」界面。
            let handle = app.clone();
            std::thread::spawn(move || spawn::boot(&handle));
            notify(&app, "Plateau 更新", &format!("{msg}\n\n已尝试恢复本地服务，当前仍是旧版本。"));
            Err(msg)
        }
    }
}

async fn check_remote(
    app: &AppHandle,
) -> Result<Option<tauri_plugin_updater::Update>, String> {
    let mut builder = app.updater_builder().timeout(REQUEST_TIMEOUT);
    if let Some(raw) = endpoint_override() {
        let url = tauri::Url::parse(&raw).map_err(|e| format!("PLATEAU_UPDATE_ENDPOINT 非法：{e}"))?;
        builder = builder
            .endpoints(vec![url])
            .map_err(|e| format!("更新端点校验失败：{e}"))?;
    }
    let updater = builder.build().map_err(|e| format!("updater 初始化失败：{e}"))?;
    updater
        .check()
        .await
        .map_err(|e| {
            let detail = error_chain(&e);
            format!("检查更新请求失败：{detail}{}", interception_hint(&detail))
        })
}
