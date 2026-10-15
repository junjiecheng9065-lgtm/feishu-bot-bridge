import os, sys
NAME = "FeishuBotBridge"

def _cmd():
    if getattr(sys, "frozen", False): return [sys.executable, "--background"]
    return [sys.executable, "-m", "app.main", "--background"]

def _linux_path(): return os.path.expanduser(f"~/.config/autostart/{NAME}.desktop")
def _mac_path(): return os.path.expanduser(f"~/Library/LaunchAgents/com.{NAME}.plist")

def is_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, NAME); return True
        except OSError: return False
    return os.path.exists(_mac_path() if sys.platform == "darwin" else _linux_path())

def set_enabled(on):
    cmd = _cmd()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
            if on: winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, " ".join(f'"{c}"' for c in cmd))
            else:
                try: winreg.DeleteValue(k, NAME)
                except OSError: pass
        return
    path = _mac_path() if sys.platform == "darwin" else _linux_path()
    if not on:
        if os.path.exists(path): os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if sys.platform == "darwin":
        args = "".join(f"<string>{c}</string>" for c in cmd)
        body = f'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>Label</key><string>com.{NAME}</string><key>ProgramArguments</key><array>{args}</array><key>RunAtLoad</key><true/></dict></plist>'
    else:
        body = f"[Desktop Entry]\nType=Application\nName=飞书 Bot 桥\nExec={' '.join(cmd)}\nX-GNOME-Autostart-enabled=true\n"
    open(path, "w", encoding="utf-8").write(body)
