"""飞书 Bot 桥 core: config store, per-bot worker processes (lark-oapi websocket), supervisor."""
import json, os, re, sys, time, threading, uuid, collections, multiprocessing as mp, urllib.request

APP_NAME = "FeishuBotBridge"
VERSION = "0.2.20"
ACK_TEXT = "收到，处理中…"
DEFAULT_USER_NAME = "桔子"
FIELDS = ["name", "app_id", "app_secret", "webhook_url", "webhook_key", "owner_open_id",
          "bot_open_id", "group_all", "enabled", "agent_id", "sync_chat_id", "sync_enabled"]
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
        self.data = {"bots": {}, "settings": {"port": 8765, "autostart": False, "user_display_name": DEFAULT_USER_NAME}}
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

    def user_name(self):
        return str(self.data.get("settings", {}).get("user_display_name") or DEFAULT_USER_NAME).strip() or DEFAULT_USER_NAME

    def bots(self):
        return dict(self.data["bots"])

    def get(self, bid):
        return self.data["bots"].get(bid)

    def upsert(self, bid, fields):
        with self.lock:
            cur = self.data["bots"].get(bid) or {"id": bid, "name": "", "app_id": "", "app_secret": "",
                "webhook_url": "", "webhook_key": "", "owner_open_id": "", "bot_open_id": "",
                "group_all": False, "enabled": False,
                "agent_id": "", "sync_chat_id": "", "sync_enabled": False}
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


def send_text(cfg, target, text, client=None, msg_type="text", content=None):
    """target: om_xxx -> reply to message; oc_xxx -> send to chat. Returns (ok, msg)."""
    from lark_oapi.api.im.v1 import (ReplyMessageRequest, ReplyMessageRequestBody,
        CreateMessageRequest, CreateMessageRequestBody)
    client = client or lark_client(cfg["app_id"], cfg["app_secret"])
    if content is None: content = json.dumps({"text": text}, ensure_ascii=False)
    if target.startswith("om_"):
        req = ReplyMessageRequest.builder().message_id(target).request_body(
            ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).build()).build()
        resp = client.im.v1.message.reply(req)
    elif target.startswith("oc_"):
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder().receive_id(target).msg_type(msg_type).content(content).build()).build()
        resp = client.im.v1.message.create(req)
    else:
        return False, "target 必须以 om_ 或 oc_ 开头"
    if not resp.success():
        return False, f"code={resp.code} msg={resp.msg}"
    return True, getattr(resp.data, "message_id", "") or "ok"


def patch_card(cfg, message_id, content, client=None):
    """Update an interactive card in place (card must have update_multi). Returns (ok, msg)."""
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
    client = client or lark_client(cfg["app_id"], cfg["app_secret"])
    req = PatchMessageRequest.builder().message_id(message_id).request_body(
        PatchMessageRequestBody.builder().content(content).build()).build()
    resp = client.im.v1.message.patch(req)
    if not resp.success(): return False, f"code={resp.code} msg={resp.msg}"
    return True, message_id


