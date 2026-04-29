# 第 12 课讲解：Worktree + Task Isolation 是如何实现的

这节课要解决的问题是：当第 11 课已经让 autonomous teammate 会自己找任务、认领任务、完成任务之后，为什么真实工程里还是不够安全？

答案是：因为第 11 课解决的是“任务调度”，但还没有彻底解决“执行隔离”。

如果两个 agent 同时在同一个工作目录里做事，即使它们认领的是不同任务，仍然可能发生这些问题：

- 同时修改同一个文件，未提交改动互相污染
- 一个任务产生的临时文件干扰另一个任务
- 想保留某个任务的现场继续调试，却发现目录已经被别的任务继续改乱了
- 任务虽然在任务板上分开了，但实际执行空间仍然混在一起

所以 `s12` 做的事情，可以概括成一句话：

- 把“任务隔离”从逻辑层推进到目录层，让每个任务拥有独立 worktree 执行空间

对应实现文件：`agents/s12_worktree_task_isolation.py`

---

## 一、先理解这节课的目标

第 12 课不是在扩展更多 agent 行为，而是在给第 11 课补上真正的工程隔离边界。

这节课要达到的目标有 5 个：

1. 任务板继续负责“做什么”
2. git worktree 负责“在哪里做”
3. 一个任务可以显式绑定一个 worktree
4. 命令执行时可以切到该 worktree 目录里跑
5. 收尾时可以明确选择 keep 还是 remove，并把生命周期写入事件日志

所以这节课最核心的能力不是更强的智能，而是：

- 更安全的并行执行
- 更明确的任务到目录映射
- 更可恢复的执行现场
- 更可观察的生命周期记录

这是 Harness Engineering for Real Agents 很关键的一步，因为真正的多 agent 系统不能只会“分任务”，还必须会“分工作空间”。

---

## 二、为什么第 11 课的 Autonomous Agents 还不够

第 11 课已经实现了：

- 任务持久化到 `.tasks/`
- teammate 空闲时自动扫描 ready task
- 任务会从 `pending -> in_progress -> completed`
- agent 可以在 `WORK -> IDLE -> WORK/SHUTDOWN` 之间切换

这些能力已经让系统看起来很像一个真实团队了。但它还有一个隐患：

- 所有任务默认共享同一个 repo 目录

这意味着第 11 课的隔离主要还是“逻辑隔离”：

- 任务 ID 不同
- owner 不同
- 状态不同

但物理执行环境仍然是同一个目录。

举个例子。

任务 A 负责重构认证模块，任务 B 负责重构登录页。即使任务板已经把它们分成两个任务，只要 A 和 B 都在当前仓库根目录里改代码，仍然会遇到：

- `git status` 混在一起
- 一个任务的未提交改动影响另一个任务测试结果
- 想只回滚其中一个任务会非常麻烦

所以第 12 课解决的，不是“任务再分细一点”，而是：

- 给每个任务分配独立执行目录

这就是 worktree isolation 的价值。

---

## 三、这节课的核心设计：Control Plane + Execution Plane

第 12 课最值得学的抽象，是把系统分成两个平面：

- Control Plane：任务控制面
- Execution Plane：执行隔离面

任务控制面继续使用 `.tasks/task_*.json`，负责记录：

- 任务 ID
- 任务目标
- 当前状态
- owner
- 绑定的 worktree 名称

执行隔离面新增 `.worktrees/`，负责记录：

- worktree 实际目录
- git branch
- worktree 当前生命周期状态
- 它绑定的是哪个任务
- 它经历了哪些 create / keep / remove 事件

你可以把它记成一句话：

- 任务负责“为什么做、做到了哪一步”
- worktree 负责“在哪儿做、目录现场现在是什么状态”

这是第 12 课比前面几课更工程化的地方：

- 控制信息和执行信息开始明确分层

---

## 运行原理架构图

下面这张纯 ASCII 图，描述的是 `s12` 的主要组件和数据流：

