# 与 Wiki 查询的区别

| | 微信人物记忆 | Obsidian Wiki |
| --- | --- | --- |
| 真相载体 | SQLite 中的联系人、会话、群成员、消息 | Obsidian 中的 Markdown 与 Source 原件 |
| 查询单位 | 人、消息、画像事实 | 文件、标题、段落、文本块 |
| 默认检索 | 人名路由 + SQLite FTS5 BM25 + QMD vector + `LIKE` | `rg` 精确检索 + QMD BM25 / vector / hybrid 自动路由 |
| 向量 | 有，独立命名 index；文档与索引均在 vault 外 | 有，索引在 vault 外 |
| 返回证据 | `message_id`、发送者、会话、payload hash | 文件路径、行号、片段、双链来源 |
| 适合 | 谁说过什么、某人是谁、微信里哪些人涉及某主题 | 项目状态、调研结论、个人笔记、跨文档综合 |

两者复用 QMD 模型与运行方式，但 collection、index、证据标识分开。微信 SQLite 不直接进入 QMD：先生成只含安全可读文本和 `message_id` 的派生 Markdown，再由 QMD embedding。

两者都不应该把检索结果直接当最终答案。脚本负责召回，当前 Agent 负责理解、交叉核验和表达。

需要跨库时分两次查询：先用本 Skill 取微信证据，再用 `wiki-query` 取知识库证据；回答中分别标注消息 ID 与文件路径。
