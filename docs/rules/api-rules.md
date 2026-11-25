# API 设计规则（api-rules）

| 项 | 内容 |
|---|---|
| 版本 | v1.1（2026-11-25 评审整改：新增 edit_conflict 错误码、manual 状态 8 态对齐） |
| 创建日期 | 2026-11-25 |
| 适用范围 | 本项目全部 REST 接口与 WebSocket 事件契约的设计、实现与变更 |
| 上游依据 | 《02.整体架构设计.md》v1.0（D1~D13，含"WebSocket 为主"选型与"接入层仅监听 127.0.0.1"约束）；《03.数据库设计.md》；《项目需求文档.md》v1.1 |
| 强制力 | 涉及 API 设计或修改时，必须先阅读并遵守本规则；与本规则冲突的实现一律以本规则为准，修改规则需更新版本号并在变更记录注明 |

---

## 1. 通用约定

1. **部署形态**：前后端同源——SPA 构建产物由 FastAPI 静态托管，REST 与 WS 均走同一 host（仅监听 `127.0.0.1`）；无跨域、无认证（本地单用户，需求 3.1）；
2. **路径前缀**：REST 统一 `/api`；本应用为本地单用户工具，**不做版本号前缀**（`/api/v1/...` 不采用）；契约变化由本文件与《04.API设计.md》版本记录管理；
3. **数据格式**：请求与响应体均为 JSON（UTF-8），`Content-Type: application/json`；唯一的二进制端点是 PDF 文件流（见第 6 节）；
4. **字段命名**：snake_case；时间字段以 `_at` 结尾（ISO 8601 UTC TEXT），业务日期字段以 `_date` 结尾（`YYYY-MM-DD`）——与 database-rules 第 6 节一致；
5. **布尔与枚举**：布尔用 JSON true/false；枚举值与数据库 CHECK 约束中的值严格一致（如 `manual.status` 的 8 态），禁止 API 层自造别名；
6. **路径类参数**：笔记路径（vault 相对路径）作为路径参数时必须 URL 编码；正斜杠 `/` 为路径分隔符，统一使用 `/` 不用反斜杠。

## 2. REST 设计规范

1. **资源命名**：名词复数、层级不超过两段（如 `/api/manuals/{id}/tasks` 允许；更深层级改用动作子资源）；
2. **方法语义**：GET 只读且无副作用；POST 创建资源或触发动作；PUT 整体更新；PATCH 部分更新；DELETE 删除（服务层显式事务，对应 database-rules 第 3 节"禁级联"）；
3. **动作型端点**：无法映射到 CRUD 的业务动作用 `POST /api/<资源>/<id>/<动作>` 表达（如 `/api/manuals/3/rebuild`），动作名用动词小写；
4. **幂等性**：GET/PUT/DELETE 幂等；POST 动作端点须明确幂等语义——重复触发同一动作时返回既有进行中任务而非新建（如重复入库同一手册返回已排队任务）；
5. **列表分页**：本应用数据规模小（笔记 ≤50、手册几十份、会话数十），列表默认**全量返回**，不引入分页参数；仅会话消息量大时支持 `?limit=&before_id=` 游标参数（可选）；
6. **过滤参数**：列表接口的筛选走查询参数，参数名与数据库列名一致（如 `GET /api/manuals?brand=ACS&status=ready`）；
7. **响应包络**：
   - 成功：直接返回资源 JSON 或 `{ "data": ... }`（列表含集合语义时用 data 包裹）；
   - 失败：统一 `HTTP 4xx/5xx` + `{ "error": { "code", "message", "detail"? } }`，`code` 取自第 5 节错误码枚举，`message` 为可直接展示给用户的中文文案；
8. **状态码使用**：200 成功；201 创建成功；400 参数错误；404 资源不存在；409 状态冲突（如对非 failed 任务重试）；422 校验失败（Pydantic）；500 内部错误（不向客户端暴露堆栈）；
9. **空值约定**：可空字段返回 `null`（不省略键），前端不做"键是否存在"判断。

## 3. WebSocket 规范

1. **连接划分**（对应 02 文档 D 系列"WebSocket 为主"选型）：
   - `/ws/events`——全局唯一状态通道：服务端推送 + 客户端上下文上报（本次交付范围唯一 WS）；
   - `/ws/terminal/{tab_id}`——终端字节流通道（**三期**，本期不实现，仅预留）；
2. **消息包络**：所有 WS 消息为 JSON：`{ "topic", "type", "payload" }`；`topic` 为事件域（见第 4 节枚举），`type` 为 `event`（服务端推送）/`report`（客户端上报）/`error`；
3. **单向语义**：events 通道上服务端→客户端为推送；客户端→服务端**仅允许**上下文上报（`context.report`），不允许经 WS 触发业务动作（业务动作一律走 REST，保证可审计）；
4. **重连与对账**：断线重连后，客户端必须**全量对账**——重新拉取受影响资源的 REST 快照（如 `GET /api/ingest/tasks`、`GET /api/notes`），WS 推送只做"提示刷新"，不作为唯一数据来源；RAG 回答流丢失的兜底见第 4 节 `rag.stream` 说明；
5. **心跳**：服务端每 30s 发 `ping` 帧，客户端 10s 内未收到任何帧即判定断线发起重连（本地回环，宽松即可）；
6. **背压**：推送频率控制——进度类事件（`ingest.progress`）按变化节流（≥500ms 间隔），token 流帧按 LLM 输出节奏原样转发不节流。

