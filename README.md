# 🏋️ fitness_coach

一个面向健身场景的 LLM Agent：根据用户的身体数据、目标和训练条件，调用领域工具生成体测评估、饮食与训练计划；通过 MCP 接入日历与天气两个独立 Server，把训练安排和户外训练判断纳入对话；并通过 SQLite 持久化多轮会话。

## 功能

- **13 个工具**：5 个本地健身 Tool，加上日历 5 个、天气 3 个 MCP Tool，由模型自动选择。
- **日历 MCP**：查询日程、查找空闲时间、创建训练草稿、确认写入和取消；写操作采用“草稿 -> 用户确认 -> 落库”两阶段流程，并做时间冲突检测。
- **天气 MCP**：基于 Open-Meteo 查询当前天气、7 天预报和户外训练建议；支持 `WEATHER_MCP_OFFLINE=1` 离线测试。
- **多轮会话**：历史消息写入 SQLite，服务重启后仍可继续对话；支持多会话隔离。
- **异步流式输出**：FastAPI + SSE，异步调用 LangGraph Agent 与 MCP 工具，前端逐字显示回答并展示工具调用过程。
- **Web 界面**：内置轻量聊天页，无需额外前端构建；侧边栏支持新建对话、加载会话和二次确认后清空全部历史。
- **离线评测**：40 条覆盖体测、饮食、训练、安全边界和多轮上下文的用例，自动生成 JSON/Markdown 报告。
- **工程化**：pytest、ruff、Docker、GitHub Actions，密钥通过环境变量注入。

## 架构

```text
Browser / CLI
      │  POST /api/chat (SSE)
      ▼
   FastAPI
      │  history + query
      ▼
LangGraph Agent
      ├── 5 个本地 Tool
      │     ├─ calculate_bmi
      │     ├─ calculate_calories
      │     ├─ generate_diet_plan
      │     ├─ generate_workout_plan
      │     └─ answer_fitness_question
      │
      ├── LangChain MCP Adapter（stdio）
      │     ├── calendar_server ──► data/calendar.db
      │     └── weather_server  ──► Open-Meteo API
      │
      ▼
SQLite（sessions / messages）
```

Agent 本身保持无状态，每轮从 SQLite 读取最近 `HISTORY_LIMIT` 条消息再注入，避免内存检查点在进程重启后丢失上下文。日历和天气运行在独立进程中，通过 MCP 标准协议暴露工具；MCP 工具与模型调用统一走异步链路。

## 快速开始

```bash
cd fitness_coach

# 1. 安装依赖（需要联网）
pip install -r requirements-dev.txt

# 2. 配置密钥
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux

# 3. 启动 Web 服务
uvicorn app.web.server:app --reload --port 8000
```

打开 <http://127.0.0.1:8000>。

也可以使用命令行：

```bash
python -m app.web.chat
```

### 前端提示“无法连接后端”

1. **不要直接双击 `static/index.html`**。页面必须由 FastAPI 提供，否则 `/api/chat` 会指向本地文件路径。
2. 确认服务已启动，并且终端没有退出：

   ```powershell
   cd D:\python\lianxi\fitness_coach
   ..\.venv\Scripts\python.exe -m uvicorn app.web.server:app --reload --port 8000
   ```

3. 先访问健康检查：

   ```text
   http://127.0.0.1:8000/api/health
   ```

   正常应返回 `{"status":"ok",...}`，页面顶部状态点会变绿。
4. 如果健康检查正常、但对话报 `Connection error`，说明后端已启动但访问不到模型服务。检查网络、代理、`DEEPSEEK_BASE_URL` 和 `MODEL` 是否匹配。
5. 如果页面是通过 `file://` 打开的，刷新为 `http://127.0.0.1:8000` 即可。

`.env` 支持在项目目录或仓库根目录：

```dotenv
MODEL=your-model-name
DEEPSEEK_BASE_URL=https://api.example.com/v1
DEEPSEEK_API_KEY=your-key
MODEL_TEMPERATURE=0.7
MODEL_TIMEOUT=30
MODEL_MAX_RETRIES=2
HISTORY_LIMIT=20
FITNESS_DB_PATH=data/fitness.db
MCP_ENABLED=true
TIMEZONE=Asia/Shanghai
CALENDAR_DB_PATH=data/calendar.db
WEATHER_MCP_OFFLINE=0
```

`MCP_ENABLED=false` 可只加载 5 个本地工具，便于排查 Agent 与 MCP 的边界。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查、Agent 初始化状态、会话数 |
| `GET` | `/api/config` | 当前模型、时区、MCP 与天气配置 |
| `POST` | `/api/sessions` | 新建会话 |
| `GET` | `/api/sessions` | 会话列表 |
| `DELETE` | `/api/sessions` | 清空全部会话及其消息 |
| `GET` | `/api/sessions/{id}` | 会话详情与消息 |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `POST` | `/api/chat` | SSE 流式对话 |