```text
+------------------+
| 用户 / 终端输入   |
+------------------+
          |
          v
+------------------+
| main CLI loop    |
+------------------+
          |
          v
+------------------+
| agent_loop       |
+------------------+
          |
          v
+------------------+
| TOOL_HANDLERS    |
+------------------+
    |        |        |
    |        |        +------------------------------+
    |        |                                       |
    |        +-------------------+                   |
    |                            |                   |
    v                            v                   v
+------------------+   +------------------+   +------------------+
| TaskManager      |   | WorktreeManager  |   | EventBus         |
| .tasks/task_*.json|  | .worktrees/      |   | events.jsonl     |
+------------------+   | index.json       |   +------------------+
    |                  +------------------+            ^
    |                           |                      |
    | bind_worktree             | emit lifecycle       |
    | update status             | events               |
    |                           |                      |
    |                           v                      |
    |                 +------------------------+       |
    |                 | git worktree add/remove| ------+
    |                 +------------------------+
    |                           |
    |                           v
    |                 +------------------------+
    |                 | .worktrees/<name>/     |
    |                 | isolated repo lane     |
    |                 +------------------------+
    |                           |
    +---------------------------+---- worktree_run(command)
```

如果把它再简化成一条主线，就是：

```text
task_create
-> worktree_create(task_id=...)
-> TaskManager 记录 worktree 绑定
-> WorktreeManager 在 .worktrees/<name>/ 创建独立 git worktree
-> worktree_run 在该目录中执行命令
-> worktree_keep 或 worktree_remove 收尾
-> EventBus 把整个生命周期写入 events.jsonl
```

---

## 四、`detect_repo_root()`：为什么这节课要先探测 repo 根目录

第 12 课一开始先做了一件非常务实的事情：通过 `detect_repo_root()` 判断当前目录是不是处在 git 仓库里，对应 `agents/s12_worktree_task_isolation.py:41`。

原因很简单：

- worktree 是 git 提供的能力
- 如果当前目录不是 git repo，后面的 worktree 工具就不成立

函数会尝试执行：

```text
git rev-parse --show-toplevel
```

如果成功，就把 repo 根目录作为 `REPO_ROOT`；否则退回当前目录 `WORKDIR`。

这一步的意义在于：

1. 让脚本在真实仓库里工作时，所有 `.tasks/` 和 `.worktrees/` 都相对 repo root 落盘
2. 即使不在 git repo，也能让脚本启动，只是 worktree 工具会提示不可用

这是一种很典型的 harness 设计：

- 尽量让系统能启动
- 但把依赖条件和能力边界讲清楚

所以第 12 课并不是盲目假设“环境永远正确”，而是先做能力探测，再决定哪些工具真正可用。

---

## 五、`EventBus`：这节课新增的生命周期日志层

`EventBus` 定义在 `agents/s12_worktree_task_isolation.py:72`。

它解决的问题不是“任务如何执行”，而是：

- 任务与 worktree 的生命周期如何被观察、回放和排错

它会把事件 append 到：

- `.worktrees/events.jsonl`

每条事件都至少包含：

- `event`
- `ts`
- `task`
- `worktree`
- 可选的 `error`

常见事件包括：

- `worktree.create.before`
- `worktree.create.after`
- `worktree.create.failed`
- `worktree.remove.before`
- `worktree.remove.after`
- `worktree.remove.failed`
- `worktree.keep`
- `task.completed`

这一步非常重要，因为到第 12 课，系统已经不只是“有没有结果”，而开始关心：

- 中间经历了哪些状态变化
- 失败发生在哪一步
- 任务和 worktree 是否同步完成了状态迁移

也就是说，EventBus 把原本隐性的目录生命周期，显式变成了一个可读事件流。

---

## 六、`TaskManager`：任务板在这一课里发生了什么变化

`TaskManager` 定义在 `agents/s12_worktree_task_isolation.py:112`。

和前一课相比，它的核心变化不是任务状态机本身，而是新增了 worktree 绑定能力。

