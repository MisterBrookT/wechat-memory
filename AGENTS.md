# AGENTS.md

## 目标

本项目把用户提供的结构化聊天数据变成人物记忆库：稳定存储、人物画像、可溯源查询。

## 边界

- 入口仅接受 `schemas/import.schema.json` 定义的结构化 JSON。
- 禁止加入取钥、解密、内存扫描、逆向、Hook/注入、访问控制绕过或私有数据库解析。
- 不发送、编辑、撤回、群发或操作微信客户端。
- 原始记录版本只追加、不覆盖。画像与回答必须保留 `message_id`、证据哈希与引用快照。
- 数据库、导出、日志不得提交 Git，不写入 Obsidian vault。
- 导入必须幂等、可恢复；稳定 ID 不得因重复导入产生重复记录。
- 语义判断交给 Codex；SQLite 负责确定性存储、过滤、全文检索和证据校验。

## 实现

- Python 3.11+，优先标准库。
- SQLite + FTS5；迁移向前兼容。
- CLI 输出 JSON；进度与警告写 stderr；不打印原始聊天全文。
- 默认数据：`~/Library/Application Support/wechat-memory/crm.sqlite`。
