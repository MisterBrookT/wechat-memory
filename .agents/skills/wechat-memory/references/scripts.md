# 脚本说明

所有脚本只调用 PATH 中的 `wechat-memory`，不依赖仓库绝对路径。

| 脚本 | 做什么 | 是否调用 Agent | 是否写数据库 |
| --- | --- | --- | --- |
| `status` | 返回身份、会话、消息、画像数量和最近导入状态 | 否 | 否 |
| `import-json` | 按公开数据契约导入用户提供的结构化 JSON | 否 | 是，事实库 |
| `classify` | 根据规范化会话与群成员关系刷新身份角色 | 否 | 是，事实库 |
| `index` | 从安全可读消息生成 QMD 文档，并更新独立微信向量索引 | 否 | 是，派生检索层 |
| `retrieve` | 融合 SQL/FTS 与 QMD vector，取回原消息和可选画像事实 | 否 | 否 |
| `person` | 返回一个人的身份、已有画像、结构化事实 | 否 | 否 |
| `evidence` | 按消息 ID 回看原消息、payload hash 和画像证据快照 | 否 | 否 |
| `profile` | 为指定人物生成或刷新可重建画像 | 是，启动一次分析 Agent | 是，分析库 |

典型调用：

```bash
scripts/status
scripts/import-json /path/to/export.json
scripts/index
scripts/retrieve "谁提到过 Agent 产品？" --limit 80
scripts/person "张三"
scripts/evidence 123
scripts/profile "张三"
```

`import-json` 只接受 `schemas/import.schema.json` 定义的数据，不负责提取、解密或操作客户端。重复导入同一 namespace 和稳定消息 ID 是幂等的。导入后再运行 `index`；它只更新可重建检索层。
