# Bot 配置指南

# 什么是 Bot 功能

最新内测版的 Server酱³ 支持类 Telegram 的 Bot 功能，不但可以下行消息、用户还可以回复（上行）消息。

# 如何配置 Server酱³ 的 Bot

## 创建 Bot

1. 登入官网https://sc3.ft07.com/，在 SendKey 页面获得 sendkey 和 uid

![img](https://sc3.ft07.com/images/20260204225914.png)

1. 下载并安装 Server酱³ 1.1.0+ 版本的客户端
   - Android 1.1.0+94 APK [下载链接](https://the7.ft07.com/upload/serverchan3-1.1.0-b94.apk)
   - iOS 1.1.0+93 TestFlight [链接](https://testflight.apple.com/join/JRY9g28a)
2. 输入Sendkey 登入
3. 点击右上角头像左边的机器人图标，进入 Bot 管理界面

![img](https://sc3.ft07.com/images/20260204230046.png)

1. 点击右上角+创建一个新的 Bot

![img](https://sc3.ft07.com/images/20260204230203.png)

1. 在 Bot 列表中左滑，点击【编辑】

![img](https://sc3.ft07.com/images/20260204230341.png)

再次进入可以看到【Bot Token】 这是我们通过 Bot 给用户发消息和获取消息的重要凭证

## 调用接口

- TOKEN : 即为 【Bot Token】
- chat_id : 即为 Server酱³ 中的 uid

### 获取 Bot 信息

```bash
curl https://bot-go.apijia.cn/bot<TOKEN>/getMe
```

### 发送推送消息

消息下行：通过 Bot 向用户发送消息

```bash
curl -X POST https://bot-go.apijia.cn/bot<TOKEN>/sendMessage \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 1, "text": "测试消息", "parse_mode": "markdown", "silent": false}'
```

- parse_mode : 可选，消息解析模式，默认 markdown
- silent : 可选，是否推送到消息通道，默认 false

状态下行：通过 Bot 向用户发送状态消息

```bash
curl -X POST https://bot-go.apijia.cn/bot<TOKEN>/sendChatAction \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 1, "action": "typing"}'
```

- action: 状态类型，目前只支持 typing，会显示为「正在思考」
- 期望返回：

```json
{"ok": true, "result": true}
```

### 轮询获取更新

如果没有配置 webhook，用户发给 Bot 的消息（上行消息）会被缓存，并支持通过以下接口轮询获取

```bash
curl https://bot-go.apijia.cn/bot<TOKEN>/getUpdates?timeout=5&offset=3
```

- timeout : 可选，轮询超时时间，默认 5 秒，最大 30 秒
- offset : 可选，从哪个 update_id 开始轮询，默认 0

轮询结果为 JSON数组，如

```json
{"ok":true,"result":[
    {"update_id": 3, "message": {"message_id": 10, "chat_id": 1, "text": "你好"}}
]}
```

或者为空

```json
{"ok":true,"result":[]}
```

## Webhook

配置好 Webhook 以后，当用户在客户端中向 Bot 发送消息（上行消息）时，我们会向 Webhook 地址发送 POST 请求。

- 配置 Webhook 地址：在 Server酱³ 客户端中，点击 Bot 管理界面的【编辑】，输入 Webhook 地址，点击【保存】
- 请求体为 JSON 格式，包含用户发送的消息内容和其他相关信息
- 10 秒超时，若未在 10 秒内响应，Server酱³ 终止回调

```json
{"ok":true,"update_id":12,"message":{"message_id":20,"chat_id":4,"text":"很好很速度"}}
```

如果有配置 webhook secret，请求头中会包含 `X-Sc3Bot-Webhook-Secret` 字段，值为配置的 secret。