def update_text(cfg, message_id, text, client=None):
    """Edit a text message in place (PUT im/v1/messages/:id). Returns (ok, msg)."""
    from lark_oapi.api.im.v1 import UpdateMessageRequest, UpdateMessageRequestBody
    client = client or lark_client(cfg["app_id"], cfg["app_secret"])
    req = UpdateMessageRequest.builder().message_id(message_id).request_body(
        UpdateMessageRequestBody.builder().msg_type("text").content(json.dumps({"text": text}, ensure_ascii=False)).build()).build()
    resp = client.im.v1.message.update(req)
    if not resp.success(): return False, f"code={resp.code} msg={resp.msg}"
    return True, message_id


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
            q.put({"bot": cfg["id"], "type": "group_ctx",
                   "value": {"chat_id": m.chat_id, "message_id": mid} if m.chat_type == "group" else None})
            ok, msg = send_text(cfg, mid, ACK_TEXT, client)
            log(f"已回复「{ACK_TEXT}」 code=0 mid={msg} reply_to={mid}" if ok else f"回复失败 {msg}", "info" if ok else "warn")

        def on_card(data):
            from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
            ev = data.event; v = (ev.action.value if ev and ev.action else None) or {}
            if not isinstance(v, dict) or "fbw" not in v: return P2CardActionTriggerResponse({})
            sid = ev.operator.open_id if ev.operator else ""
            if allowed and sid not in allowed:
                log("忽略非主人卡片点击"); return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": "无权限"}})
            ctx = ev.context
            p = {"chat_id": ctx.open_chat_id if ctx else "", "message_id": ctx.open_message_id if ctx else "",
                 "chat_type": "group" if (ctx and (ctx.open_chat_id or "").startswith("oc_")) else "p2p",
                 "sender_open_id": sid, "text": v.get("v") or v.get("l") or "", "card_action": True,
                 "widget_prompt": v.get("p", ""), "bot_slug": cfg.get("slug") or cfg["id"], "bot_name": cfg.get("name", "")}
            st, body = post_webhook(cfg["webhook_url"], cfg["webhook_key"], p, timeout=2.5)
            if not 200 <= st < 300:
                def retry():
                    for attempt in range(4):
                        time.sleep(min(60, 5 * 2 ** attempt))
                        s2, _ = post_webhook(cfg["webhook_url"], cfg["webhook_key"], p)
                        if 200 <= s2 < 300: log(f"卡片选择已转发(重试) {v.get('l')}"); return
                    log("卡片选择转发失败", "error")
                threading.Thread(target=retry, daemon=True).start()
            else: log(f"卡片选择已转发: {v.get('l')}")
            if p["chat_type"] == "group" and p["message_id"]:
                q.put({"bot": cfg["id"], "type": "group_ctx", "value": {"chat_id": p["chat_id"], "message_id": p["message_id"]}})
            w = {"prompt": v.get("p", ""), "options": [{"label": x} for x in (v.get("o") or [v.get("l")])]}
            try:
                # rebuild resolved card with all options from current card if possible: only chosen known here
                card = widget_card(w, v["fbw"], chosen=v.get("l"))
            except Exception: card = None
            r = {"toast": {"type": "success", "content": f"已选择：{v.get('l')}"}}
            if card: r["card"] = {"type": "raw", "data": card}
            return P2CardActionTriggerResponse(r)

        def on_msg(data):
            threading.Thread(target=lambda: _safe(handle, data, log), daemon=True).start()

        eh = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_msg) \
            .register_p2_card_action_trigger(on_card).build()
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
        self.sync = ChatSync(store, self.log)

    def log(self, bot, msg, level="info"):
        self._add({"bot": bot, "ts": time.time(), "level": level, "msg": msg})

    def _add(self, e):
        self.logs.append(e)
        m = e.get("msg", "")
        if m.startswith("已转发"): self.status.setdefault(e["bot"], {"restarts": 0})["last_msg_at"] = e["ts"]
        elif m.startswith("已同步"): self.status.setdefault(e["bot"], {"restarts": 0})["last_sync_at"] = e["ts"]
        elif m.startswith("聊天同步异常"): self.status.setdefault(e["bot"], {"restarts": 0})["sync_error_at"] = e["ts"]
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
            elif e.get("type") == "group_ctx":
                self.sync.set_group(e["bot"], e.get("value"))
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


# ---------------- Grok Bot chat -> Feishu sync (local transcript replicas, same machine only) ----------------
def grok_store_dir():
    if os.environ.get("GROK_BOT_PERSISTENCE_DIR"): return os.environ["GROK_BOT_PERSISTENCE_DIR"]
    if sys.platform == "win32": base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin": base = os.path.expanduser("~/Library/Application Support")
    else: base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "Grok Bot", "sand-client-persistence")


def _b32name(n):
    import base64
    try: return base64.b32decode(n.upper() + "=" * (-len(n) % 8)).decode("utf-8", "replace")
    except Exception: return ""


def find_transcript(agent_id):
    """Newest local replica file for this agent (across signed-in accounts) or None."""
    d = grok_store_dir(); best = None
    try: names = os.listdir(d)
    except OSError: return None
    for fn in names:
        if not fn.endswith(".blob"): continue
        if not _b32name(fn[:-5]).endswith(".transcript.replicas." + agent_id): continue
        p = os.path.join(d, fn); m = os.path.getmtime(p)
        if not best or m > best[0]: best = (m, p)
    return best and best[1]


_HIDDEN_MARKS = ("GROK_BOT_HIDDEN_PROMPT", "<system-reminder", "<background-task", "<task-notification",
                 "<task_notification", "<automation", "[agent]", "[Agent]")
_NONUSER_FLAGS = ("fromAgent", "toAgent", "channel", "channelSender", "author", "hidden", "isHidden", "system",
                  "isSystem", "automationId", "automation", "backgroundTask", "taskId", "source", "origin")
_BAD_KINDS = ("agent", "system", "automation", "background", "task", "routine", "voice", "hidden", "nudge")


def _is_human_user(e):
    """True only for a message haohao typed himself in the Grok UI."""
    for k in _NONUSER_FLAGS:
        if e.get(k): return False
    meta = e.get("metadata") or e.get("meta") or {}
    if isinstance(meta, dict):
        for k in ("kind", "type", "source", "origin", "sender"):
            v = str(meta.get(k) or "").lower()
            if any(x in v for x in _BAD_KINDS): return False
        if meta.get("hidden") or meta.get("fromAgent"): return False
    nonce = str(e.get("clientNonce") or "")
    if ":" in nonce: return False  # e.g. voice-nudge:call-... ; typed messages carry a bare uuid/hex nonce
    c = (e.get("content") or "").lstrip()
    if any(m in c for m in _HIDDEN_MARKS[:1]) or any(c.startswith(m) for m in _HIDDEN_MARKS[1:]): return False
    return True


def _agent_name(v):
    if isinstance(v, dict):
        return str(v.get("name") or v.get("title") or v.get("label") or v.get("displayName") or "").strip()
    return ""


