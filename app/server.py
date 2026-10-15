import os, re, sys, time, uuid
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from . import core, autostart

UI_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "ui") \
    if not os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")) \
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


class BotIn(BaseModel):
    name: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    webhook_url: str | None = None
    webhook_key: str | None = None
    owner_open_id: str | None = None
    bot_open_id: str | None = None
    group_all: bool | None = None
    enabled: bool | None = None
    agent_id: str | None = None
    sync_chat_id: str | None = None
    sync_enabled: bool | None = None


class ReplyIn(BaseModel):
    bot: str
    target: str
    text: str


def create_app(store: core.Store, mgr: core.Manager, on_show=None):
    app = FastAPI(title="飞书 Bot 桥")
    token = store.data["settings"].get("api_token")
    if not token:
        token = uuid.uuid4().hex; store.data["settings"]["api_token"] = token; store.save()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # localhost only; /reply additionally requires the local API token
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"detail": "仅允许本机访问"}, 403)
        if request.url.path == "/reply" and request.headers.get("authorization") != f"Bearer {token}":
            return JSONResponse({"detail": "需要 Authorization: Bearer <本地 API token>"}, 401)
        return await call_next(request)

    def view(bid, b):
        p = core.public(b); st = mgr.status.get(bid, {})
        p.update(id=bid, state=mgr.state(bid), restarts=st.get("restarts", 0),
                 started_at=st.get("started_at"), connected_at=st.get("connected_at"),
                 last_msg_at=st.get("last_msg_at"), last_sync_at=st.get("last_sync_at"),
                 sync_error=bool(st.get("sync_error_at") and st.get("sync_error_at") > (st.get("last_sync_at") or 0)))
        return p

    @app.get("/", response_class=HTMLResponse)
    def index():
        return open(os.path.join(UI_DIR, "index.html"), encoding="utf-8").read()

    @app.get("/icon.png")
    def icon():
        from fastapi.responses import FileResponse
        return FileResponse(os.path.join(UI_DIR, "icon.png"))

    @app.get("/api/bots")
    def list_bots():
        return [view(k, v) for k, v in store.bots().items()]

    @app.post("/api/bots")
    def create_bot(b: BotIn):
        d = b.model_dump()
        if not d.get("name"): raise HTTPException(400, "名称必填")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", d["name"]).strip("-").lower() or "bot"
        bid = slug if not store.get(slug) else f"{slug}-{uuid.uuid4().hex[:4]}"
        store.upsert(bid, d)
        return view(bid, store.get(bid))

    @app.get("/api/bots/{bid}")
    def get_bot(bid: str):
        b = store.get(bid)
        if not b: raise HTTPException(404, "不存在")
        return view(bid, b)

    @app.put("/api/bots/{bid}")
    def update_bot(bid: str, b: BotIn):
        if not store.get(bid): raise HTTPException(404, "不存在")
        old_enabled = store.get(bid).get("enabled")
        d = b.model_dump(); sync_only = all(v is None for k, v in d.items() if k not in ("agent_id", "sync_chat_id", "sync_enabled"))
        store.upsert(bid, d)
        new = store.get(bid)
        if sync_only: return view(bid, new)
        if mgr.state(bid) != "stopped":
            mgr.restart(bid)
        elif new.get("enabled") and not old_enabled:
            try: mgr.start(bid)
            except Exception as e: mgr.log(bid, f"启用后启动失败: {e}", "error")
        if not new.get("enabled") and old_enabled: mgr.stop(bid)
        return view(bid, new)

    @app.delete("/api/bots/{bid}")
    def delete_bot(bid: str):
        mgr.stop(bid)
        if not store.delete(bid): raise HTTPException(404, "不存在")
        return {"ok": True}

    @app.post("/api/bots/{bid}/start")
    def start(bid: str):
        try: mgr.start(bid)
        except KeyError: raise HTTPException(404, "不存在")
        except ValueError as e: raise HTTPException(400, str(e))
        return view(bid, store.get(bid))

    @app.post("/api/bots/{bid}/stop")
    def stop(bid: str):
        mgr.stop(bid); return view(bid, store.get(bid))

    @app.post("/api/bots/{bid}/test")
    def test(bid: str):
        b = store.get(bid)
        if not b: raise HTTPException(404, "不存在")
        if not b.get("webhook_url"): raise HTTPException(400, "未配置 webhook")
        p = {"chat_id": "oc_test", "message_id": f"om_test_{int(time.time())}", "chat_type": "p2p",
             "sender_open_id": b.get("owner_open_id", "").split(",")[0], "bot_slug": bid,
             "bot_name": b.get("name", ""), "test": True,
             "text": "[飞书 Bot 桥连通性测试] 这是一条测试消息，无需处理，也不要回复飞书。"}
        st, body = core.post_webhook(b["webhook_url"], b["webhook_key"], p)
        mgr.log(bid, f"测试 webhook → HTTP {st}", "info" if 200 <= st < 300 else "warn")
        return {"status": st, "ok": 200 <= st < 300, "body": body[:200]}

    @app.get("/api/bots/{bid}/avatar")
    def get_avatar(bid: str):
        from fastapi.responses import FileResponse, Response
        b = store.get(bid)
        if not b: raise HTTPException(404, "不存在")
        p = core.avatar_path(bid)
        if not os.path.isfile(p) and b.get("app_id") and b.get("app_secret"):
            core.fetch_bot_avatar(dict(b, id=bid))
        if not os.path.isfile(p): return Response(status_code=404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.post("/api/bots/{bid}/avatar")
    async def set_avatar(bid: str, request: Request):
        """Body: JSON {"path": "<local file>"} or raw image bytes (UI upload). Local only; Feishu avatar untouched."""
        if not store.get(bid): raise HTTPException(404, "不存在")
        ct = request.headers.get("content-type", "")
        if ct.startswith("application/json"):
            src = (await request.json()).get("path") or ""
            if not os.path.isfile(src): raise HTTPException(400, f"文件不存在: {src}")
            with open(src, "rb") as f: data = f.read()
        else:
            data = await request.body()
        if not data or len(data) > 10 * 1024 * 1024: raise HTTPException(400, "图片为空或过大")
        with open(core.avatar_path(bid), "wb") as f: f.write(data)
        mgr.log(bid, f"头像已更新（本地，{len(data)} 字节）")
        return {"ok": True, "path": core.avatar_path(bid), "bytes": len(data)}

    @app.get("/api/status")
    def status(request: Request):
        bots = store.bots()
        return {"bots": {k: mgr.state(k) for k in bots}, "data_dir": core.data_dir(),
                "autostart": autostart.is_enabled(), "version": core.VERSION, "port": request.url.port}

    @app.get("/api/logs")
    def logs(bot: str | None = None, limit: int = 200):
        items = [e for e in mgr.logs if not bot or e["bot"] == bot]
        return items[-limit:]

    @app.post("/api/import")
    def do_import(body: dict = Body(default={})):
        base = body.get("path") or "/workspace/feishu-bridge"
        if not os.path.isdir(base): raise HTTPException(400, f"目录不存在: {base}")
        r = core.import_legacy(store, base)
        mgr.log("system", f"从 {base} 导入 {len(r)} 个机器人（均为未启用状态）")
        return r

    @app.post("/api/import-json")
    def import_json(body: dict = Body(...)):
        n = 0
        for bid, b in (body.get("bots") or {}).items():
            exists = store.get(bid)
            b = dict(b); b["enabled"] = exists.get("enabled", False) if exists else False
            b.update(core.apply_registry_fields((body.get("registry") or {}).get(bid)))
            store.upsert(bid, b); n += 1
        for bid, r in (body.get("registry") or {}).items():  # registry-only import fills sync fields of existing bots
            if store.get(bid) and bid not in (body.get("bots") or {}):
                store.upsert(bid, core.apply_registry_fields(r)); n += 1
        mgr.log("system", f"从配置文件导入 {n} 个机器人（均为未启用状态）")
        return {"imported": n}

    @app.get("/api/settings")
    def get_settings(): return {"user_display_name": store.user_name()}

    @app.post("/api/settings")
    def set_settings(body: dict = Body(...)):
        if "user_display_name" in body:
            v = str(body.get("user_display_name") or "").strip()[:40] or core.DEFAULT_USER_NAME
            with store.lock: store.data["settings"]["user_display_name"] = v; store.save()
        return {"user_display_name": store.user_name()}

    @app.get("/api/autostart")
    def get_auto(): return {"enabled": autostart.is_enabled()}

    @app.post("/api/autostart")
    def set_auto(body: dict = Body(...)):
        autostart.set_enabled(bool(body.get("enabled")))
        return {"enabled": autostart.is_enabled()}

    @app.get("/api/ping")
    def ping(): return {"app": core.APP_NAME, "version": core.VERSION}

    @app.post("/api/show")
    def show():
        if on_show: on_show()
        return {"ok": True}

    @app.get("/api/token")
    def get_token(): return {"token": token}

    @app.post("/reply")
    def reply(r: ReplyIn):
        b = store.get(r.bot) or next((v for v in store.bots().values() if v.get("name") == r.bot), None)
        if not b: raise HTTPException(404, "机器人不存在")
        ok, msg = core.send_text(b, r.target, r.text)
        mgr.log(b["id"], f"/reply → {r.target[:12]}… {'成功' if ok else '失败 ' + msg}")
        if not ok: raise HTTPException(502, msg)
        return {"ok": True, "message_id": msg}

    return app
