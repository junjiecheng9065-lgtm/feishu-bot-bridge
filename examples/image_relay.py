#!/usr/bin/env python3
"""Box-side image relay for 飞书 Bot 桥.
Grok agents' SendToUser images live only on the box (file:///home/box/agent-data/agents/<agent>/{attachments,assets}/...).
The Windows bridge can't read them, so this mirrors each bot's new images into that bot app's own Feishu Drive
folder 'grok-bridge-images' (same file name). The bridge downloads by name and sends msg_type=image.
Only images modified at/after CUTOFF are mirrored (never older ones). Credentials come from lark-cli profiles (keychain);
nothing secret is printed."""
import glob, json, os, shutil, subprocess, sys, time
ROOT = os.path.dirname(os.path.abspath(__file__))
BOTS = os.path.join(ROOT, "bots"); STAGE = os.path.join(ROOT, "image-relay"); STATE = os.path.join(STAGE, "state.json")
LOG = os.path.join(ROOT, "logs", "image_relay.log")
FOLDER = "grok-bridge-images"
CUTOFF = time.mktime(time.strptime(os.environ.get("RELAY_CUTOFF", "2026-10-19 09:00"), "%Y-%m-%d %H:%M"))
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
MAX = 10 * 1024 * 1024

def log(m):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + m + "\n")

def env(path):
    d = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); d[k.replace("export ", "").strip()] = v.strip().strip('"\'')
    return d

def cli(profile, *args, cwd=None):
    r = subprocess.run(["lark-cli", "--profile", profile, *args], capture_output=True, text=True, cwd=cwd, timeout=300)
    try: return json.loads(r.stdout)
    except Exception: return {"ok": False, "error": (r.stderr or r.stdout)[-300:]}

def folder_token(profile, st):
    if st.get("folders", {}).get(profile): return st["folders"][profile]
    d = cli(profile, "drive", "+create-folder", "--as", "bot", "--name", FOLDER)
    tok = (d.get("data") or {}).get("token") or (d.get("data") or {}).get("folder_token")
    if not tok:
        err = d.get("error"); ms = err.get("missing_scopes") if isinstance(err, dict) else None
        log(f"[{profile}] create folder failed: missing_scopes={ms}" if ms else f"[{profile}] create folder failed: {str(err)[:150]}"); return None
    st.setdefault("folders", {})[profile] = tok; json.dump(st, open(STATE, "w")); log(f"[{profile}] folder ready"); return tok

def once(st):
    for envf in sorted(glob.glob(os.path.join(BOTS, "*.env"))):
        bid = os.path.basename(envf)[:-4]; e = env(envf); prof = e.get("LARK_PROFILE"); agent = e.get("AGENT_ID")
        if not prof or not agent: continue
        sd = os.path.join(STAGE, bid); os.makedirs(sd, exist_ok=True); new = 0
        for p in glob.glob(f"/home/box/agent-data/agents/{agent}/attachments/*") + glob.glob(f"/home/box/agent-data/agents/{agent}/assets/*"):
            n = os.path.basename(p)
            if not n.lower().endswith(EXTS) or os.path.getmtime(p) < CUTOFF: continue
            dst = os.path.join(sd, n)
            if os.path.exists(dst): continue
            if os.path.getsize(p) > MAX:
                try:
                    from PIL import Image
                    im = Image.open(p).convert("RGB"); q = 90
                    while True:
                        im.save(dst, "JPEG", quality=q)
                        if os.path.getsize(dst) <= MAX or q <= 40: break
                        q -= 10
                except Exception as ex: log(f"[{bid}] compress fail {n}: {ex}"); continue
            else: shutil.copyfile(p, dst)
            new += 1
        pushed = set(st.setdefault("pushed", {}).setdefault(bid, []))
        todo = [n for n in os.listdir(sd) if n not in pushed]
        if not todo: continue
        if time.time() - st.setdefault("fail_at", {}).get(bid, 0) < 300: continue
        tok = folder_token(prof, st)
        if not tok: continue
        d = cli(prof, "drive", "+push", "--as", "bot", "--folder-token", tok, "--local-dir", bid, "--if-exists", "skip", cwd=STAGE)
        if d.get("ok"): st["pushed"][bid] = sorted(pushed | set(todo)); st["fail_at"].pop(bid, None)
        else: st["fail_at"][bid] = time.time()
        json.dump(st, open(STATE, "w"))
        log(f"[{bid}] pushed {len(todo)} new: ok={d.get('ok')} {json.dumps((d.get('data') or {}).get('summary', ''), ensure_ascii=False)[:200]} {str(d.get('error') or '')[:200]}")

def main():
    os.makedirs(STAGE, exist_ok=True)
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    loop = "--loop" in sys.argv
    while True:
        try: once(st)
        except Exception as ex: log(f"error {type(ex).__name__}: {ex}")
        if not loop: break
        time.sleep(10)

if __name__ == "__main__": main()