def _visible(e):
    """Skip only content not visible in the Grok chat (hidden prompts, reminders, task notices, voice nudges)."""
    if e.get("hidden") or e.get("isHidden"): return False
    meta = e.get("metadata") or e.get("meta") or {}
    if isinstance(meta, dict):
        if meta.get("hidden"): return False
        for k in ("kind", "type", "source", "origin"):
            v = str(meta.get(k) or "").lower()
            if any(x in v for x in ("system", "background", "voice", "hidden", "nudge")): return False
    if ":" in str(e.get("clientNonce") or ""): return False
    c = (e.get("content") or "").lstrip()
    if any(m in c for m in _HIDDEN_MARKS[:1]) or any(c.startswith(m) for m in _HIDDEN_MARKS[1:5]): return False
    return True


_FROM_IDS = {}
TALLY_AGENT_ID = "fb90b446-93f7-421a-b85a-f613963d1b47"


_IMAGES = {}  # transcript path -> {eid: (seq, [image urls])} from send-message entries


def _image_urls(m):
    urls = []
    for key in ("images", "attachments"):
        for a in m.get(key) or []:
            u = a.get("url") or a.get("uri") or a.get("path") if isinstance(a, dict) else a
            if not isinstance(u, str): continue
            mt = (a.get("mimeType") or a.get("mime_type") or a.get("type") or "") if isinstance(a, dict) else ""
            if key == "images" or "image" in str(mt) or re.search(r"\.(png|jpe?g|gif|webp|bmp)(\?|$)", u, re.I): urls.append(u)
    if m.get("type") in ("image", "attachment") and isinstance(m.get("url"), str): urls.append(m["url"])
    return urls


def resolve_image(url):
    """-> (bytes|None, how). https download, data: URI, local file, or basename lookup in local caches."""
    import base64, urllib.parse
    try:
        if url.startswith(("http://", "https://")):
            with urllib.request.urlopen(url, timeout=30) as r: return r.read(), "https"
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1]), "data-uri"
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path if url.startswith("file:") else url)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path): path = path[1:]
        if os.path.isfile(path):
            with open(path, "rb") as f: return f.read(), "local"
        name = os.path.basename(path)
        cands = [os.path.join(data_dir(), "images", name)]
        gb = os.path.join(os.environ.get("APPDATA", ""), "Grok Bot")
        if os.path.isdir(gb):
            for root, _d, files in os.walk(gb):
                if name in files: cands.append(os.path.join(root, name))
        for c in cands:
            if os.path.isfile(c):
                with open(c, "rb") as f: return f.read(), "cache"
        return None, f"only box path, not fetchable here ({name})"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


def send_image(cfg, target, data, client=None):
    """Upload bytes via im/v1/images (message) and send msg_type image. Returns (ok, msg)."""
    import io
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
    client = client or lark_client(cfg["app_id"], cfg["app_secret"])
    req = CreateImageRequest.builder().request_body(
        CreateImageRequestBody.builder().image_type("message").image(io.BytesIO(data)).build()).build()
    resp = client.im.v1.image.create(req)
    if not resp.success(): return False, f"upload code={resp.code} msg={resp.msg}"
    return send_text(cfg, target, "", client, "image", json.dumps({"image_key": resp.data.image_key}))


def read_transcript(path):
    """-> [(seq, id, role, body, title)] ordered by seq; 1:1 mirror of the visible chat.
    roles: user (haohao typed), agent_in (fromAgent), agent_out (assistant message with toAgent), assistant (SendToUser text)."""
    with open(path, encoding="utf-8") as f: j = json.load(f)
    out = []; imgs_map = _IMAGES[path] = {}
    for e in (j.get("value") or {}).get("entries") or []:
        k, seq, eid = e.get("kind"), e.get("seq"), e.get("id")
        if seq is None or not eid or e.get("isStreaming"): continue
        if k == "message" and e.get("role") == "user":
            if not _visible(e): continue
            if e.get("fromAgent"):
                fa = e["fromAgent"]
                if isinstance(fa, dict): _FROM_IDS[eid] = str(fa.get("id") or fa.get("agentId") or fa.get("agent_id") or "")
                out.append((seq, eid, "agent_in", e.get("content") or "", _agent_name(fa) or "Agent"))
            elif _is_human_user(e): out.append((seq, eid, "user", e.get("content") or "", "user"))
        elif k == "message" and e.get("role") == "assistant" and e.get("toAgent"):
            n = _agent_name(e["toAgent"])
            out.append((seq, eid, "agent_out", e.get("content") or "", ("→ " + n) if n else ""))
        elif k == "send-message":
            m = e.get("message") or {}
            imgs = _image_urls(m)
            if imgs and not e.get("toAgent") and not m.get("toAgent"): imgs_map[eid] = (seq, imgs)
            if m.get("type") == "text" and not e.get("toAgent") and not m.get("toAgent") and not m.get("widget"):
                out.append((seq, eid, "assistant", m.get("content") or "", ""))
            elif m.get("type") == "widget" or (m.get("widget") and not e.get("toAgent")):
                w = m.get("widget") if isinstance(m.get("widget"), dict) else m
                if w.get("options"): out.append((seq, eid, "widget", json.dumps(w, ensure_ascii=False), ""))
    out.sort(key=lambda r: r[0]); return out


