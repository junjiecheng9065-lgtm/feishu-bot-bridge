"""Entry point: 飞书 Bot 桥. Starts FastAPI on 127.0.0.1, supervisor, tray icon, opens UI."""
import argparse, os, sys, threading, time, webbrowser, multiprocessing as mp


def run():
    mp.freeze_support()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--background", action="store_true", help="不打开窗口（开机自启用）")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--no-autostart-bots", action="store_true", help="不自动启动已启用的机器人")
    a = ap.parse_args()
    from app import core, server
    import uvicorn
    store = core.Store()
    port = a.port or store.data["settings"].get("port", 8765)
    mgr = core.Manager(store)
    if not a.no_autostart_bots: mgr.start_enabled()
    app = server.create_app(store, mgr)
    url = f"http://127.0.0.1:{port}/"
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)

    def open_ui():
        webbrowser.open(url)

    def quit_all(*_):
        mgr.shutdown(); srv.should_exit = True

    if not a.no_tray:
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
            d.ellipse((4, 4, 60, 60), fill=(51, 112, 255)); d.text((22, 22), "桥", fill="white")
            icon = pystray.Icon("feishu-bridge", img, "飞书 Bot 桥", menu=pystray.Menu(
                pystray.MenuItem("打开设置", lambda *_: open_ui(), default=True),
                pystray.MenuItem("退出", lambda i, *_: (quit_all(), i.stop()))))
            threading.Thread(target=icon.run, daemon=True).start()
        except Exception as e:
            print("托盘不可用:", e)
    if not a.background:
        threading.Timer(1.2, open_ui).start()
    print(f"飞书 Bot 桥 运行于 {url}  数据目录 {core.data_dir()}", flush=True)
    try: srv.run()
    finally: mgr.shutdown()


if __name__ == "__main__":
    run()
