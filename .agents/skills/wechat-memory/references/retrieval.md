# 微信人物记忆查询

## 默认：当前 Agent 直接综合

`scripts/retrieve "问题"` 默认使用 `auto` 模式，返回 JSON：

- `messages`：消息 ID、时间、可读内容、发送者、会话、会话类型和人物。
- `facts`：可选的画像事实、置信度、人物、证据消息 ID、生成时快照与 hash。
- `retrieval`：实际使用的 `sql-fts` / `qmd-vector`、是否降级和警告。

当前 Agent 阅读这些证据后回答。这样不会在已有 session 内再套一个 Codex。

画像不是查询前置。没有任何画像时，原消息查询仍完整运行。

## 检索顺序

1. 问题中出现已知人物名时，先取该人物最近私聊消息和已有画像事实。
2. 从问题提取中英文关键词，查询 SQLite FTS5；结果按 BM25 排序。
3. FTS 不可用或未命中时，用 SQL `LIKE` 补检。
4. QMD vector 从 SQLite 派生的小型对话窗口和长消息重点卡片召回语义相关消息；窗口最多
   8 条高信息文本，重点卡片避免单条事实被周边闲聊稀释。
5. 使用 reciprocal rank fusion 合并文本与向量结果。
6. 根据 `message_id` 回 SQLite 读取原消息；画像事实命中时补回其证据消息。

模式：

- `auto` / `hybrid`：SQL/FTS + vector；向量不可用时自动降级。
- `exact`：只用人名、FTS、`LIKE`。
- `semantic`：优先 vector；不可用时降级到文本检索。

QMD 只索引安全可读文本和消息 ID。图片、表情、未转写语音、系统消息和短确认不进入向量，
但仍在 SQLite 中可查。原始 XML、token、AES key 不进入检索文档。

## 独立 CLI 查询

`wechat-memory query "问题"` 会先执行同一套检索，再启动 Codex 生成答案。它用于定时任务或纯终端场景；在 Codex session 中默认不用。

## 证据规则

- `sender_name` 等于资料所有者时，是所有者发言，不能归因给聊天对象。
- 群聊里以 `sender_name` 为人物、`chat_name` 为上下文；身份不明时明确说明。
- 没有转写文本的语音、图片、表情只能证明消息类型存在。
- 画像是压缩后的派生分析，可用于快速理解、分类和跨人物关联；事实库消息才是最终溯源入口。
- 重要判断附 `message_id`，并可用 `scripts/evidence ID` 核验当前 payload 与生成时快照。
