"""Entry point: 飞书 Bot 桥 desktop app. FastAPI on 127.0.0.1 + native pywebview window + tray.
Falls back to the system browser if no webview backend is available."""
import argparse, json, logging, os, sys, threading, time, urllib.request, webbrowser, multiprocessing as mp

log = logging.getLogger("app")
TITLE = "飞书 Bot 桥"


def _res(*p):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for c in (os.path.join(base, *p), os.path.join(base, "app", *p)):
        if os.path.exists(c): return c
    return None


def _existing(port, show=True):
    """Single instance: if our app already listens on port, ask it to show its window."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            if json.load(r).get("app") != "FeishuBotBridge": return False
        if show: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/show", method="POST"), timeout=3)
        return True
    except Exception:
        return False


def _win_mutex():
    if sys.platform != "win32": return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\FeishuBotBridgeSingleton")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def run():
    mp.freeze_support()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--background", action="store_true", help="最小化到托盘启动（开机自启用）")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--browser", action="store_true", help="不用原生窗口，改用浏览器")
    ap.add_argument("--no-autostart-bots", action="store_true", help="不自动启动已启用的机器人")
    a = ap.parse_args()
    from app import core, server, autostart
    import uvicorn
    store = core.Store()
    port = a.port or store.data["settings"].get("port", 8765)
    url = f"http://127.0.0.1:{port}/"

    first = _win_mutex()
    if not first or _existing(port, not a.background):
        if not first and not a.background: _existing(port)
        log.info("已有实例在运行，已唤起其窗口，退出"); return

    # autostart entry from older versions may lack --background semantics; keep it pointing at this exe
    try:
        if autostart.is_enabled(): autostart.set_enabled(True)
    except Exception as e: log.warning("刷新自启项失败: %s", e)

    st = {"win": None, "quitting": False}

    def show():
        w = st["win"]
        if w is not None:
            try: w.show(); w.restore()
            except Exception: pass
            try: w.on_top = True; w.on_top = False
            except Exception: pass
        else:
            webbrowser.open(url)

    mgr = core.Manager(store)
    if not a.no_autostart_bots: mgr.start_enabled()
    app = server.create_app(store, mgr, on_show=show)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    threading.Thread(target=srv.run, daemon=True, name="api").start()
    for _ in range(100):
        if srv.started: break
        time.sleep(0.1)

    def quit_all(*_):
        st["quitting"] = True
        try: mgr.shutdown()
        except Exception: pass
        srv.should_exit = True
        if st["win"] is not None:
            try: st["win"].destroy()
            except Exception: pass

    icon = None
    if not a.no_tray and not (sys.platform == "darwin" and not a.browser):  # macOS: tray must own main thread; window used instead
        try:
            import pystray
            from PIL import Image
            p = _res("assets", "icon.png") or _res("ui", "icon.png")
            img = Image.open(p) if p else Image.new("RGBA", (64, 64), (51, 112, 255, 255))
            icon = pystray.Icon("feishu-bridge", img, TITLE, menu=pystray.Menu(
                pystray.MenuItem("打开", lambda *_: show(), default=True),
                pystray.MenuItem("退出", lambda i, *_: (quit_all(), i.stop()))))
            threading.Thread(target=icon.run, daemon=True, name="tray").start()
        except Exception as e:
            log.warning("托盘不可用: %s", e); icon = None

    print(f"{TITLE} v{core.VERSION} 运行于 {url}  数据目录 {core.data_dir()}", flush=True)
    native = False
    if not a.browser:
        try:
            import webview
            w = webview.create_window(f"{TITLE} · v{core.VERSION}", url, width=1240, height=820, min_size=(960, 620),
                                      hidden=a.background, background_color="#F5F7FA")
            st["win"] = w

            def on_closing():
                if st["quitting"] or icon is None: return True
                w.hide(); return False  # close → hide to tray
            w.events.closing += on_closing
            native = True
            ico = _res("assets", "icon.ico" if sys.platform == "win32" else "icon.png")
            log.info("启动原生窗口 (background=%s)", a.background)
            kw = {"icon": ico} if ico and sys.platform.startswith("linux") else {}
            webview.start(private_mode=False, **kw)
            log.info("窗口事件循环结束")
        except Exception as e:
            log.warning("原生窗口不可用，改用浏览器: %s", e)
            st["win"] = None; native = False
    if not native:
        if not a.background: webbrowser.open(url)
        try:
            while not srv.should_exit: time.sleep(0.5)
        except KeyboardInterrupt: pass
    quit_all()
    if icon is not None:
        try: icon.stop()
        except Exception: pass


if __name__ == "__main__":
    run()
