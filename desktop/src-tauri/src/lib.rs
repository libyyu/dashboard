mod spawn;

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            // 旁路后台线程：spawn launcher.exe + 等 dashboard 就绪后导航窗口，
            // 不阻塞 WebView 首帧（借鉴 EvoFlow 的 sidecar-boot 思路，fail-open）。
            let handle = app.handle().clone();
            std::thread::spawn(move || spawn::boot(&handle));
            Ok(())
        })
        .on_window_event(|window, event| {
            use tauri::WindowEvent;
            if let WindowEvent::CloseRequested { .. } = event {
                if window.label() == "main" {
                    spawn::stop_launcher();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Plateau");
}
