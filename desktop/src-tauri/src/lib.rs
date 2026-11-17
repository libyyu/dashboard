mod spawn;
mod updater;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, WindowEvent};

fn focus_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

pub fn run() {
    tauri::Builder::default()
        // 第二个实例不再自己拉一套 launcher，而是把已有窗口唤到前台。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_main(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // 旁路后台线程：spawn launcher.exe + 等 dashboard 就绪后导航窗口。
            let handle = app.handle().clone();
            std::thread::spawn(move || spawn::boot(&handle));

            // 更新检查独立后台线程，失败只写日志，绝不阻塞/污染首屏。
            updater::schedule(app.handle().clone());

            let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
            let logs = MenuItem::with_id(app, "logs", "打开日志目录", true, None::<&str>)?;
            let update = MenuItem::with_id(app, "update", "检查更新", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出 Plateau", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &logs, &update, &quit])?;

            let mut tray = TrayIconBuilder::new().menu(&menu);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            let _tray = tray
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => focus_main(app),
                    "logs" => spawn::open_logs_dir(app),
                    "update" => updater::check_now(app.clone()),
                    "quit" => {
                        spawn::stop_launcher();
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // 关窗只隐藏到托盘，服务继续跑；托盘「退出 Plateau」才做有序关停。
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Plateau");
}
