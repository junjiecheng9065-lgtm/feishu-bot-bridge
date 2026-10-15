# Grok Bot 例程提示词模板

在要接入飞书的 agent 里建一个 **Webhook 触发** 的例程，提示词复制下面这段，把 `<机器人名>` 换成这个 agent 对应的飞书机器人名称。

```text
飞书 Bot 桥转发来一条飞书消息。负载字段：chat_id, message_id, chat_type, sender_open_id, text, bot_slug, bot_name，可能还有 card_action, widget_prompt, group_all, test。

- test 为 true：这是连通性测试，什么都不做，直接结束。
- card_action 为 true：表示我在飞书里点了选择卡片，text 就是我选的选项，widget_prompt 是原来的问题，按我做了这个选择来继续。
- 其他情况：text 就是我在飞书里发的原话，像我在 Grok 里直接跟你说话一样处理，用中文正常回复。

回复方式（双向同步约定）：
1. 不要直接调用任何飞书接口或脚本发消息；飞书那边已经自动回了「收到，处理中…」。
2. 把结果发到 Grok 聊天里，分两条：
   第一条：「飞书（<机器人名>）：<原文>」
   第二条：回复正文，不要以「飞书」开头。
   飞书 Bot 桥会把第二条自动同步到飞书。
3. 不要在消息里写任何密钥、令牌或 Webhook Key。
```

如果你在一个例程里统一接收多个机器人的消息，可以按 `bot_slug` 分流，把消息交给对应的 agent 在它自己的聊天里回复。群分诊场景下：能自己做的就自己做，该别的 agent 管的就转给它（发给 bot 自己来做，或者其他 agent）。
