# Harness Engineering for Real Agents - 课程测试记录

用途：记录 12 节课每一课的测试命令、观察结果、是否通过，以及后续待补事项。后续每完成一课，就持续更新这份文档。

状态说明：

- `passed`：本课已完成最小实现，且至少跑通一次关键验证
- `partial`：本课有部分实现或部分验证，但未满足验收标准
- `pending`：本课尚未开始

---

## s01 - The Agent Loop

- 状态：`passed`
- 日期：`2026-04-20`
- 实现文件：`agents/s01_agent_loop.py`
- 测试目标：
  - 验证最小 agent loop 能发起模型请求
  - 验证模型能调用 `bash`
  - 验证工具结果会返回给模型并生成最终答案
- 环境说明：
  - 使用第三方兼容 Anthropic 的 endpoint
  - `ANTHROPIC_BASE_URL` 已配置为 `https://models-proxy.stepfun-inc.com`
- 测试命令：

```bash
printf 'List all Python files in this directory\nq\n' | python3 agents/s01_agent_loop.py
```

- 关键观察：
  - 脚本成功启动
  - 模型选择调用 `bash`
  - 实际执行了 `find ... -name "*.py" -type f`
  - 最终返回了当前仓库中的 Python 文件列表
- 结果：`passed`
- 备注：
  - 启动时有 `readline` 兼容性提示，但不影响功能
  - 本课核心 loop 已验证可用

---

## s02 - Tool Use

- 状态：`passed`
- 日期：`2026-04-20`
- 实现文件：`agents/s02_tool_use.py`
- 测试目标：
  - 验证在不改 agent loop 的前提下新增工具
  - 验证模型能优先使用专用文件工具，而不是一律走 `bash`
  - 验证 `read_file` 可正常返回文件内容
- 测试命令：

```bash
printf 'Read the file requirements.txt\nq\n' | python3 agents/s02_tool_use.py
```

- 关键观察：
  - 脚本成功启动
  - 模型调用了 `read_file`
  - `requirements.txt` 内容被正确返回
  - 这次验证说明 dispatch map 与工具注册工作正常
- 结果：`passed`
- 备注：
  - 当前已实现 `bash`、`read_file`、`write_file`、`edit_file`
  - 路径访问已通过 `safe_path()` 约束在工作区内

---

## s03 - TodoWrite

- 状态：`passed`
- 日期：`2026-04-23`
- 实现文件：`agents/s03_todo_write.py`
- 测试目标：
  - 验证 agent 会先写待办再执行
  - 验证任务状态可从 `pending` 变为 `in_progress` 再到 `completed`
  - 验证模型能根据待办列表调整下一步动作
- 测试命令：

```bash
printf 'Create a directory called lesson3_demo with a Python package: add __init__.py, utils.py with an add(a, b) function, and tests/test_utils.py\nq\n' | python3 agents/s03_todo_write.py
```

- 关键观察：
  - 脚本成功启动
  - 模型先调用了 `todo`，写出分步计划
  - 在执行过程中，模型持续更新 todo 状态，从 `in_progress` 推进到 `completed`
  - 模型使用了 `write_file` 创建 `lesson3_demo/__init__.py`、`lesson3_demo/utils.py`、`lesson3_demo/tests/__init__.py`、`lesson3_demo/tests/test_utils.py`
  - 运行 `pytest` 时因本地缺少 `pytest` 模块失败，随后模型改用直接执行测试文件的方式完成验证
  - 生成文件已确认存在，`utils.py` 中包含 `add(a, b)` 实现，测试文件可运行
- 结果：`passed`
- 备注：
  - 当前 `todo` 工具已具备单个 `in_progress` 约束
  - loop 中已加入 reminder 注入机制，后续可再专门做一轮 reminder 行为测试

---

## s04 - Subagent

- 状态：`passed`
- 日期：`2026-04-23`
- 实现文件：`agents/s04_subagent.py`
- 测试目标：
  - 验证主 agent 能派发子任务
  - 验证子 agent 使用独立上下文
  - 验证子任务结果能回传主流程
- 测试命令：

