# 飞书 Bot 桥（Feishu Bot Bridge）

## 这是什么
一个本地运行的小工具：通过飞书开放平台长连接接收飞书机器人消息，并转发到你配置的自动化 Webhook；同时提供本地 HTTP 接口，把回复发回飞书会话。自带 Web 管理界面和系统托盘。

## 安装
到 [Releases](../../releases) 下载对应平台安装包：
- Windows：`FeishuBotBridge-Setup.exe`，双击安装
- macOS：`FeishuBotBridge.dmg`，拖入“应用程序”（未签名，首次打开需右键→打开）
- Linux：`FeishuBotBridge-x86_64.AppImage`，`chmod +x` 后运行

从源码运行：
```bash
pip install -r requirements.txt
python run.py [--port 8765] [--background] [--no-tray] [--no-autostart-bots]
```

## 配置
1. 启动后打开 http://127.0.0.1:8765/
2. 在界面中添加机器人：填写飞书应用的 App ID、App Secret，以及要转发到的 Webhook 地址和密钥。
3. 配置保存在本机数据目录（不会上传）：
   - Windows：`%APPDATA%\FeishuBotBridge`
   - macOS：`~/Library/Application Support/FeishuBotBridge`
   - Linux：`~/.config/FeishuBotBridge`
   - 可用环境变量 `FEISHU_BRIDGE_HOME` 覆盖
4. 回复接口：`POST /reply`，JSON `{"bot": "...", "target": "om_xxx 或 oc_xxx", "text": "..."}`，请求头 `Authorization: Bearer <令牌>`（令牌通过 `GET /api/token` 获取）。

> 请勿把 App Secret、Webhook 密钥提交到仓库。

## 打包
推送 `v*` 标签触发 `.github/workflows/build.yml`，构建 Windows 安装包 / macOS dmg / Linux AppImage。
