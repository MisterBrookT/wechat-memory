---
name: wechat-memory
description: 用于导入和查询本地微信人物记忆、人物画像、跨联系人主题、身份分类，或要求原消息溯源。
---

# WeChat Memory

把当前 Agent 当作理解层；脚本只负责结构化导入、混合检索和取回证据。

## 边界

- 查原始群聊时间窗、列群、爬楼摘要：用 `wechat-context`。
- 查 Obsidian 笔记、项目、调研和个人知识：用 `wiki-query`。
- 查微信人物、画像、跨联系人主题和原消息证据：用本 Skill。
- 不发送、不编辑、不自动操作微信。
- 不把 `group_member_seen` 当好友；角色允许重叠。

## 查询流程

1. 人名明确：先运行 `scripts/person "姓名"`。
2. 主题或跨人物问题：运行 `scripts/retrieve "问题"`；默认融合 SQL/FTS 与 QMD vector。
3. 当前 Agent 根据返回的 `messages` 和 `facts` 综合回答。
4. 重要判断引用 `message_id`；需要核验时运行 `scripts/evidence ID`。
5. 没有证据就说不知道；不要从空图片、语音或表情推断内容。

查询原消息不需要画像。`facts` 是可选高层派生信息；为空时仍根据 `messages` 回答。

不要默认调用 `wechat-memory query`：它会再启动一个 Codex，适合无人值守 CLI，不适合已有 Agent 的 session。

## 更新与画像

- 用户提供符合公开数据契约的 JSON：`scripts/import-json FILE`；完成后按需运行 `scripts/index`。
- 本 Skill 不提取或解密微信数据，也不操作微信客户端。
- 用户明确要求生成或刷新人物画像：`scripts/profile "姓名"`。
- 查询不会自动生成画像；原始结构化事实与派生画像分库存储。

## 可视化

`wechat-memory serve` 打开本地只读人脉星图。它展示私聊与群内发言形成的互动网络，不承担自然语言查询，也不把连线解释成亲密度。

## 参考

- 每个脚本的用途与输出：`references/scripts.md`
- 查询顺序、证据语义与限制：`references/retrieval.md`
- 与 Wiki 查询的区别：`references/wiki-query-comparison.md`
- 分层、数据库与可重建边界：`references/architecture.md`