### 1）任务结构新增 `worktree`

通过 `create()` 创建的任务，定义在 `agents/s12_worktree_task_isolation.py:144`，除了常见字段之外，还会额外保存：

- `worktree`

初始值是空字符串。

也就是说，第 12 课从任务创建开始，就已经预留了“这个任务未来要在哪个执行空间里做”的位置。

### 2）`bind_worktree()` 的意义

`bind_worktree()` 定义在 `agents/s12_worktree_task_isolation.py:181`。

它做了三件关键事：

1. 把任务的 `worktree` 字段写成指定名称
2. 如果传了 `owner`，也顺带写入 owner
3. 如果任务原本还是 `pending`，自动推进为 `in_progress`

这一点非常关键，因为它保证了：

- “任务已经拿到执行目录”
- 和 “任务已经真正开始执行”

这两个状态是同步的。

否则就会出现一种很别扭的不一致：

- 目录已经创建好了
- 任务实际上已经开工了
- 但任务板仍然显示 `pending`

第 12 课把这种一致性约束收进了 harness 本身。

---

## 七、`WorktreeManager`：这节课真正的隔离核心

`WorktreeManager` 定义在 `agents/s12_worktree_task_isolation.py:223`。

它负责的是第 12 课最核心的新能力：

- 创建、登记、执行、保留、移除 git worktree

你可以把它理解成一个最小的“隔离目录调度器”。

### 它管理哪些持久状态

`WorktreeManager` 内部维护：

- `.worktrees/`：所有隔离目录的根目录
- `.worktrees/index.json`：worktree registry
- git 本身的 worktree 元信息
- 配套事件日志 `events.jsonl`

其中 `index.json` 是非常关键的一层，因为它把执行空间本身也做成了 harness 可见状态，而不是完全依赖 `git worktree list` 的即时输出。

这意味着：

- 你不只是知道“目录存在”
- 还知道它在 harness 语义里是 `active`、`kept` 还是 `removed`

---

## 八、`create()`：worktree 是怎么创建并绑定到任务的

`WorktreeManager.create()` 定义在 `agents/s12_worktree_task_isolation.py:290`。

它的完整流程非常值得学习：

1. 校验 worktree 名称格式
2. 检查 index 里有没有重名 worktree
3. 如果传了 `task_id`，确认任务真实存在
4. 先发出 `worktree.create.before`
5. 调用 git：

```text
git worktree add -b wt/<name> .worktrees/<name> HEAD
```

6. 在 `index.json` 中登记：
   - `name`
   - `path`
   - `branch`
   - `task_id`
   - `status=active`
7. 如果绑定了任务，则调用 `TaskManager.bind_worktree()`
8. 发出 `worktree.create.after`
9. 如果中间失败，则发出 `worktree.create.failed`

这里最值得学的不是 `git worktree add` 命令本身，而是这整套“执行动作 + 状态写回 + 事件落盘”的闭环。

也就是说，第 12 课不是单纯执行一条 git 命令，而是在把 git worktree 收编进 harness 自己的状态系统。

---

## 九、`run()`：为什么 worktree 里的命令才是真正隔离执行

`WorktreeManager.run()` 定义在 `agents/s12_worktree_task_isolation.py:373`。

它和前面课程里的普通 `bash` 最大区别在于：

- `cwd` 不再是仓库根目录
- 而是某个命名 worktree 的目录

也就是：

```python
subprocess.run(command, shell=True, cwd=worktree_path, ...)
```

这一步的工程意义非常大。

因为从这一刻开始：

- agent 执行的不是“共享工作区里的命令”
- 而是“任务专属隔离目录里的命令”

这意味着：

- 一个 worktree 里的临时文件不会跑到主 repo 根目录
- 每个任务都能有自己独立的 `git status`
- 并行实验时，未提交改动不再天然互相污染

这就是为什么这节课的测试里专门验证了：

- 在 worktree 目录里生成 `lane.txt`
- 主 repo 根目录里并不会出现这个文件

