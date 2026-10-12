# Agent 长期记忆（D+C）设计

状态：按产品确认落地。142 一人一助手；正式业务仍只在工作台账本。

## 锁定含义

- **D**：个人偏好 + 工作事实摘要（跨会话可用）
- **C**：旧对话 / 记忆片段可检索
- **V1（线上默认）**：关键词检索；不依赖 embedding 服务
- **V2（代码预埋）**：BGE-M3（OpenAI 兼容 embeddings）+ 向量列；仅当配置填了模型与端点才启用
- A 看不到 B 的记忆与对话索引
- 记忆层 ≠ 待办/任务/周报账本；写库纪律不变（draft → 确认 → confirm）

## 启用开关

工作台 `.env`：

| 变量 | 空时 | 有值时 |
|------|------|--------|
| `EMBEDDING_MODEL` | V1 | 与 base_url 同时有则开 V2 |
| `EMBEDDING_BASE_URL` | V1 | BGE-M3 等 OpenAI 兼容 `/v1/embeddings` |
| `EMBEDDING_API_KEY` | 可选 | 若端点需要鉴权 |

派生：`memory_vector_enabled = bool(embedding_model and embedding_base_url)`。

不在 DSH / MiniMax 对话配置里挂向量模型。

## 数据

- `agent_memory_notes`：`pref` / `work_summary` / `fact`
- `agent_memory_chunks`：会话摘录、笔记切片等；`text` 必填；`embedding DOUBLE PRECISION[]` 可空（V1 不写）。相似度在应用内余弦计算；本机可另装 pgvector 扩展作后续优化，不阻塞 V1。

## 工具

签名 `POST /api/dsh/tools/*`，MCP 同名：

- `memory_get`：偏好摘要、工作摘要、近期笔记
- `memory_search`：query；V1 关键词，V2 关键词+向量
- `memory_upsert`：写 note（不替代账本写库）

## 禁止

- 用记忆声称「已创建任务/待办/周报」
- 跨用户检索
- 未配模型时强行调 embedding
- 把正式业务只写进记忆而不走 MCP confirm