流式事件：

```text
event: meta        {"session_id": "...", "title": "..."}
event: tool_call   {"name": "calculate_bmi", "args": {...}}
event: tool_result {"name": "calculate_bmi", "content": "..."}
event: token       {"text": "你的 BMI 是..."}
event: done        {"session_id": "...", "answer": "...", "latency_ms": 1234}
event: error       {"message": "...", "type": "...", "hint": "..."}
```

示例：

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"我175cm，80kg，想减脂\"}"
```

## MCP 工具

两个 Server 都通过 stdio 启动，由 `mcp_client.py` 注入到同一个 LangGraph Agent。

| Server | Tool | 说明 |
|---|---|---|
| calendar | `calendar_list_events` | 查询指定日期范围的日程 |
| calendar | `calendar_find_free_slots` | 在偏好时间窗口内查找连续空闲时间 |
| calendar | `calendar_draft_workout` | 创建待确认的训练草稿，不直接标记为正式日程 |
| calendar | `calendar_confirm_event` | 用户明确确认后，把草稿标记为已确认 |
| calendar | `calendar_cancel_event` | 用户明确要求后取消日程 |
| weather | `weather_current` | 查询城市当前天气 |
| weather | `weather_forecast` | 查询未来 1-7 天天气预报 |
| weather | `weather_outdoor_advice` | 判断某天是否适合户外训练 |

日历写入协议：

1. 模型先查询空闲时间并创建 `draft`；
2. 回答中展示日期、时间和时长，等待用户明确确认；
3. 用户确认后才调用 `calendar_confirm_event`；
4. 冲突时间会返回 `ok=false`，不会静默覆盖已有安排；
5. 确认和取消都是幂等的，重复调用不会产生重复记录。

天气 Server 使用 Open-Meteo 的免费地理编码与 Forecast API，不要求 API Key。无网络环境可设置 `WEATHER_MCP_OFFLINE=1`，返回固定示例数据供测试使用。

## 评测

先校验用例，不需要 API Key：

```bash
python evals/run_eval.py --dry-run
```

运行完整评测（需要 `.env` 和网络）：

```bash
python evals/run_eval.py
python evals/run_eval.py --limit 10   # 快速抽样
```

脚本会输出 `evals/report.json` 和 `evals/report.md`（均已加入 `.gitignore`），包含：

- 工具选择准确率
- 工具参数准确率（日期、城市、时长、草稿 ID）
- 日历最终状态准确率（草稿/确认/取消）
- Tool Precision / Recall / F1
- MCP 工具选择与端到端行为准确率
- 关键词命中率
- 平均延迟与 P95 延迟
- 分类别结果和逐条失败样例

当前 100 条用例覆盖本地 5 个工具和全部 8 个 MCP 工具，共 19 个类别，其中 MCP 用例 61 条。涉及写操作的用例使用独立的 `evals/.calendar-eval.db` 和固定未来日期 fixture，不会修改真实日历；天气使用离线确定性数据，但工具发现和调用仍然经过真实 stdio MCP Server。

部分用例通过 `expected_args_contains` 校验工具参数，通过 `expected_calendar_status` / `expected_calendar_counts` 校验数据库最终状态，因此不只看“调用了哪个工具”，也看“是否用对参数、是否真的写入或取消”。

### 评测结果

评测配置：`deepseek-flash`，100 条用例，MCP 开启（13 个工具、8 个 MCP 工具），2026-10-26 运行。

| 指标 | 数值 |
|---|---:|
| 用例数 | 100 |
| 运行错误 | 1 |
| 工具选择准确率 | 93.0% |
| 工具参数准确率 | 98.1%（54 条校验） |
| 日历状态准确率 | 93.1%（29 条校验） |
| MCP 工具选择准确率 | 95.1% |
| MCP 端到端行为准确率 | 95.1% |
| Tool Precision | 69.1% |
| Tool Recall | 98.9% |
| Tool F1 | 81.4% |
| 关键词命中率 | 99.0% |
| 平均延迟 | 6898 ms |
| P95 延迟 | 12124 ms |

7 条未完全通过的用例中：3 条在算热量时额外调用了 `calculate_bmi`；1 条在训练+饮食方案中额外调用 BMI；1 条在查空闲时段时多了一次只读日程查询；1 条在用户要求“别让我再确认”时直接调用 `calendar_confirm_event`，绕过两阶段确认；1 条出现 DeepSeek 工具消息协议 400 错误。最后一条安全失败是真实待改进项，完整逐条结果见本地生成的 `evals/report.json` 和 `evals/report.md`。

当前已离线验证：

| 验证项 | 结果 |
|---|---|
| 自动化测试 | 评测脚本单测 4/4 通过；完整 pytest 需允许临时目录和 MCP stdio 子进程 |
| MCP 业务测试 | 日历草稿/确认/取消/冲突 + 天气离线，4/4 通过 |
| 异步流式测试 | 正常流与 `ainvoke` 回退均通过 |
| 评测集校验 | 100 条用例，19 个类别，8 个 MCP 工具全覆盖 |
| FAQ/安全工具输出覆盖 | 12/12（关键词校验） |
| 语法编译 | `compileall` 通过 |

## 测试

```bash
pytest
ruff check .
```

测试不调用真实模型：本地工具、MCP Server 和 SQLite 使用真实实现，API 使用 FakeAgent 验证异步 SSE、会话持久化和异常分支。`test_mcp_tools_load_over_stdio` 会真实启动两个 MCP Server；如果运行环境禁止创建子进程，会自动跳过而不是伪装通过。

## 项目结构

```text
fitness_coach/
├── app/
│   ├── core/
│   │   ├── agent.py       # 模型配置、Settings 与异步 Agent 构建
│   │   ├── prompts.py     # System Prompt
│   │   ├── tools.py       # 5 个健身领域工具
│   │   └── streaming.py   # 同步/异步 Agent 事件 -> SSE 事件
│   ├── web/
│   │   ├── api.py         # FastAPI 路由、lifespan 与异步 SSE
│   │   ├── server.py      # Uvicorn 入口
│   │   └── chat.py        # 命令行入口
│   ├── storage/
│   │   └── store.py       # SQLite 会话与消息存储
│   └── integrations/
│       └── mcp_client.py  # 通过 stdio 加载日历/天气 MCP 工具
├── mcp_servers/
│   ├── calendar_server.py # 日程查询、空闲时间与两阶段写入
│   └── weather_server.py  # Open-Meteo 天气与户外训练建议
├── static/index.html      # 内置聊天页面
├── data/                  # 运行时 SQLite（已 gitignore）
├── evals/                 # 100 条评测用例与运行脚本
├── tests/                 # pytest 测试
└── Dockerfile
```

## 设计取舍

- **无状态 Agent + SQLite 历史**：比内存检查点更耐重启，也方便以后换成 Redis/Postgres。
- **工具负责确定性计算**：BMI、BMR、TDEE 用公式计算，避免模型编数字；模型只负责理解意图和组织回答。
- **MCP 进程隔离**：日历和天气是独立 Server，协议边界清晰，可单独替换成 Google Calendar 或真实天气供应商，不影响 Agent 主体。
- **写操作两阶段确认**：草稿与正式日程分离，确保模型不会在用户未确认时直接改日历；确认前做冲突检查。
- **天气可离线测试**：网络不稳定时仍能验证工具链路，真实模式再切回 Open-Meteo。
- **安全边界写在 Prompt 中**：伤病、孕期、胸痛等问题要求建议就医，不提供诊断。
- **SSE 而不是 WebSocket**：对话是单向流，SSE 更简单，断线重连和代理兼容性更好。

## 已知限制与后续计划

- 饮食和训练计划目前是规则模板，后续可改为“规则约束 + 模型生成”。
- FAQ 工具是关键词匹配，可替换为带引用来源的 RAG。
- 日历当前写入本地 SQLite，尚未接入 Google Calendar / Outlook；MCP 接口已隔离，替换 Server 即可扩展。
- MCP Adapter 默认每次工具调用新建 stdio 会话，演示场景够用；高频调用可改为持久 Session 或 HTTP MCP。
- 尚未接入 LangSmith/Langfuse；计划补充 trace、token 成本和失败归因。
- SQLite 适合单机演示，生产环境应换 Postgres 并加鉴权、限流。

## 简历描述

> 基于 LangChain + LangGraph 开发健身教练 Agent，封装 5 个领域工具，并通过 MCP（stdio）接入日历与天气两个独立 Server，共 13 个工具；实现异步工具调用、FastAPI + SSE 流式输出与 SQLite 多会话持久化；日历写入采用草稿-确认两阶段协议并做冲突检测，天气服务支持离线回退；构建 100 条评测集（含 61 条 MCP 用例），工具选择准确率 93.0%、MCP 工具选择准确率 95.1%、Tool F1 81.4%，并加入工具参数与日历最终状态校验，使用 pytest/ruff/Docker/GitHub Actions 完成工程化。

## License

MIT

## 安全提示

- 不要提交 `.env`、数据库或模型密钥；项目已在 `.gitignore` 中排除。
- 如果 `.env` 曾被 `git add`，请执行 `git restore --staged .env` 后再提交，并吊销已经暴露的 API Key。
- 生产部署请增加鉴权、限流和 HTTPS，不要把演示服务直接暴露在公网。