## 4. events 通道 topic 枚举（本次交付范围）

| topic | 方向 | payload 要点 | 对应需求 |
|---|---|---|---|
| `note.changed` | 推送 | path、变更类型（created/modified/deleted/moved）、customer/project（可解析时） | FR-NOTE-06 |
| `index.updated` | 推送 | path、indexed_status、last_error（失败可见可重试） | FR-RAG-03 |
| `ingest.progress` | 推送 | task_id、manual_id、status、progress、error | FR-RAG-08 |
| `manual.updated` | 推送 | manual_id、status、progress（状态机 8 态广播） | FR-RAG-08~10 |
| `rag.stream` | 推送 | message_id、delta（增量文本）、done（bool）；**断线兜底**：done 后可按 message_id 经 REST 补拉完整回答与引用 | FR-RAG-16 |
| `config.updated` | 推送 | 变更的配置 key 列表（不含值，值经 REST 拉取） | FR-CFG 热生效 |
| `backup.done` | 推送 | backup_id、status | OQ-10 |
| `context.report` | 上报 | 当前打开笔记的 path/customer/project（RAG 自动感知上下文，客户端切换笔记时上报） | FR-RAG-18 |

## 5. 错误码枚举（error.code）

| code | HTTP | 场景 |
|---|---|---|
| `invalid_request` | 400 | 参数缺失/格式错误 |
| `path_not_found` | 404 | vault/PDF 路径不存在或不在白名单根内 |
| `resource_not_found` | 404 | 数据库记录不存在 |
| `path_conflict` | 409 | 目标笔记路径已存在（新建记录重名） |
| `edit_conflict` | 409 | 笔记保存冲突：外部修改且未带 force（FR-NOTE-05，禁止静默覆盖） |
| `state_conflict` | 409 | 状态机不允许的操作（如对 ready 手册重试、对排队中任务再次入库） |
| `head_invalid` | 422 | 笔记头部字段不完整（保存仍允许，返回警告；仅整理向导相关接口按需阻塞） |
| `config_invalid` | 422 | 配置值校验失败（Pydantic） |
| `path_outside_root` | 400 | 请求路径越出配置根目录（防目录穿越，安全项） |
| `model_error` | 502 | 云端 LLM/Embedding 调用失败（含超时、鉴权失败，detail 区分） |
| `parse_error` | 500 | MinerU 解析失败（入库任务 error 字段同步记录） |
| `internal_error` | 500 | 兜底 |

## 6. 特殊端点约定

1. **PDF 文件流**：`GET /api/reader/pdf/{manual_id}` 返回 `application/pdf` 字节流，**必须支持 HTTP Range**（pdf.js 按需加载大手册）；未入库手册预览走同一端点（C2 双模式，Q-04）；
2. **API Key 脱敏**（安全铁律，对应 NFR-SEC-03 / database-rules 第 8 节）：任何接口**永不返回** `llm.api_key` / `embedding.api_key` 原文；配置读取时返回 `{ "api_key_set": true, "api_key_masked": "sk-***abc" }`；前端提交空/掩码值表示"不修改"；
3. **路径安全**：所有接收文件路径的接口必须校验路径位于对应配置根（vault_root / pdf_root）之内，越界一律 `path_outside_root`；
4. **配置测试连接**：`POST /api/config/test-llm`、`POST /api/config/test-embedding` 接受与保存接口相同的配置体但**不落库**，返回连通性与模型可用性结果（含耗时），供 FR-CFG-01/02 的"测试"按钮。

## 7. 契约与实现的一致性

1. **资源 ↔ 服务 ↔ 表映射**：每个 REST 资源在《04.API设计.md》中标注对应后端服务与数据库表；实现时三者必须对齐，新增接口先补设计文档；
2. **OpenAPI**：FastAPI 自动生成 OpenAPI 文档（仅开发期访问 `/api/docs`），实现期以 Pydantic 模型与本文档双向校验；
3. **枚举单一来源**：API 枚举值以数据库 CHECK 约束为唯一来源，前后端共享的类型定义在实现期由同一枚举生成，禁止多处手写漂移。

## 8. 变更规则

1. 接口新增：先在《04.API设计.md》补充条目（版本号 +1）再实现；
2. 接口破坏性变更：原则上不允许（本地单用户无外部消费者，破坏性变更直接修改并同步前端，但仍需文档记录）；
3. topic 新增：同样先文档后实现；废弃 topic 保留一个版本周期后移除。

## 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-11-25 | 首次创建：通用约定、REST/WS 规范、topic 枚举、错误码、脱敏与路径安全 |
| v1.1 | 2026-11-25 | 评审整改：新增 `edit_conflict` 错误码（FR-NOTE-05 冲突保存）；manual 状态枚举对齐为 8 态（Q-15，新增 discovered） |