```bash
printf 'Use a subtask to find what testing framework this project uses\nq\n' | python3 agents/s04_subagent.py
```

- 关键观察：
  - 脚本成功启动
  - 父 agent 调用了 `task` 工具，而不是直接自己读文件
  - `task` 工具触发了子 agent，子 agent 在独立上下文中完成项目检查
  - 父 agent 最终只收到子 agent 的摘要文本，没有接收子 agent 的完整中间历史
  - 子 agent 给出的结论是项目使用 `pytest`，后续核对仓库内容也能找到对应证据：`tests/test_agents_smoke.py`、`lesson3_demo/tests/test_utils.py`、`.pytest_cache/`
- 结果：`passed`
- 备注：
  - 本课重点已验证：上下文隔离来自“父子 agent 分离 + 子上下文丢弃”
  - 当前实现禁止子 agent 再次生成 `task`，避免递归派发

---

## s05 - Skill Loading

- 状态：`passed`
- 日期：`2026-04-23`
- 实现文件：`agents/s05_skill_loading.py`
- 配套技能：
  - `skills/agent-builder/SKILL.md`
  - `skills/code-review/SKILL.md`
- 测试目标：
  - 验证 skill 能按需加载
  - 验证知识通过工具注入，而非预塞进 system prompt
  - 验证未请求 skill 时不会提前加载
- 测试命令：

```bash
printf 'What skills are available?\nq\n' | python3 agents/s05_skill_loading.py
```

```bash
printf 'I need to do a code review - load the relevant skill first\nq\n' | python3 agents/s05_skill_loading.py
```

```bash
python3 -m py_compile agents/s05_skill_loading.py tests/test_agents_smoke.py
```

```bash
python3 -m pytest tests/test_agents_smoke.py -q
```

- 关键观察：
  - 脚本成功启动，并在 system prompt 中暴露了本地 skills 的名称与描述
  - 询问 `What skills are available?` 时，模型直接回答可用 skills 列表，没有提前调用 `load_skill`
  - 询问 `I need to do a code review - load the relevant skill first` 时，模型明确调用了 `load_skill`
  - `load_skill` 的工具结果以 `<skill name="code-review"> ... </skill>` 形式注入，证明完整知识是按需加载而不是常驻 system prompt
  - 新增 `agents/s05_skill_loading.py` 后，编译检查与 `tests/test_agents_smoke.py` 都通过
- 结果：`passed`
- 备注：
  - 当前第 5 课使用本地示例 skills：`agent-builder`、`code-review`
  - 这套实现延续了前几课的基础文件工具，并新增 `SkillLoader` 负责扫描 `skills/*/SKILL.md`

---

## s06 - Context Compact

- 状态：`passed`
- 日期：`2026-04-24`
- 实现文件：`agents/s06_context_compact.py`
- 配套测试：
  - `tests/test_s06_context_compact.py`
- 测试目标：
  - 验证长上下文会触发压缩
  - 验证关键任务信息在压缩后仍被保留
  - 验证对话仍可持续推进
- 测试命令：

```bash
python3 -m py_compile agents/s06_context_compact.py tests/test_agents_smoke.py tests/test_s06_context_compact.py
```

```bash
python3 -m pytest tests/test_agents_smoke.py tests/test_s06_context_compact.py -q
```

```bash
python3 agents/s06_context_compact.py <<'EOF'
Before doing anything else, use the compact tool with focus "current task and important files", then tell me what happened.
q
EOF
```

```bash
python3 -u - <<'PY'
from agents import s06_context_compact as s06
s06.THRESHOLD = 100
history = [{"role": "user", "content": "Please keep working after auto compaction and confirm that compression happened. " + ("context " * 80)}]
s06.agent_loop(history)
print(history[-1]["content"])
PY
```