这说明隔离不只是逻辑上的，而是真的发生在文件系统层面。

---

## 十、`keep()` 与 `remove()`：为什么收尾也要显式建模

worktree 并不是做完就一定删掉。

有些任务你会想：

- 先保留现场，后续还要继续调试
- 暂时不合并，但希望目录还在

这就是 `keep()` 的意义，对应 `agents/s12_worktree_task_isolation.py:453`。

它不会删除目录，而是：

- 把该 worktree 在 `index.json` 里的状态改成 `kept`
- 发出 `worktree.keep` 事件

而 `remove()` 则定义在 `agents/s12_worktree_task_isolation.py:399`，它的职责更重：

1. 发出 `worktree.remove.before`
2. 执行 `git worktree remove`
3. 如果 `complete_task=True` 且该 worktree 绑定了任务：
   - 把任务状态更新为 `completed`
   - 清空任务上的 `worktree` 字段
   - 发出 `task.completed`
4. 把 index 里的 worktree 状态改成 `removed`
5. 发出 `worktree.remove.after`
6. 如果失败，发出 `worktree.remove.failed`

这一段非常有代表性，因为它体现了第 12 课的一条核心工程原则：

- 收尾不是“顺手删个目录”
- 而是一组需要保持一致性的生命周期状态迁移

---

## 十一、这节课的状态机是什么

如果把第 12 课抽象成状态机，它至少包含两条并行状态线：

### 1）任务状态机

```text
pending -> in_progress -> completed
```

### 2）worktree 状态机

```text
absent -> active -> kept
absent -> active -> removed
```

这两条状态机之间通过绑定关系相连：

- 当 worktree 创建并绑定任务时，任务会从 `pending` 推到 `in_progress`
- 当 worktree remove 且 `complete_task=True` 时，任务会推进到 `completed`

所以第 12 课的本质不是新增一条孤立功能，而是把：

- 任务生命周期
- 目录生命周期

通过显式状态同步串起来。

这就是 “Task Isolation” 真正的含义。

---

## 十二、CLI 和工具层是怎么把这些能力暴露给模型的

第 12 课的工具注册定义在 `agents/s12_worktree_task_isolation.py:555`。

和前几课一样，模型并不是天然知道该怎么用 worktree，而是 harness 把能力包装成一组清晰工具：

- `task_create`
- `task_list`
- `task_get`
- `task_update`
- `task_bind_worktree`
- `worktree_create`
- `worktree_list`
- `worktree_status`
- `worktree_run`
- `worktree_keep`
- `worktree_remove`
- `worktree_events`

再加上通用工具：

- `bash`
- `read_file`
- `write_file`
- `edit_file`

这样模型就能在一轮对话里完成一整条链路：

```text
create task
-> create worktree
-> run command inside worktree
-> inspect status
-> keep/remove worktree
-> inspect events
```

`agent_loop()` 定义在 `agents/s12_worktree_task_isolation.py:714`，`main()` 定义在 `agents/s12_worktree_task_isolation.py:748`。

它们延续了前几课的交互习惯：

- 用户在 `s12 >>` 下输入自然语言
- 模型选择合适工具
- harness 执行工具并把结果回灌给模型

所以第 12 课的创新不在 loop 形式，而在工具语义和状态层。

---

## 十三、如何在终端里观察这节课

这节课最适合直接跑交互式脚本：

```bash
python3 agents/s12_worktree_task_isolation.py
```

启动后建议你重点观察三类产物：

- `.tasks/task_*.json`
- `.worktrees/index.json`
- `.worktrees/events.jsonl`

### 一个最推荐的实验流程

1. 创建两个任务，例如 backend auth 和 frontend login
2. 给每个任务各创建一个 worktree
3. 在其中一个 worktree 里写文件或跑 `git status`
4. 检查主 repo 根目录是否未被该临时文件污染
5. 对一个 worktree 执行 `keep`
6. 对另一个 worktree 执行 `remove` 且 `complete_task=true`
7. 再检查：
   - task 状态是否变成 `completed`
   - task 上的 `worktree` 是否被解绑
   - index 状态是否变成 `removed`
   - events 里是否多出 `task.completed`

