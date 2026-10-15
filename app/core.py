"""飞书 Bot 桥 core: config store, per-bot worker processes (lark-oapi websocket), supervisor."""
import json, os, re, sys, time, threading, uuid, collections, multiprocessing as mp, urllib.request

APP_NAME = "FeishuBotBridge"
ACK_TEXT = "收到，处理中…"
FIELDS = ["name", "app_id", "app_secret", "webhook_url", "webhook_key", "owner_open_id",
          "bot_open_id", "group_all", "enabled"]
SECRET_FIELDS = {"app_secret", "webhook_key"}


def data_dir():
    if os.environ.get("FEISHU_BRIDGE_HOME"):
        d = os.environ["FEISHU_BRIDGE_HOME"]
    elif sys.platform == "win32":
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    elif sys.platform == "darwin":
        d = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    else:
        d = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------- config store (JSON) ----------------
class Store:
    def __init__(self):
        self.path = os.path.join(data_dir(), "config.json")
        self.lock = threading.Lock()
        self.data = {"bots": {}, "settings": {"port": 8765, "autostart": False}}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        try: os.chmod(self.path, 0o600)
        except OSError: pass

    def bots(self):
        return dict(self.data["bots"])

    def get(self, bid):
        return self.data["bots"].get(bid)

    def upsert(self, bid, fields):
        with self.lock:
            cur = self.data["bots"].get(bid) or {"id": bid, "name": "", "app_id": "", "app_secret": "",
                "webhook_url": "", "webhook_key": "", "owner_open_id": "", "bot_open_id": "",
                "group_all": False, "enabled": False}
            for k in FIELDS:
                if k in fields and fields[k] is not None:
                    if k in SECRET_FIELDS and fields[k] == "": continue  # blank = keep existing secret
                    cur[k] = fields[k]
            self.data["bots"][bid] = cur
            self.save()
            return cur

    def delete(self, bid):
        with self.lock:
            r = self.data["bots"].pop(bid, None)
            self.save()
            return r


def public(bot):
    """Bot dict safe for UI: secrets masked."""
    b = dict(bot)
    for k in SECRET_FIELDS:
        b[k + "_set"] = bool(b.get(k)); b[k] = ""
    return b


# ---------------- webhook ----------------
def post_webhook(url, key, payload, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:150]}"


# ---------------- SDK helpers ----------------
def lark_client(app_id, app_secret):
    import lark_oapi as lark
    return lark.Client.builder().app_id(app_id).app_secret(app_secret).log_level(lark.LogLevel.ERROR).build()


def send_text(cfg, target, text, client=None):
    """target: om_xxx -> reply to message; oc_xxx -> send to chat. Returns (ok, msg)."""
    from lark_oapi.api.im.v1 import (ReplyMessageRequest, ReplyMessageRequestBody,
        CreateMessageRequest, CreateMessageRequestBody)
    client = client or lark_client(cfg["app_id"], cfg["app_secret"])
    content = json.dumps({"text": text}, ensure_ascii=False)
    if target.startswith("om_"):
        req = ReplyMessageRequest.builder().message_id(target).request_body(
            ReplyMessageRequestBody.builder().msg_type("text").content(content).build()).build()
        resp = client.im.v1.message.reply(req)
    elif target.startswith("oc_"):
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder().receive_id(target).msg_type("text").content(content).build()).build()
        resp = client.im.v1.message.create(req)
    else:
        return False, "target 必须以 om_ 或 oc_ 开头"
    if not resp.success():
        return False, f"code={resp.code} msg={resp.msg}"
    return True, getattr(resp.data, "message_id", "") or "ok"


def fetch_bot_open_id(cfg):
    import lark_oapi as lark
    client = lark_client(cfg["app_id"], cfg["app_secret"])
    req = lark.BaseRequest.builder().http_method(lark.HttpMethod.GET).uri("/open-apis/bot/v3/info") \
        .token_types({lark.AccessTokenType.TENANT}).build()
    resp = client.request(req)
    try:
        return json.loads(resp.raw.content).get("bot", {}).get("open_id", "")
    except Exception:
        return ""