- 关键观察：
  - 脚本新增了三层压缩：`micro_compact()`、`auto_compact()`、`compact` 工具
  - 本地单元测试验证了两件事：旧的冗长 `tool_result` 会被替换成占位符，而 `read_file` 的历史结果会被保留；`auto_compact()` 会落盘 transcript 并返回摘要消息
  - 手动测试时，模型先调用了 `compact`，随后脚本打印 `[manual compact]`、保存 `.transcripts/transcript_*.jsonl`，并在压缩后继续工作和回答
  - 自动压缩测试中，为了在 `2026-04-24` 这次验证里稳定触发，临时将 `THRESHOLD` 调低到 `100`；日志中可以看到 `[auto_compact triggered]`、`[transcript saved: ...]` 与 `[auto_compact] conversation replaced with summary`
  - `.transcripts/` 目录已实际生成 transcript 文件，说明完整历史被移出活跃上下文而不是直接丢弃
- 结果：`passed`
- 备注：
  - `agents/s06_context_compact.py` 比参考实现多做了一点体验优化：手动压缩后不会直接结束当前轮，而是允许模型在压缩后继续回答
  - 当前实现保留最近 `3` 个 tool results，并默认保留 `read_file` 结果，避免压缩后立刻反复重读文件

---

## s07 - Task System

- 状态：`passed`
- 日期：`2026-04-24`
- 实现文件：`agents/s07_task_system.py`
- 当前定位：`终端可观测版`任务系统，重点验证 agent 在终端里可见地完成 `create_task -> list_tasks -> get_task -> update_task_status -> list_tasks` 的全过程
- 配套测试：
  - `tests/test_s07_task_system.py`
- 测试目标：
  - 验证任务会落盘到 `.tasks/`
  - 验证终端 trace 能展示任务创建前后、查看详情、更新状态后的变化
  - 验证旧任务快照与当前实现可兼容读取，避免课堂演示时被历史 `.tasks/*.json` 干扰
- 测试命令：

```bash
python3 -m py_compile agents/s07_task_system.py tests/test_agents_smoke.py tests/test_s07_task_system.py
```

```bash
python3 -m pytest tests/test_agents_smoke.py tests/test_s07_task_system.py -q
```

```bash
python3 agents/s07_task_system.py <<'EOF'
Create one task titled "s07-visual-trace-20260424". Then list all tasks, get the details of the task you just created, update it to in_progress, and list tasks again so I can observe the change.
q
EOF
```

```bash
python3 agents/s07_task_system.py --demo
```

- 关键观察：
  - 当前课堂演示应以 `create_task`、`list_tasks`、`get_task`、`update_task_status` 这 4 个工具名为准，不再混用旧版 `task_create` / `task_update` 命名
  - 每次工具调用前后，终端都会打印 `[tasks:before]` / `[tasks:after]` 快照；每轮模型请求会打印 `[agent] round N: requesting model`；工具返回值会打印 `[tool_result] ...`
  - 真实交互中，可以直接在终端看到同一个任务从 `pending` 变成 `in_progress`，并且 `list_tasks` 与 `get_task` 返回结果会与磁盘上的 `.tasks/<uuid>.json` 同步
  - 当前实现会兼容读取历史任务文件中的 `subject` / `blockedBy` 字段，避免仓库里旧的 lesson 7 数据导致演示中断
  - 已补充 `-d` / `--demo` 一键演示入口；真实回归中，`python3 agents/s07_task_system.py --demo` 会自动串起 `create -> list -> get -> update -> list`，并按顺序打印 `[demo] step 1/5 ...` 到 `[demo] step 5/5 ...`
  - 这次 `--demo` 真实测试里，演示任务标题形如 `s07-demo-ade95ddc`，其状态会从 `pending` 变成 `in_progress`；测试结束后临时生成的 demo 任务文件已清理，未污染现有 `.tasks/`
- 结果：`passed`
- 备注：
  - 本次文档已统一到“终端可观测版”描述，不再使用旧版 `TaskManager/task_create/task_update` 叙述
  - `--demo` 模式适合课堂演示固定生命周期；默认不带参数时仍进入交互式 REPL，便于继续做自由实验
  - `-d` 是 `--demo` 的短参数别名，这一点已由单元测试覆盖

---

## s08 - Background Tasks

- 状态：`passed`
- 日期：`2026-04-24`
- 实现文件：`agents/s08_background_tasks.py`
- 配套测试：
  - `tests/test_s08_background_tasks.py`