### 这节课你应该重点看什么

- 任务文件和 worktree registry 是否同步变化
- 隔离目录里生成的文件是否只存在于该 lane
- remove 和 keep 的语义是否清晰不同
- 事件日志是否完整记录了 create / keep / remove 的前后过程

这节课最重要的教学价值就在这里：

- 你不只是“相信隔离存在”
- 而是能直接看到磁盘状态和事件日志证明它存在

---

## 十四、第 12 课相对第 11 课到底提升了什么

如果要把第 11 课和第 12 课放在一起理解，我建议这样看：

- 第 11 课解决的是“谁来做下一件事”
- 第 12 课解决的是“这件事应该在哪个独立目录里做”

也就是说：

- `s11` 让 agent 学会自治调度
- `s12` 让自治调度拥有安全执行边界

没有第 11 课，系统不会自己找活干。

没有第 12 课，系统即使会找活干，也可能在共享目录里把多个任务搅在一起。

所以第 12 课不是可有可无的小增强，而是把多 agent harness 从“会并行”推进到“能安全并行”的关键一步。

---

## 十五、这节课你最应该记住的 6 个点

如果只带走最重要的内容，我建议记住下面 6 句话：

1. 第 11 课解决任务调度，第 12 课解决执行隔离
2. `.tasks/` 是控制面，`.worktrees/` 是执行面
3. task 和 worktree 必须通过显式绑定保持一致
4. `worktree_run()` 的关键不是跑命令，而是把 `cwd` 切进隔离目录
5. `keep` 和 `remove` 都是生命周期状态，不只是 convenience 操作
6. `events.jsonl` 让 worktree 生命周期变成可观察、可回放、可排错的事件流

---

## 十六、建议你结合代码重点阅读的位置

如果你准备边读边跑，我建议按下面顺序看源码：

1. `agents/s12_worktree_task_isolation.py:41` 的 `detect_repo_root()`
2. `agents/s12_worktree_task_isolation.py:72` 的 `EventBus`
3. `agents/s12_worktree_task_isolation.py:112` 的 `TaskManager`
4. `agents/s12_worktree_task_isolation.py:181` 的 `bind_worktree()`
5. `agents/s12_worktree_task_isolation.py:223` 的 `WorktreeManager`
6. `agents/s12_worktree_task_isolation.py:290` 的 `create()`
7. `agents/s12_worktree_task_isolation.py:373` 的 `run()`
8. `agents/s12_worktree_task_isolation.py:399` 的 `remove()`
9. `agents/s12_worktree_task_isolation.py:453` 的 `keep()`
10. `agents/s12_worktree_task_isolation.py:555` 的工具注册
11. `agents/s12_worktree_task_isolation.py:714` 的 `agent_loop()`
12. `agents/s12_worktree_task_isolation.py:748` 的 `main()`

这样读下来，你会非常清楚：

- repo root 是怎么确定的
- 任务和 worktree 是怎么建立绑定的
- worktree 是怎么真正被创建、执行、保留、删除的
- 生命周期日志是怎么落盘的

---

## 十七、小结

第 12 课解决的，不是“如何让模型更会做事”，而是“如何让多个任务在真实仓库里互不干扰地做事”。

它通过 4 个关键设计把这一点落地了：

- `TaskManager` 继续维护任务控制面
- `WorktreeManager` 提供独立执行目录
- `EventBus` 记录完整生命周期事件
- `task <-> worktree` 绑定把控制状态和执行状态串成闭环

如果说：

- 第 11 课是让 agent 开始“自己找活干”

那么第 12 课就是：

- 让 agent 开始“各干各的目录，互不干扰”

这就是 Worktree + Task Isolation 在这套课程里的真正工程含义。