# ---------------- worker (child process, one per bot) ----------------
def worker_main(cfg, other_bot_names, q, seen_path):
    def log(msg, level="info"):
        q.put({"bot": cfg["id"], "ts": time.time(), "level": level, "msg": msg})
    try:
        import asyncio
        import lark_oapi as lark
        import lark_oapi.ws.client as wsc
        wsc.loop = asyncio.new_event_loop(); asyncio.set_event_loop(wsc.loop)  # SDK uses a module loop
        client = lark_client(cfg["app_id"], cfg["app_secret"])
        bot_id = cfg.get("bot_open_id") or ""
        if not bot_id:
            bot_id = fetch_bot_open_id(cfg)
            log(f"获取机器人 open_id {'成功' if bot_id else '失败（群聊 @ 判断将仅按名称）'}")
            if bot_id: q.put({"bot": cfg["id"], "type": "bot_open_id", "value": bot_id})
        allowed = {x.strip() for x in (cfg.get("owner_open_id") or "").split(",") if x.strip()}
        seen = collections.OrderedDict()
        if os.path.exists(seen_path):
            for l in open(seen_path, encoding="utf-8").read().split()[-2000:]: seen[l] = 1
        lock = threading.Lock()

        def handle(data):
            ev = data.event; m = ev.message
            mid = m.message_id
            with lock:
                if mid in seen: return
                seen[mid] = 1
                with open(seen_path, "a", encoding="utf-8") as f: f.write(mid + "\n")
                while len(seen) > 5000: seen.popitem(last=False)
            sid = ev.sender.sender_id.open_id if ev.sender and ev.sender.sender_id else ""
            if allowed and sid not in allowed:
                log(f"忽略非主人消息 {mid}"); return
            mentions = m.mentions or []
            ids = {x.id.open_id for x in mentions if x.id}
            names = {x.name for x in mentions}
            group_all = False
            if m.chat_type == "group":
                at_me = (bot_id and bot_id in ids) or (not bot_id and cfg.get("name") in names)
                if not at_me:
                    if not cfg.get("group_all") or names & other_bot_names: return
                    group_all = True
            try:
                c = json.loads(m.content or "{}"); text = c.get("text") or m.content
            except ValueError:
                text = m.content
            for x in mentions:
                if isinstance(text, str) and x.key: text = text.replace(x.key, "@" + (x.name or ""))
            p = {"chat_id": m.chat_id, "message_id": mid, "chat_type": m.chat_type, "sender_open_id": sid,
                 "text": text, "bot_slug": cfg.get("slug") or cfg["id"], "bot_name": cfg.get("name", "")}
            if group_all: p["group_all"] = True
            for attempt in range(5):
                st, body = post_webhook(cfg["webhook_url"], cfg["webhook_key"], p)
                if 200 <= st < 300: break
                log(f"转发失败 {mid} 状态={st} {body[:80]}", "warn"); time.sleep(min(60, 5 * 2 ** attempt))
            else:
                log(f"放弃转发 {mid}", "error"); return
            log(f"已转发 {mid} ({m.chat_type})")
            ok, msg = send_text(cfg, mid, ACK_TEXT, client)
            log(f"已回复「{ACK_TEXT}」" if ok else f"回复失败 {msg}", "info" if ok else "warn")

        def on_msg(data):
            threading.Thread(target=lambda: _safe(handle, data, log), daemon=True).start()

        eh = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_msg).build()
        ws = lark.ws.Client(cfg["app_id"], cfg["app_secret"], event_handler=eh,
                            log_level=lark.LogLevel.INFO, auto_reconnect=True)
        log("websocket 连接中…")
        q.put({"bot": cfg["id"], "type": "state", "value": "running"})
        ws.start()  # blocks; SDK auto-reconnects
        log("websocket 已退出", "warn")
    except Exception as e:
        log(f"工作进程异常: {type(e).__name__}: {str(e)[:200]}", "error")


def _safe(fn, data, log):
    try: fn(data)
    except Exception as e: log(f"处理消息异常: {type(e).__name__}: {e}", "error")