- 测试目标：
  - 验证长耗时操作可放后台
  - 验证前台 agent 不被阻塞
  - 验证后台完成后能收到结果通知
- 测试命令：

```bash
python3 -m py_compile agents/s08_background_tasks.py tests/test_agents_smoke.py tests/test_s08_background_tasks.py
```

```bash
python3 -m pytest tests/test_agents_smoke.py tests/test_s08_background_tasks.py -q
```

```bash
python3 agents/s08_background_tasks.py <<'EOF'
Run "python3 -c \"import time; time.sleep(2); print('done')\"" in the background, then create a file called bg_demo.txt with the content hello while it runs, and then check background task status.
q
EOF
```

```bash
{
  printf 'Start a background task running "python3 -c \"import time; time.sleep(1); print(\\\"notify-ok\\\")\"". Do not poll it yet; just confirm that it was started.\n'
  sleep 3
  printf 'What background updates have arrived?\n'
  printf 'q\n'
} | python3 agents/s08_background_tasks.py
```

- 关键观察：
  - 新增 `BackgroundManager`，通过后台线程包装 `subprocess.run(...)`，并为每个任务维护 `running/completed/error/timeout` 状态
  - 单元测试验证了两件事：`background_run` 启动的任务会在完成后进入通知队列；`inject_background_results()` 会把完成通知以 `<background-results>` 的形式追加到下一轮 `messages`
  - 第一轮真实交互中，模型先调用 `background_run` 启动一个 `sleep(2)` 的 Python 命令，再继续调用 `write_file` 创建 `bg_demo.txt`，最后用 `check_background` 看到任务已完成并返回 `done`，说明前台并没有被后台任务阻塞
  - 第二轮真实交互中，第一条消息只启动后台任务而不主动轮询；等待 3 秒后发送第二条消息，模型直接根据注入的后台结果回答 `notify-ok` 已完成，证明“后台完成通知 -> 下一轮自动注入”这条链路已跑通
  - 本课实现同时复用了 `s02` 的文件工具，并对阻塞式 `bash` 与 `background_run` 共用危险命令拦截逻辑，避免后台执行绕过限制
- 结果：`passed`
- 备注：
  - 为了避免像第 6 课那样留下不可控后台残留，本课额外提供了 `join_all()`，便于测试里等待后台线程结束
  - 真实测试中临时创建的 `bg_demo.txt` 已在验证后清理

---

## s09 - Agent Teams

- 状态：`passed`
- 日期：`2026-04-24`
- 实现文件：`agents/s09_agent_teams.py`
- 配套测试：
  - `tests/test_s09_agent_teams.py`
- 测试目标：
  - 验证多个 agent 可并行处理任务
  - 验证 JSONL 邮箱式消息传递有效
  - 验证 team roster 状态可持久化
- 测试命令：

```bash
python3 -m py_compile agents/s09_agent_teams.py tests/test_agents_smoke.py tests/test_s09_agent_teams.py
```

```bash
python3 -m pytest tests/test_agents_smoke.py tests/test_s09_agent_teams.py -q
```

```bash
{
  printf 'Spawn a teammate named bob with role notifier. Give bob one job only: send lead the message "hello-from-bob" and then finish. After that, tell me what you delegated.\n'
  sleep 6
  printf 'What updates have arrived from teammates?\n'
  printf 'q\n'
} | python3 agents/s09_agent_teams.py
```

