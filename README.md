# 飞书 Bot 桥（Feishu Bot Bridge）

把 **Grok Bot 的每个 agent** 接到 **它自己的飞书机器人** 上，做成双向同步：

- **飞书 → Grok**：你在飞书里给机器人发消息（单聊，或群里 @ 它），桥通过飞书长连接收到后，转发到这个 agent 的 Grok Bot 例程（routine）Webhook，唤醒对应 agent 处理；同时在飞书里自动回一句「收到，处理中…」。
- **Grok → 飞书**：桥读取**本机** Grok Bot 桌面端的聊天记录，把 agent 在 Grok 里可见的回复、你在 Grok 里打的字、agent 之间的对话、选择题卡片，原样镜像到飞书单聊。飞书里点选项，会回传给 agent。

一个应用可以同时管理多个机器人，每个机器人显示连接状态、日志，支持一键测试 Webhook。自带桌面窗口、系统托盘和开机自启。

> 第一次配置？直接看 **[完整设置指南 docs/SETUP.md](docs/SETUP.md)**，从零到跑通大约 15 分钟一个机器人。

## 下载安装

到 [Releases](../../releases/latest) 下载对应平台的安装包：

| 平台 | 文件 | 安装方式 |
|---|---|---|
| Windows 10/11 | `FeishuBotBridge-Setup.exe` | 双击安装（已内置 WebView2）。也有免安装版 `FeishuBotBridge.exe` |
| macOS | `FeishuBotBridge.dmg` | 拖进「应用程序」。应用未签名，首次打开请右键 →「打开」，或执行 `xattr -dr com.apple.quarantine /Applications/FeishuBotBridge.app` |
| Linux x86_64 | `FeishuBotBridge-x86_64.AppImage` | `chmod +x FeishuBotBridge-x86_64.AppImage && ./FeishuBotBridge-x86_64.AppImage` |

**要装在哪台电脑上？** 装在**运行 Grok Bot 桌面端、并且登录了这些 agent 的那台电脑**上。Grok → 飞书 的同步读的是这台电脑本地的 Grok Bot 聊天记录，不跨电脑读取。

## 三步跑通（概要）

1. **飞书开放平台**：为每个 agent 建一个企业自建应用，开启机器人能力、加权限、事件和回调都选「长连接」，发布版本。拿到 App ID / App Secret。→ [指南第 1 节](docs/SETUP.md#1-在飞书开放平台创建机器人每个-agent-一个)
2. **Grok Bot**：让这个 agent 建一个 Webhook 触发的例程，复制它的 Webhook URL 和 Key；记下 agent id。→ [指南第 2 节](docs/SETUP.md#2-在-grok-bot-里给-agent-建一个-webhook-例程)
3. **飞书 Bot 桥**：新增机器人，填上面的值，点「测试 Webhook」，打开「启用」；给机器人发一条消息拿到单聊 chat_id，填进去再打开「聊天同步」。→ [指南第 3–5 节](docs/SETUP.md#3-在飞书-bot-桥里添加机器人)

## 功能一览

- 每个机器人一个独立工作进程，飞书长连接断线自动重连，进程挂掉自动按退避重启
- 消息去重（同一条飞书消息只转发一次），Webhook 失败自动重试
- 只接收「主人」的消息（可填多个 open_id），群聊默认只收 @ 本机器人的消息；可选「群聊接收全部消息」做群分诊机器人（分诊 bot 能自己答的就自己做，该别的 agent 管的就转给它）
- 飞书选择卡片：Grok 里的选择题会变成飞书按钮卡片，点按钮后选项回传给 agent，卡片变成「已选择」
- 群聊上下文：群消息唤醒的 bot（或 Tally 从群里派单的 bot）回复只发到该群；有群上下文时 bot↔bot 只发关键节点到群（「Tally → bot：已派」+ 任务首行、「bot 收到处理中…」、含完成/卡住等标记的「bot → Tally」卡片，正式回复照旧），其余 agent 消息只留在 Grok；无群上下文时一律只在单聊；桥代其他 bot 实际发到 Tally 群的内容会转给 Tally 的 webhook（from_bot:true，跳过自动确认）
- 可设置「我的显示名」（默认「桔子」），作为你在 Grok 里发的消息同步到飞书时的卡片标题（设置页或 `POST /api/settings`）
- 本地管理界面 `http://127.0.0.1:8765/`（仅本机可访问），配置和日志都存在本机，不上传
- 开机自启（Windows 注册表 Run 项 / macOS LaunchAgent / Linux autostart），以托盘最小化方式启动

## 文档

- [docs/SETUP.md](docs/SETUP.md)：完整设置指南（飞书后台、Grok 例程、桥的每个字段、验证、排错）
- [examples/routine-prompt.md](examples/routine-prompt.md)：可直接复制的 Grok 例程提示词模板
- [examples/bots.example.json](examples/bots.example.json)：批量导入配置的示例（不含任何真实密钥）

## 从源码运行

需要 Python 3.12：

```bash
pip install -r requirements.txt
python run.py [--port 8765] [--background] [--no-tray] [--browser] [--no-autostart-bots]
```

| 参数 | 作用 |
|---|---|
| `--port` | 管理界面端口，默认 8765 |
| `--background` | 启动时最小化到托盘（开机自启用的就是它） |
| `--no-tray` | 不显示托盘图标 |
| `--browser` | 不用原生窗口，用系统浏览器打开管理界面 |
| `--no-autostart-bots` | 启动时不自动连接已启用的机器人 |

## 打包发布

推送 `v*` 标签会触发 `.github/workflows/build.yml`，在 GitHub Actions 上分别构建 Windows 安装包、macOS dmg、Linux AppImage，并自动发布到 Releases：

```bash
git tag v0.2.6 && git push origin v0.2.6
```

版本号在 `app/core.py` 的 `VERSION`。

## 安全

- App Secret、Webhook Key 只保存在本机数据目录的 `config.json`（权限 600），界面里显示为掩码，**不要提交到仓库**。`.gitignore` 已排除 `config.json`、`*.env`、`export/`、日志和状态文件。
- 管理界面和接口只接受 `127.0.0.1` 访问；`POST /reply` 另外需要本地 API 令牌。