# ---------------- supervisor ----------------
class Manager:
    def __init__(self, store):
        self.store = store
        self.ctx = mp.get_context("spawn")
        self.q = self.ctx.Queue()
        self.procs = {}       # id -> Process
        self.want = set()     # ids that should be running
        self.status = {}      # id -> dict
        self.logs = collections.deque(maxlen=2000)
        self.logfile = os.path.join(data_dir(), "bridge.log")
        self.mu = threading.Lock()
        threading.Thread(target=self._drain, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()

    def log(self, bot, msg, level="info"):
        self._add({"bot": bot, "ts": time.time(), "level": level, "msg": msg})

    def _add(self, e):
        self.logs.append(e)
        try:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%F %T', time.localtime(e['ts']))} [{e['bot']}] {e['level']} {e['msg']}\n")
        except OSError: pass

    def _drain(self):
        while True:
            try: e = self.q.get()
            except Exception: time.sleep(1); continue
            if e.get("type") == "bot_open_id":
                if self.store.get(e["bot"]): self.store.upsert(e["bot"], {"bot_open_id": e["value"]})
            elif e.get("type") == "state":
                self.status.setdefault(e["bot"], {})["connected_at"] = time.time()
            else:
                self._add(e)

    def start(self, bid):
        cfg = self.store.get(bid)
        if not cfg: raise KeyError(bid)
        missing = [k for k in ("app_id", "app_secret", "webhook_url", "webhook_key") if not cfg.get(k)]
        if missing: raise ValueError("缺少字段: " + ", ".join(missing))
        with self.mu:
            self.want.add(bid)
            self._spawn(bid)

    def _spawn(self, bid):
        p = self.procs.get(bid)
        if p and p.is_alive(): return
        cfg = dict(self.store.get(bid))
        others = {b.get("name", "") for k, b in self.store.bots().items() if k != bid}
        seen = os.path.join(data_dir(), f"seen_{bid}.txt")
        p = self.ctx.Process(target=worker_main, args=(cfg, others, self.q, seen), daemon=True)
        p.start(); self.procs[bid] = p
        st = self.status.setdefault(bid, {"restarts": 0})
        st["started_at"] = time.time(); st["pid"] = p.pid
        self.log(bid, f"启动工作进程 pid={p.pid}")

    def stop(self, bid):
        with self.mu:
            self.want.discard(bid)
            p = self.procs.pop(bid, None)
        if p and p.is_alive():
            p.terminate(); p.join(5)
            if p.is_alive(): p.kill()
            self.log(bid, "已停止")

    def restart(self, bid):
        self.stop(bid); self.start(bid)

    def _supervise(self):
        while True:
            time.sleep(5)
            with self.mu:
                for bid in list(self.want):
                    p = self.procs.get(bid)
                    if not self.store.get(bid): self.want.discard(bid); continue
                    if p is None or not p.is_alive():
                        st = self.status.setdefault(bid, {"restarts": 0})
                        up = time.time() - st.get("started_at", 0)
                        if up > 300: st["restarts"] = 0  # healthy for a while -> reset backoff
                        if up < min(300, 5 * 2 ** st.get("restarts", 0)): continue
                        st["restarts"] = st.get("restarts", 0) + 1
                        self.log(bid, f"工作进程已退出，自动重启（第 {st['restarts']} 次）", "warn")
                        try: self._spawn(bid)
                        except Exception as e: self.log(bid, f"重启失败 {e}", "error")

    def state(self, bid):
        p = self.procs.get(bid)
        if bid in self.want and p and p.is_alive(): return "running"
        if bid in self.want: return "restarting"
        return "stopped"

    def start_enabled(self):
        for bid, b in self.store.bots().items():
            if b.get("enabled"):
                try: self.start(bid)
                except Exception as e: self.log(bid, f"自动启动失败: {e}", "error")

    def shutdown(self):
        for bid in list(self.procs): self.stop(bid)


# ---------------- import from /workspace/feishu-bridge ----------------
def _env(path):
    d = {}
    if not os.path.exists(path): return d
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("export "): line = line[7:]
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def import_legacy(store, base, lark_cfg=os.path.expanduser("~/.lark-cli/config.json")):
    """Imports bots (enabled=False, dry run). Returns summary without secrets."""
    secrets = _env(os.path.join(base, "secrets.env"))
    hooks = {}
    try:
        for h in json.load(open(os.path.join(base, "state", "webhooks.json"), encoding="utf-8")).get("bots", []):
            hooks[h.get("bot")] = h
    except Exception: pass
    apps = {}
    try:
        for a in json.load(open(lark_cfg, encoding="utf-8")).get("apps", []):
            apps[a.get("name") or a.get("appId")] = a.get("appId", "")
    except Exception: pass
    out = []
    bdir = os.path.join(base, "bots")
    for fn in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        if not fn.endswith(".env"): continue
        slug = fn[:-4]; c = _env(os.path.join(bdir, fn))
        name = c.get("BOT_NAME", slug)
        url = secrets.get(c.get("URL_ENV", ""), "") or hooks.get(name, {}).get("webhook_url", "")
        key = secrets.get(c.get("KEY_ENV", ""), "") or hooks.get(name, {}).get("webhook_key", "")
        app_id = c.get("APP_ID") or apps.get(c.get("LARK_PROFILE", ""), "")
        exists = store.get(slug)
        b = store.upsert(slug, {"name": name, "app_id": app_id, "webhook_url": url, "webhook_key": key,
            "owner_open_id": c.get("ALLOWED_OPEN_IDS") or c.get("HAOHAO_OPEN_ID", ""),
            "bot_open_id": c.get("BOT_OPEN_ID", ""), "group_all": c.get("GROUP_ALL") == "1",
            "enabled": exists.get("enabled", False) if exists else False})
        b["slug"] = slug; store.save()
        out.append({"id": slug, "name": name, "app_id": bool(app_id), "webhook": bool(url and key),
                    "app_secret": bool(b.get("app_secret"))})
    return out