- 关键观察：
  - 第 9 课不再是一次性 subagent，而是把 teammate 持久化到 `.team/config.json`，并把每个成员状态维护为 `working/idle`
  - 新增 `MessageBus`，通过 `.team/inbox/*.jsonl` 实现 append-only 邮箱；`send_message()` 只追加消息，`drain()` 读取后清空，形成最小“收件箱”机制
  - `TeammateManager.spawn()` 会登记成员、保存 team roster，并在线程中启动独立 teammate loop；队友完成后再把状态切回 `idle`
  - 单元测试验证了 4 条核心链路：邮箱 send/drain、`spawn()` 后 config 持久化、下一轮前 inbox 注入、非 lead 不能越权读取别人 inbox
  - 新增一个更真实的 3 人协作写作案例测试：`alice(researcher)` 先产出要点并发给 `bob(writer)`，`bob` 基于 inbox 草拟 release note 后转给 `carol(editor)`，最后由 `carol` 润色并回传给 `lead`，再由 lead 落盘成 `team_release_note.md`
  - 真实交互中，lead 成功调用 `spawn_teammate` 拉起 `bob`；随后 `bob` 在自己的 agent loop 中调用 `send_message` 给 lead 发消息，并在下一轮通过 `end_turn` 完成任务
  - 第二条用户消息进入时，终端先打印 `[lead] inbox 1 message(s)`，说明后台 teammate 的回信已经自动注入 lead 上下文；随后模型基于这条新消息汇总了 bob 的完成状态
- 结果：`passed`
- 备注：
  - 这节课先实现“持久身份 + 邮箱通信 + 线程化 teammate loop”，消息协议的结构化约束留到第 10 课继续细化
  - 为避免验证过程污染后续学习，测试后已将 `.team/` 恢复到仅保留 `lead` 的初始状态

---

## s10 - Team Protocols

- 状态：`passed`
- 日期：`2026-04-24`
- 实现文件：`agents/s10_team_protocols.py`
- 配套测试：
  - `tests/test_s10_team_protocols.py`
- 测试目标：
  - 验证团队请求/响应协议生效
  - 验证消息格式统一且可追踪
  - 验证 lead 与 teammate 可围绕 `request_id` 完成闭环协作
- 测试命令：

```bash
python3 -m py_compile agents/s10_team_protocols.py tests/test_s10_team_protocols.py tests/test_agents_smoke.py
```

```bash
python3 -m pytest tests/test_s10_team_protocols.py tests/test_agents_smoke.py -q
```

- 关键观察：
  - 第 10 课是在第 9 课 `agent teams` 的基础上继续演进：保留 `.team/inbox/*.jsonl` 邮箱通信，同时新增 `ProtocolRegistry` 来跟踪带 `request_id` 的结构化请求
  - 当前实现落地了两套最小协议状态机：`shutdown_request -> shutdown_response` 与 `plan_approval_request -> plan_approval_response`，状态统一为 `pending -> approved/rejected`
  - lead 侧新增了 `shutdown_request`、`check_shutdown`、`review_plan`、`check_plan` 等工具；teammate 侧新增 `submit_plan` 与 `shutdown_response`，职责边界更清晰
  - 这次测试中最关键的修复点是：teammate 在完成初始任务后不能立刻退出线程，而要继续存活并轮询 inbox，这样 lead 后续发起的 graceful shutdown 才能真正被接收和响应
  - 单元测试覆盖了协议注册表生命周期、消息投递、inbox 注入，以及一个完整 mocked 协作流：`lead spawn bob -> bob submit_plan -> lead approve -> bob report completion -> lead request shutdown -> bob approve shutdown`
  - 当前回归结果为 `9 passed`，说明第 10 课的核心协议闭环已经跑通
- 结果：`passed`
- 备注：
  - 本课重点不再是“能否发消息”，而是“消息是否具备可追踪、可确认、可拒绝的协议语义”
  - 如果要做真人交互演示，可继续运行 `python3 agents/s10_team_protocols.py`，然后在终端里结合 `/team`、`/inbox`、`/protocols` 观察团队状态与协议状态

---

## s11 - Autonomous Agents

- 状态：`passed`
- 日期：`2026-04-27`
- 实现文件：`agents/s11_autonomous_agents.py`
- 配套测试：
  - `tests/test_s11_autonomous_agents.py`
- 测试目标：
  - 验证 agent 会自行扫描任务池
  - 验证 agent 能自主认领任务
  - 验证 idle 阶段会在 inbox 与 task board 之间切换
  - 验证上下文变短后能做身份重注入
- 测试命令：

```bash
python3 -m py_compile agents/s11_autonomous_agents.py tests/test_s11_autonomous_agents.py tests/test_agents_smoke.py
```

```bash
python3 -m pytest tests/test_s11_autonomous_agents.py tests/test_agents_smoke.py -q
```