def _card(title, template, md):
    return json.dumps({"config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}}]}, ensure_ascii=False)


def _md_card(md):
    """Headerless card whose markdown element renders links, bold, lists, code."""
    return json.dumps({"config": {"wide_screen_mode": True, "update_multi": True},
        "elements": [{"tag": "markdown", "content": md}]}, ensure_ascii=False)


def _md_card_t(title, md):
    return json.dumps({"config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "grey"},
        "elements": [{"tag": "markdown", "content": md}]}, ensure_ascii=False)


def _widget_options(w):
    res = []
    for i, o in enumerate(w.get("options") or []):
        if isinstance(o, dict):
            lab = str(o.get("label") or o.get("value") or ""); val = str(o.get("value") or lab)
        else: lab = val = str(o)
        if lab: res.append((chr(65 + i) if i < 26 else str(i + 1), lab, val))
    return res


def _widget_text(body):
    try:
        w = json.loads(body)
        return str(w.get("prompt") or w.get("question") or "请选择") + "\n" + "\n".join(f"{k}. {l}" for k, l, _ in _widget_options(w))
    except Exception: return ""


def widget_card(w, wid, chosen=None):
    """Feishu card for a Grok choice widget. chosen=label -> resolved (no buttons)."""
    prompt = str(w.get("prompt") or w.get("question") or w.get("title") or "请选择")
    opts = _widget_options(w); els = []
    if chosen is None:
        btns = [{"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": f"{k}. {lab}"[:100]},
                 "value": {"fbw": wid, "v": val, "l": lab, "p": prompt[:200], "o": [x[1][:60] for x in opts][:20]}} for k, lab, val in opts]
        for i in range(0, len(btns), 4): els.append({"tag": "action", "actions": btns[i:i + 4]})
        tip = "点按钮选择" + ("（多选：可直接在群里回复多个选项）" if w.get("multiSelect") else "")
        if w.get("allowCustom", True): tip += "；也可直接回复自定义答案"
        els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": tip}]})
        tpl = "blue"
    else:
        els.append({"tag": "markdown", "content": "\n".join(("✅ **" + lab + "**") if lab == chosen else ("~~" + lab + "~~")
                                                          for _, lab, _ in opts) or chosen})
        els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"已选择：{chosen}"}]})
        tpl = "green"
    return {"config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": prompt[:100]}, "template": tpl}, "elements": els}


