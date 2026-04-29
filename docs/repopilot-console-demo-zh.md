# RepoPilot Console Demo 说明

`RepoPilot Console` 是基于前 12 课能力拼出来的第一个可视化毕业项目 demo。

它不是聊天窗口外面套一层壳，而是一个真正的工程控制台：

- 你可以在 UI 里提交一个 mission
- 后端会自动创建一次 run
- lead 会拆出 implementation / validation / docs 三条任务 lane
- 每条 lane 会创建独立 git worktree
- 后台任务会真实执行 `py_compile` 或 `unittest`
- 你可以直接在 UI 中查看 worktree 里的文件产物

对应文件：

- 后端入口：`agents/repopilot_console.py`
- 运行时内核：`agents/repopilot_runtime.py`
- 前端页面：`agents/repopilot_ui/index.html`
- 前端脚本：`agents/repopilot_ui/app.js`
- 前端样式：`agents/repopilot_ui/styles.css`

---

## 一、如何启动

确保已经安装依赖，然后在仓库根目录运行：

```bash
python3 agents/repopilot_console.py
```

默认会启动在：

```text
http://127.0.0.1:8765
```

如果你更喜欢显式命令，也可以用：

```bash
uvicorn agents.repopilot_console:app --host 127.0.0.1 --port 8765
```

---

## 二、页面结构

第一版控制台包含 6 个主要区域：

### 1. Mission Bar

页面顶部右侧输入框用于提交 mission，例如：

```text
Build a prototype task lane with Python artifacts, validation checks, and operator docs.
```

点击 `Start Run` 后，会启动一次新的 run。

### 2. Task Board

左侧展示任务板，按状态分栏：

- `pending`
- `in_progress`
- `completed`
- `blocked`

你可以看到任务如何被 agent 认领，以及最终完成情况。

### 3. Run Timeline

中间展示实时事件流，例如：

- `run.created`
- `task.created`
- `task.claimed`
- `worktree.create.after`
- `background.started`
- `background.finished`
- `protocol.review_request`
- `lane.failed`
- `run.failed`
- `run.completed`

这是最能体现 harness 运行过程的区域。

### 4. Agents

右上角显示各 agent 当前状态：

- `lead`
- `backend_dev`
- `qa_dev`
- `docs_dev`

你可以看到它们分别处于 planning、coding、testing、reviewing、complete 等阶段。

### 5. Worktrees

左下区域展示每个任务绑定的 worktree：

- worktree 名称
- task id
- 当前状态 `active / kept / removed`
- Keep / Remove 操作按钮

### 6. Artifact Viewer

右下区域会列出当前 run 的 worktree 文件，点击后可直接查看文件内容。

这部分是第一版 demo 的关键体验点：

- UI 不只是看日志
- 还能直接看任务实际产出的文件

---

## 三、这版 demo 的真实执行内容

当前第一版不是“假 UI”，而是会真实做这些事：

- 即使仓库还没有首个 commit（`HEAD` 还不存在），也能为每条 lane 创建 orphan worktree
- 如果某条 lane 失败，run 会明确进入 `failed`，而不是错误地显示 `completed`

### implementation lane

在独立 worktree 中生成：

- `repopilot_runs/<slug>/backend/__init__.py`
- `repopilot_runs/<slug>/backend/feature.py`

并后台执行：

```bash
python3 -m py_compile ...
```

### validation lane

在独立 worktree 中生成：

- `repopilot_runs/<slug>/qa/contract.py`
- `repopilot_runs/<slug>/qa/test_contract.py`

并后台执行：

```bash
python3 -m unittest discover ...
```

### docs lane

在独立 worktree 中生成：

- `repopilot_runs/<slug>/docs/README.md`
- `repopilot_runs/<slug>/docs/CHANGELOG.md`

### lead review lane

最终会生成：

- `.repopilot/summaries/<run_id>/MISSION.md`
- `.repopilot/summaries/<run_id>/RUN_SUMMARY.md`

---

## 四、与前 12 课的能力映射

这版控制台已经直接用到了这些课程思想：

- `s07`：任务持久化
- `s08`：后台任务
- `s09`：多 agent lane 概念
- `s10`：协议事件流
- `s11`：autonomous teammate 风格的任务认领
- `s12`：任务与 git worktree 绑定

所以它虽然还是第一版，但已经不再是 CLI lesson demo，而是一个可运行、可观察、可解释的毕业项目原型。

---

## 五、建议的演示方式

推荐你这样演示：

1. 打开控制台首页
2. 输入一个 mission
3. 点击 `Start Run`
4. 先看 `Task Board` 中任务被创建
5. 再看 `Run Timeline` 中 lane claim / worktree create / background job 变化
6. 最后在 `Artifact Viewer` 中点击文件，展示真实产物
7. 如果需要，再在 `Worktrees` 区域点击 `Keep` 或 `Remove`

这样能非常直观地展示：

- harness 不只是“能回答”
- 它还能“组织执行过程”

---

## 六、下一步最值得继续增强的方向

如果继续往下做，我建议优先增强这 4 件事：

1. 把当前 deterministic lane 逻辑替换成真实 LLM lead / teammate
2. 增加 diff viewer，而不只是 file viewer
3. 增加 run replay，支持回放某次任务执行过程
4. 增加人工审批点，让用户在 UI 中 approve / reject 某个 plan