- 关键观察：
  - 第 11 课是在第 10 课的 team protocols 之上继续演进：teammate 不再只等 lead 指派，而是会在 `idle` 阶段主动轮询 inbox 和 `.tasks/` 任务看板
  - 新增 `TaskBoard`，把任务扫描、依赖判断、认领写回等逻辑集中起来；当前会同时兼容 `dependencies` 与旧字段 `blockedBy`
  - 自治 teammate 的生命周期现在分成 `WORK -> IDLE -> WORK/SHUTDOWN`，其中 idle 阶段会优先看 inbox，再尝试 auto-claim 第一个 ready task
  - 本地实现额外加入了 `reinject_identity_if_needed()`：当上下文被压缩到很短时，会在消息前部补回 `<identity>` 块，避免 agent 忘记自己是谁
  - 单元测试覆盖了 5 条关键链路：ready task 扫描、任务认领、身份重注入、idle 自动认领、以及一个完整 mocked flow：`bob idle -> auto-claim task -> send status -> mark completed -> idle timeout -> shutdown`
  - 当前回归结果为 `8 passed`，说明第 11 课最核心的“自治找活干”闭环已经跑通
- 结果：`passed`
- 备注：
  - 这节课为了更适合终端实操，lead 侧额外补了 `create_task` / `list_tasks` / `get_task` / `update_task_status`，这样可以直接在 `s11` 脚本里创建任务并观察队友自动认领
  - 交互式验证时可使用 `python3 agents/s11_autonomous_agents.py`，再结合 `/tasks`、`/team`、`/protocols` 观察任务与成员状态变化
  - 额外修复了脚本直接启动兼容性：现在 `python3 agents/s11_autonomous_agents.py` 不再因 `ModuleNotFoundError: No module named 'agents'` 失败，并移除了本机 `readline` 不兼容绑定带来的误报警告

---

## s12 - Worktree + Task Isolation

- 状态：`passed`
- 日期：`2026-04-27`
- 实现文件：`agents/s12_worktree_task_isolation.py`
- 配套测试：
  - `tests/test_s12_worktree_task_isolation.py`
- 测试目标：
  - 验证任务与 worktree 绑定
  - 验证不同任务在独立目录执行
  - 验证 worktree 生命周期索引与事件日志
  - 验证移除 worktree 时可同步完成绑定任务
- 测试命令：

```bash
python3 -m py_compile agents/s12_worktree_task_isolation.py tests/test_s12_worktree_task_isolation.py tests/test_agents_smoke.py
```

```bash
python3 -m pytest tests/test_s12_worktree_task_isolation.py tests/test_agents_smoke.py -q
```

- 关键观察：
  - 第 12 课把第 11 课的 task board 再往前推进一步：任务负责“做什么”，worktree 负责“在哪里做”，两者通过 `task_id <-> worktree name` 显式绑定
  - 本地实现沿用了上游的双平面设计：控制面是 `.tasks/task_*.json`，执行面是 `.worktrees/<name>/` + `.worktrees/index.json`
  - `TaskManager.bind_worktree()` 会在绑定时自动把 `pending` 推进到 `in_progress`，避免“任务已分配目录但状态还停留在待办”这种不一致
  - `WorktreeManager.remove(..., complete_task=True)` 会同时完成 3 件事：删目录、把任务标记为 `completed`、清空任务上的 `worktree` 绑定，并写入 `task.completed`
  - `.worktrees/events.jsonl` 把 `worktree.create.before/after`、`worktree.remove.before/after`、`worktree.keep`、`task.completed` 都显式落盘，方便回放与排错
  - 本次回归使用真实临时 git 仓库做测试，不是纯 mock；验证了 worktree 创建、隔离目录写文件、keep、remove、task completion 的完整链路
- 结果：`passed`
- 备注：
  - 当前 `python3 agents/s12_worktree_task_isolation.py` 已可直接启动，并会自动检测当前目录是不是 git repo
  - 当前测试结果为 `7 passed`，其中包含 `tests/test_agents_smoke.py` 的脚本编译回归