class ChatSync:
    POLL = 3
    GROUP_TTL = 30 * 60

    def __init__(self, store, log):
        self.store, self.log, self.mtimes, self.clients = store, log, {}, {}
        self.gmu = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    # pending group context: group message woke the bot -> its next replies also go to the group
    def _group_path(self, bid): return os.path.join(data_dir(), f"groupctx_{bid}.json")

    def set_group(self, bid, v):
        with self.gmu:
            p = self._group_path(bid)
            if v:
                self._save(p, {"chat_id": v["chat_id"], "message_id": v.get("message_id") or "", "ts": time.time(),
                               "prefix": v.get("prefix") or "", "dispatched": bool(v.get("dispatched"))})
                self.log(bid, f"群上下文已记录 {v.get('message_id') or v['chat_id']}")
            elif os.path.exists(p):
                os.remove(p); self.log(bid, "群上下文已清除")

    def get_group(self, bid):
        with self.gmu:
            p = self._group_path(bid)
            try:
                with open(p, encoding="utf-8") as f: g = json.load(f)
            except (OSError, ValueError): return None
            if time.time() - g.get("ts", 0) > self.GROUP_TTL:
                try: os.remove(p)
                except OSError: pass
                return None
            return g

    def _state_path(self, bid): return os.path.join(data_dir(), f"chatsync_{bid}.json")

    def _loop(self):
        while True:
            for bid, b in self.store.bots().items():
                if not (b.get("sync_enabled") and b.get("agent_id") and b.get("sync_chat_id")
                        and b.get("app_id") and b.get("app_secret")): continue
                try: self._tick(bid, b)
                except Exception as e: self.log(bid, f"聊天同步异常: {type(e).__name__}: {str(e)[:150]}", "warn")
            time.sleep(self.POLL)

    def _tick(self, bid, b):
        path = find_transcript(b["agent_id"])
        if not path: return
        m = os.path.getmtime(path); key = (bid, b["agent_id"])
        sp = self._state_path(bid)
        if self.mtimes.get(key) == m and os.path.exists(sp): return
        rows = read_transcript(path)
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f: st = json.load(f)
        else: st = {}
        if st.get("agent_id") != b["agent_id"]:  # first run (or agent changed): start at latest, no backlog
            st = {"agent_id": b["agent_id"], "seq": rows[-1][0] if rows else 0, "sent": []}
            self._save(sp, st); self.log(bid, f"聊天同步开始，起点 seq={st['seq']}")
        client = self.clients.get(bid)
        if not client or client[0] != (b["app_id"], b["app_secret"]):
            client = ((b["app_id"], b["app_secret"]), lark_client(b["app_id"], b["app_secret"])); self.clients[bid] = client
        for seq, eid, role, body, title in rows:
            if seq <= st["seq"]: continue
            t = body.strip()
            # Doorway handoff often is "飞书：原文\n回复". Skip echo-only; sync the reply after the first line.
            if t.startswith("飞书"):
                rest = t.split("\n", 1)
                if len(rest) < 2 or not rest[1].strip():
                    st["seq"] = seq; self._save(sp, st); continue
                t = rest[1].strip()
            if eid not in st["sent"] and t:
                # Route: p2p stays p2p; group-triggered bot replies go ONLY to that group (not also p2p).
                # haohao Grok→飞书 user cards always stay on sync_chat_id (p2p).
                if role == "user" and not body.strip().startswith("飞书"):
                    self.set_group(bid, None)
                dispatched = False
                if role == "agent_in" and b.get("agent_id") != TALLY_AGENT_ID and (
                        _FROM_IDS.get(eid) == TALLY_AGENT_ID or (not _FROM_IDS.get(eid) and title.strip().lower() == "tally")):
                    tb = next((x for x in self.store.bots().values() if x.get("agent_id") == TALLY_AGENT_ID), None)
                    tg = self.get_group(tb["id"]) if tb else None
                    if tg and tg.get("chat_id"):
                        nm = b.get("name") or bid
                        self.set_group(bid, {"chat_id": tg["chat_id"], "prefix": nm, "dispatched": True})
                        if not self._task(tg["chat_id"], nm, "处理中", task=self._first_line(t)):
                            aok, amsg = send_text(b, tg["chat_id"], ACK_TEXT, client[1])  # fallback ack
                            self.log(bid, f"Tally 派单，已在群里确认" if aok else f"群确认失败 {amsg}", "info" if aok else "warn")
                        dispatched = True
                if role in ("agent_in", "agent_out"):
                    ag = self.get_group(bid)
                    if ag and ag.get("chat_id") and ag["chat_id"] != b.get("sync_chat_id"):
                        # group ctx: only key-node cards (已派 / 完成·卡住); else Grok-only (no group, no p2p)
                        self._key_agent(bid, b, role, title, t, client[1])
                        st["sent"] = (st["sent"] + [eid])[-500:]; st["seq"] = seq; self._save(sp, st); continue
                    # no group ctx: fall through → p2p 1:1
                g = self.get_group(bid) if role in ("assistant", "widget") else None
                use_group = bool(g and g.get("chat_id") and g.get("chat_id") != b.get("sync_chat_id"))
                if role == "user":
                    dest = b["sync_chat_id"]
                elif use_group:
                    dest = g.get("message_id") or g["chat_id"]
                else:
                    dest = b["sync_chat_id"]
                _last_content = [None, "interactive"]
                def _st(bb, d, tx, cl, mt, ct, _lc=_last_content):
                    _lc[0] = ct; _lc[1] = mt; return send_text(bb, d, tx, cl, mt, ct)
                _ack = t.strip().rstrip("。.").replace("...", "…") in (ACK_TEXT, "收到，处理中", "收到,处理中…")
                if _ack and role not in ("user", "widget"):  # ack = plain text, never a card
                    ok, msg = _st(b, dest, "", client[1], "text", json.dumps({"text": ACK_TEXT}, ensure_ascii=False))
                elif role == "user":
                    ok, msg = _st(b, dest, "", client[1], "interactive", _card(self.store.user_name(), "wathet", t[:9000]))
                elif role == "widget":
                    ok, msg = _st(b, dest, "", client[1], "interactive",
                                        json.dumps(widget_card(json.loads(body), eid), ensure_ascii=False))
                elif role == "agent_in":
                    ok, msg = _st(b, dest, "", client[1], "interactive", _card(title, "purple", t[:9000]))
                elif use_group and g.get("prefix") and role == "assistant":
                    ok, msg = _st(b, dest, "", client[1], "interactive", _md_card_t(g["prefix"], t[:9000]))
                    if ok:
                        g["prefix"] = ""; self.set_group(bid, g)
                elif title:
                    ok, msg = _st(b, dest, "", client[1], "interactive", _md_card_t(title, t[:9000]))
                else:
                    ok, msg = _st(b, dest, "", client[1], "interactive", _md_card(t[:9000]))
                if not ok and use_group and role != "user" and dest.startswith("om_"):
                    self.log(bid, f"群回复失败 {msg}，改为直接发到群", "warn")
                    ok, msg = send_text(b, g["chat_id"], "", client[1], _last_content[1], _last_content[0])
                if not ok: raise RuntimeError(f"发送失败 {msg}")
                if use_group and role != "user":
                    self._to_tally(bid, b, g["chat_id"], msg if str(msg).startswith("om_") else "",
                                   t if role != "widget" else _widget_text(body))
                if use_group and role == "assistant" and g.get("dispatched") and b.get("agent_id") != TALLY_AGENT_ID:
                    self._task(g["chat_id"], b.get("name") or bid, "完成", result=self._result_line(t))
                where = "群" if use_group and role != "user" else "单聊"
                self.log(bid, f"已同步到{where} {role} seq={seq}")
                st["sent"] = (st["sent"] + [eid])[-500:]
            st["seq"] = seq; self._save(sp, st)
        self._images(bid, b, rows, st, sp, client, path)
        self.mtimes[key] = m

    def _images(self, bid, b, rows, st, sp, client, path):
        """Send images of SendToUser messages after their text, to the same destination. Deduped via img_sent."""
        if "img_floor" not in st:
            st["img_floor"] = (rows[-1][0] - 30) if rows else 0; st["img_sent"] = []  # small backfill on first run
        for eid, (seq, urls) in sorted(_IMAGES.get(path, {}).items(), key=lambda x: x[1][0]):
            if seq <= st["img_floor"] or eid in st["img_sent"]: continue
            if seq > st.get("seq", 0): continue  # wait until text is processed
            g = self.get_group(bid)
            use_group = bool(g and g.get("chat_id") and g.get("chat_id") != b.get("sync_chat_id"))
            dest = (g.get("message_id") or g["chat_id"]) if use_group else b["sync_chat_id"]
            for i, u in enumerate(urls):
                data, how = resolve_image(u)
                if data is None:
                    self.log(bid, f"图片未同步 seq={seq} #{i + 1}: {how}", "warn"); continue
                ok, msg = send_image(b, dest, data, client[1])
                if not ok and use_group and dest.startswith("om_"): ok, msg = send_image(b, g["chat_id"], data, client[1])
                where = "群" if use_group else "单聊"
                self.log(bid, f"图片已同步到{where} seq={seq} #{i + 1} ({how}) code=0 mid={msg}" if ok else f"图片发送失败 seq={seq} #{i + 1} {msg}",
                         "info" if ok else "warn")
            st["img_sent"] = (st["img_sent"] + [eid])[-500:]; self._save(sp, st)

    def _to_tally(self, bid, b, chat_id, message_id, text):
        """Bridge posted a non-Tally bot's message into Tally's GROUP_ALL group -> also POST it to Tally's webhook
        (Feishu doesn't deliver other bots' messages). Never Tally->Tally; acks are never passed here; deduped."""
        if b.get("agent_id") == TALLY_AGENT_ID or not chat_id.startswith("oc_") or not text.strip(): return
        tb = next((x for x in self.store.bots().values() if x.get("agent_id") == TALLY_AGENT_ID), None)
        if not (tb and tb.get("group_all") and tb.get("webhook_url") and tb.get("webhook_key")): return
        if tb["id"] == bid: return
        import hashlib
        h = message_id or hashlib.sha1((chat_id + "\n" + text[:4000]).encode("utf-8")).hexdigest()
        dp = os.path.join(data_dir(), "tallyfwd.json"); now = time.time()
        with self.gmu:
            try:
                with open(dp, encoding="utf-8") as f: seen = json.load(f)
            except (OSError, ValueError): seen = {}
            seen = {k: v for k, v in seen.items() if now - v < 3600}
            if h in seen: return
            seen[h] = now; self._save(dp, seen)
        p = {"from_bot": True, "bot_name": b.get("name", ""), "bot_slug": b.get("slug") or bid, "text": text[:8000],
             "chat_id": chat_id, "message_id": message_id, "chat_type": "group", "group_all": True}
        def go():
            for attempt in range(4):
                st, _ = post_webhook(tb["webhook_url"], tb["webhook_key"], p)
                if 200 <= st < 300: self.log(tb["id"], f"已转发 bot 群消息 来自 {p['bot_name']}"); return
                time.sleep(min(60, 5 * 2 ** attempt))
            self.log(tb["id"], f"bot 群消息转发失败 来自 {p['bot_name']}", "warn")
        threading.Thread(target=go, daemon=True).start()

    def _first_line(self, t, n=60):
        line = (t or "").split("\n", 1)[0].strip().replace("\r", "")
        return (line[:n] + "…") if len(line) > n else line

    def _dedupe(self, key, file="groupmirror.json", ttl=3600):
        import hashlib
        h = key if len(key) == 40 and all(c in "0123456789abcdef" for c in key) else hashlib.sha1(key.encode("utf-8")).hexdigest()
        dp = os.path.join(data_dir(), file); now = time.time()
        with self.gmu:
            try:
                with open(dp, encoding="utf-8") as f: seen = json.load(f)
            except (OSError, ValueError): seen = {}
            seen = {k: v for k, v in seen.items() if now - v < ttl}
            if h in seen: return True
            seen[h] = now; self._save(dp, seen); return False

    def _key_agent(self, bid, b, role, title, t, client):
        """With group ctx: bot<->bot only updates the per-dispatch task card. Else stays in Grok."""
        g = self.get_group(bid)
        if not (g and g.get("chat_id") and g["chat_id"] != b.get("sync_chat_id")): return
        me = b.get("name") or bid
        other = title.lstrip("→ ").strip() if role == "agent_out" else title.strip()
        if role == "agent_out" and b.get("agent_id") == TALLY_AGENT_ID and other:  # Tally → bot: 已派
            self._task(g["chat_id"], other, "已派", task=self._first_line(t))
            self._target_ack(g["chat_id"], other); return
        # bot's reply back to Tally: bot-side agent_out, or Tally-side agent_in (same event; card dedupes)
        if role == "agent_out" and other.lower() == "tally" and b.get("agent_id") != TALLY_AGENT_ID: target = me
        elif role == "agent_in" and b.get("agent_id") == TALLY_AGENT_ID and other: target = other
        else: return
        low = t.lower()
        if any(m in t for m in ("卡住", "失败")) or any(m in low for m in ("blocked", "fail")):
            self._task(g["chat_id"], target, "卡住", result=self._first_line(t, 120), only_existing=True)
        else:
            self._task(g["chat_id"], target, "完成", result=self._result_line(t), only_existing=True)
        self._queue_result(g["chat_id"], target, t)

    _TRIVIAL = ("收到", "好的", "ok", "处理中", "马上", "稍等", "在做", "开始")

    def _queue_result(self, chat_id, bot_name, text):
        """Debounce (20s) the dispatched bot's reply to Tally; post the longest substantive one to the group once."""
        t = (text or "").strip()
        if len(t) < 30 and any(w in t.lower() for w in self._TRIVIAL): return
        if len(t) < 8: return
        if not hasattr(self, "_rq"): self._rq = {}; self._rqmu = threading.Lock()
        k = chat_id + "|" + bot_name
        with self._rqmu:
            ent = self._rq.get(k)
            if ent and ent.get("timer"): ent["timer"].cancel()
            if not ent or len(t) >= len(ent["text"]): ent = {"text": t}
            ent["timer"] = threading.Timer(20, self._post_result, (chat_id, bot_name, k))
            ent["timer"].daemon = True; self._rq[k] = ent; ent["timer"].start()

    def _post_result(self, chat_id, bot_name, k):
        with self._rqmu: ent = self._rq.pop(k, None)
        if not ent: return
        t = ent["text"]
        tgt = next((x for x in self.store.bots().values() if (x.get("name") or x.get("id")) == bot_name), None)
        if not (tgt and tgt.get("app_id") and tgt.get("app_secret")): return
        try:
            with open(os.path.join(data_dir(), "tasks.json"), encoding="utf-8") as f:
                mid = (json.load(f).get(k) or {}).get("mid", "")
        except (OSError, ValueError): mid = ""
        if self._dedupe("res|" + (mid or k) + "|" + t[:200], "resultseen.json", 86400): return
        parts, rest = [], t
        while rest:
            cut = rest[:3800]
            if len(rest) > 3800 and "\n" in cut[1000:]: cut = cut[:cut.rfind("\n")]
            parts.append(cut); rest = rest[len(cut):].lstrip("\n")
        for i, part in enumerate(parts):
            if len(parts) > 1: part = f"({i + 1}/{len(parts)})\n" + part
            try: ok, msg = send_text(tgt, mid or chat_id, part)
            except Exception as e: ok, msg = False, repr(e)[:120]
            if not ok and mid: ok, msg = send_text(tgt, chat_id, part)
            self.log(tgt["id"], f"派单结果已发群 {i + 1}/{len(parts)} code=0 mid={msg}" if ok else f"派单结果发群失败 {msg}",
                     "info" if ok else "warn")

    def _target_ack(self, chat_id, bot_name):
        """Dispatched bot itself replies 「收到，处理中…」 (plain text) to Tally's dispatch notice in the group."""
        tgt = next((x for x in self.store.bots().values() if (x.get("name") or x.get("id")) == bot_name), None)
        if not (tgt and tgt.get("app_id") and tgt.get("app_secret")):
            self.log("tally", f"派单回执跳过：找不到 bot {bot_name}", "warn"); return
        try:
            with open(os.path.join(data_dir(), "tasks.json"), encoding="utf-8") as f:
                mid = (json.load(f).get(chat_id + "|" + bot_name) or {}).get("mid", "")
        except (OSError, ValueError): mid = ""
        if self._dedupe("ack|" + (mid or chat_id + bot_name + str(int(time.time() // 60))), "ackseen.json", 86400): return
        try: ok, msg = send_text(tgt, mid or chat_id, ACK_TEXT)
        except Exception as e: ok, msg = False, repr(e)[:120]
        if not ok and mid: ok, msg = send_text(tgt, chat_id, ACK_TEXT)
        self.log(tgt["id"], f"派单回执「{ACK_TEXT}」 code=0 mid={msg} reply_to={mid or '-'}" if ok else f"派单回执失败 {msg}",
                 "info" if ok else "warn")

    # ---- task card (one per dispatch, posted as Tally, patched in place) ----
    _RANK = {"已派": 0, "处理中": 1, "卡住": 2, "完成": 3}

    def _tally(self):
        tb = next((x for x in self.store.bots().values() if x.get("agent_id") == TALLY_AGENT_ID), None)
        if not (tb and tb.get("app_id") and tb.get("app_secret")): return None, None
        c = self.clients.get(tb["id"])
        if not c or c[0] != (tb["app_id"], tb["app_secret"]):
            c = ((tb["app_id"], tb["app_secret"]), lark_client(tb["app_id"], tb["app_secret"])); self.clients[tb["id"]] = c
        return tb, c[1]

    def _result_line(self, t):
        m = re.search(r"https?://\S+", t or "")
        return m.group(0)[:200] if m else self._first_line(t, 120)

    @staticmethod
    def _task_text(rec):
        task = " ".join((rec.get("task") or "…").split())
        if len(task) > 60: task = task[:60] + "…"
        tag = {"完成": "✅完成", "卡住": "⚠️卡住"}.get(rec["status"], "处理中")
        out = f"@{rec['bot']} {task}  · {tag}"
        return out

    def _task(self, chat_id, bot_name, status, task="", result="", only_existing=False):
        """Create/update the task card for (group, bot). Returns True if a card is in the group."""
        tb, tc = self._tally()
        if not tb: return False
        dp = os.path.join(data_dir(), "tasks.json"); now = time.time(); k = chat_id + "|" + bot_name
        with self.gmu:
            try:
                with open(dp, encoding="utf-8") as f: tasks = json.load(f)
            except (OSError, ValueError): tasks = {}
            tasks = {a: v for a, v in tasks.items() if now - v.get("ts", 0) < 86400}
            rec = tasks.get(k)
            if rec and (now - rec.get("ts", 0) > self.GROUP_TTL or status == "已派"):
                rec = None  # stale or a new dispatch after completion -> new card
            new = rec is None
            if new and only_existing: return False  # replies never create a card on their own
            if new: rec = {"bot": bot_name, "task": task, "status": status, "ts": now, "result": result, "mid": ""}
            else:
                if self._RANK.get(status, 0) < self._RANK.get(rec["status"], 0) and not (rec["status"] == "卡住" and status == "处理中"):
                    if not result or result == rec.get("result"): return True  # no downgrade, nothing new
                    status = rec["status"]
                if (status, task or rec.get("task"), result or rec.get("result")) == (rec["status"], rec.get("task"), rec.get("result")):
                    return True  # dedupe
                rec["status"] = status; rec["task"] = rec.get("task") or task
                if result: rec["result"] = result
                rec["ts"] = now
            tasks[k] = rec; self._save(dp, tasks)
        text = self._task_text(rec); ok = False
        if rec.get("mid"):
            if rec.get("shown") == text: return True
            ok, msg = update_text(tb, rec["mid"], text, tc)
            if not ok: self.log(tb["id"], f"任务消息编辑失败 {msg}，改发新消息", "warn")
        if not ok:
            ok, msg = send_text(tb, chat_id, text, tc)
            if ok and str(msg).startswith("om_"):
                with self.gmu:
                    try:
                        with open(dp, encoding="utf-8") as f: tasks = json.load(f)
                    except (OSError, ValueError): tasks = {}
                    if k in tasks: tasks[k]["mid"] = msg; tasks[k]["shown"] = text; self._save(dp, tasks)
        elif ok:
            with self.gmu:
                try:
                    with open(dp, encoding="utf-8") as f: tasks = json.load(f)
                except (OSError, ValueError): tasks = {}
                if k in tasks: tasks[k]["shown"] = text; self._save(dp, tasks)
        self.log(tb["id"], f"任务消息 {bot_name}：{rec['status']} code=0 mid={msg}" if ok else f"任务消息发送失败 {msg}", "info" if ok else "warn")
        return ok

    def _save(self, p, st):
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, p)


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


def apply_registry_fields(r):
    """bots_registry.json entry -> sync fields (sync_enabled untouched)."""
    if not isinstance(r, dict): return {}
    d = {}
    if r.get("agent_id"): d["agent_id"] = r["agent_id"]
    cid = r.get("chat_id") or r.get("FEISHU_P2P_CHAT_ID") or r.get("p2p_chat_id")
    if cid: d["sync_chat_id"] = cid
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
    reg = {}
    try: reg = json.load(open(os.path.join(base, "bots_registry.json"), encoding="utf-8"))
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
            "enabled": exists.get("enabled", False) if exists else False,
            **apply_registry_fields(reg.get(slug))})
        b["slug"] = slug; store.save()
        out.append({"id": slug, "name": name, "app_id": bool(app_id), "webhook": bool(url and key),
                    "app_secret": bool(b.get("app_secret"))})
    return out
