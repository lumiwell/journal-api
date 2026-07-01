# Journal API - 后端开发规范 (Agent Rules)

在处理 `journal-api` 项目时，必须严格遵守以下技术栈与架构约定：

## 1. 技术栈选型
*   **核心框架**：FastAPI (异步模式) + PostgreSQL 16 (`pgvector` 就绪)。
*   **ORM 规范**：强依赖 `SQLModel` + `asyncpg` (`AsyncSession`)。除复杂更新外，禁止直接使用纯 SQLAlchemy 模型。

## 2. 核心架构与业务哲学
*   **单一时间线 (Timeline)**：秉持“一个人只有一条时间线”的哲学。匿名会话 (`guest_session_id`) 在用户注册/登录时，必须通过底层原子操作 (如 `update().where().values()`) 无缝合并到用户的主会话中，绝不能产生分裂。
*   **后端组装上下文**：前端每次只发最新的一条消息。后端负责提取历史对话（滑动窗口），且**系统提示词 (System Prompt) 必须永远固定在 `messages[0]`**。

## 3. 并发、安全与异常处理
*   **并发防抖 (Advisory Locks)**：任何调用大模型等高耗时、非幂等的操作（如萃取日记），必须在最开始获取 PostgreSQL 的事务级建议锁（`pg_try_advisory_xact_lock`），获取失败立即返回 429，防止用户狂点按钮导致并发漏洞。
*   **防 ID 探测 (Anti-IDOR)**：在通过 ID 获取任何隐私数据（如某篇日记）时，必须校验其是否归属 `current_user`。若越权访问或不存在，**统一抛出 `404 Not Found`（绝不能抛出 403）**，防止黑客枚举资源。
*   **流式响应安全**：严禁在 `async for chunk in stream` 循环内部进行数据库写操作。必须先将内容 Yield 给前端，等 `[DONE]` 结束后再统一 Commit 落库，防止数据库断连拖垮整个流。

## 4. SQLModel 与静态类型避坑 (重点)
为了避免 IDE 爆红（Pyright / Pylance）和 500 崩溃，请牢记：
*   **where 条件包裹**：在编写 `select` 或 `update` 的 `.where()` 条件时，必须使用 `col()` 包裹模型字段。例如：`.where(col(Message.session_id) == id)`，防止被推导为 bool。
*   **执行与解析**：必须使用 `await db.exec(statement)` 替代弃用的 `db.execute()`。注意：对于 `select`，`db.exec()` 返回的已经是 `ScalarResult`，**千万不要再去调用 `.scalars()`**（如 `result.scalars().all()` 会引发 500 崩溃，直接 `result.all()` 即可）。

## 5. 本地环境
*   测试库：`localhost:5432` (User: `lumi`, Pass: `123456`, DB: `journal`)。
