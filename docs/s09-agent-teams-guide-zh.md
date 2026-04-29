# 第 9 课讲解：Agent Teams 是如何实现的

这节课要解决的问题是：当一个 agent 已经会用工具、会列 Todo、会开 subagent、会跑后台任务之后，为什么还需要一个“团队”机制？

答案是：因为复杂任务往往不是“多开几个函数调用”就能解决，而是需要多个有身份、有状态、能互相发消息的 agent 持续协作。

`s09` 做的事情，不是把 `s04` 的 subagent 再包一层，而是第一次把“持久 teammate + 邮箱通信 + 团队名册”引入 harness。

对应实现文件：`agents/s09_agent_teams.py`

---

## 一、先理解这节课的目标

第 9 课的目标可以概括成一句话：

- 不再只让主 agent 单线程做所有事
- 而是让 lead 可以拉起多个持续存在的 teammate
- 每个 teammate 都有自己的身份、状态和上下文
- 队友之间通过邮箱异步通信

这和前几课相比，变化是非常大的。

在前几课里：

- `s04` 的 subagent 是一次性调用，做完就结束
- `s08` 的 background task 也是一次性后台执行，完成后只回一个结果

但真实的团队协作需要的是：

1. 有名字的成员
2. 能跨多轮存在
3. 能知道自己现在是 `working` 还是 `idle`
4. 能把消息发给 lead 或其他 teammate
5. 在下一轮继续基于新消息做事

这就是 Agent Teams 的最小模型。

---

## 二、为什么第 4 课的 subagent 不够

`s04` 很重要，因为它第一次让我们学会“把子问题拆给独立上下文”。但它本质上还是一次性 worker：

- 创建一个子 agent
- 给它一个任务
- 跑完
- 返回摘要
- 结束

这个模式适合：

- 一次性调研
- 一次性代码检查
- 一次性生成某段内容

但它不适合团队协作，因为它缺少三样东西：

1. 没有持久身份  
   下一次再创建时，它不是“上次那个 alice”，只是另一个临时 subagent。

2. 没有生命周期  
   它没有 `idle -> working -> idle` 这种长期状态。

3. 没有通信通道  
   它通常只把最终结果返回给主 agent，不能像队友那样中途发消息。

所以你可以把 `s04` 理解为：

- “一次性委派”

而 `s09` 是：

- “持续存在的队友”

这是两个层级的能力。

---

## 三、为什么第 8 课的 background task 也不够

`s08` 解决的是“耗时 shell 命令不要阻塞前台 agent”。

它的后台任务本质是：

- 一条命令
- 一个线程
- 跑完后回一个结果

这很适合：

- 跑测试
- 编译
- 长时间脚本
- 慢 I/O 命令

但 background task 不是 teammate，因为它没有“模型驱动的持续决策”。

后台任务不会：

- 自己看收件箱
- 自己决定下一步做什么
- 自己给别人发消息
- 维护自己的对话历史

所以 `s08` 是“后台执行器”，不是“后台 agent”。

第 9 课真正新增的是：

- 每个线程里跑的不是一个 shell 命令
- 而是一个完整的 agent loop

这就是质变点。

---

## 四、这一课的核心设计：持久 agent + 邮箱通信

第 9 课的架构可以概括成下面这条链路：

```text
lead 接到任务
-> 调用 spawn_teammate
-> TeammateManager 记录队友到 .team/config.json
-> 为该队友启动一个独立线程
-> 线程内运行完整 teammate agent loop
-> lead / teammate 通过 .team/inbox/*.jsonl 互发消息
-> 每轮调用模型前先收取 inbox 并注入上下文
-> 队友完成当前任务后回信给 lead，并把状态切回 idle
```

这里最值得注意的不是“线程”本身，而是三个概念第一次同时成立：

1. 持久身份
2. 团队状态
3. agent-to-agent 通信

这三个放在一起，才构成了最小版 agent team。

---

## 五、`MessageBus`：最小邮箱系统是怎么做的

邮箱系统定义在 `agents/s09_agent_teams.py:55` 的 `MessageBus`。

它使用的是非常朴素但非常实用的设计：

- 每个成员一个 JSONL 文件
- 文件路径在 `.team/inbox/<name>.jsonl`
- 发送消息时 append 一行 JSON
- 读取消息时一次性读出并清空

也就是典型的：

- append-only on send
- drain-on-read on receive

### `send()` 做了什么

`send()` 会写入一条结构化消息，包含：

- `type`
- `from`
- `to`
- `content`
- `timestamp`

还可以附加额外字段 `extra`。

这意味着虽然第 9 课还没有正式进入“团队协议”阶段，但消息本身已经开始具备结构化雏形了。

### `drain()` 做了什么

`drain(name)` 的语义不是“偷看”，而是“收件”：

1. 读取 `<name>.jsonl`
2. 解析每一行 JSON
3. 把文件清空
4. 返回本次收到的全部消息

这一步非常关键，因为它定义了邮箱语义：

- 已读即取走
- 下一轮不会重复收到同一批消息

这让消息流动很清晰，也方便测试。

---

## 六、为什么 JSONL 邮箱是一个好教学设计

这节课完全可以用内存队列来做消息传递，但实现选择了 JSONL 文件邮箱，原因很有教学价值：

- 可观察：你能直接看到 `.team/inbox/*.jsonl`
- 易调试：消息格式就是文本 JSON
- 易持久化：即使进程还在，邮箱本身已经落盘
- 易扩展：后面做协议、日志、回放都方便

它虽然不是高性能消息队列，但对教学版 harness 很合适，因为它把“消息系统”做到了看得见、摸得着。

在 Harness Engineering 里，这类设计很重要：

- 不是一开始就追求最强抽象
- 而是先做最容易理解、最容易验证的最小闭环

---

## 七、`TeammateManager`：团队名册和生命周期管理

`TeammateManager` 定义在 `agents/s09_agent_teams.py:136`。

它负责的不是模型推理，而是团队层面的“组织管理”。

它主要做四件事：

1. 维护 `.team/config.json`
2. 确保 `lead` 永远存在
3. 跟踪每个成员的 `role` 和 `status`
4. 管理每个 teammate 对应的线程

### `.team/config.json` 保存什么

当前配置里，每个成员至少包含：

- `name`
- `role`
- `status`
- `created_at`

在状态变更时，还会写入 `updated_at`。

这意味着第 9 课开始，团队已经不只是“运行时对象”，而是一个持久化实体。

### `ensure_member()` 的意义

`ensure_member()` 不只是“如果不存在就创建”，它还承担了统一收口的职责：

- 新成员第一次出现时登记进 roster
- 旧成员再次被引用时同步 role / status

这种写法可以避免“成员信息散落在各处随手改”的问题。

---

## 八、`spawn()`：为什么这次拉起的是完整 teammate，而不是一次性 worker

`spawn()` 在 `agents/s09_agent_teams.py:203`。

它的流程是：

1. 拒绝把 `lead` 当作队友名
2. 检查成员是否已经在 `working`
3. 把成员状态设置为 `working`
4. 把信息写入 `.team/config.json`
5. 启动线程，线程目标是 `_teammate_loop(...)`
6. 立即返回 `Spawned teammate ...`

这里最关键的是第 5 步：

- 线程函数不是 `subprocess.run(...)`
- 而是 `_teammate_loop(...)`

也就是说，spawn 出来的不是一个后台 shell job，而是一个真正会继续推理、继续用工具、继续发消息的 teammate。

这就是它和 `s08` 最根本的区别。

---

## 九、`_teammate_loop()`：每个队友其实都有自己的 agent loop

`_teammate_loop()` 在 `agents/s09_agent_teams.py:224`。

它内部先创建一个新的 `AgentState(name=name, role=role)`，然后把初始任务写成一条 `user` 消息：

- 你是谁
- 你的角色是什么
- 这次 assignment 是什么
- 需要时可以用 `send_message`

接着它调用的是同一个 `agent_loop(...)`，只是参数不同：

- system prompt 换成 teammate 版
- `can_spawn=False`
- `poll_inbox=True`

这说明 lesson 9 的一个重要工程思想：

- lead 和 teammate 并不是两套完全不同的框架
- 而是在同一个 loop 上，通过 system prompt 和工具权限做角色分化

这是一种非常干净的演进方式。

### 队友完成后会发生什么

如果 teammate loop 返回了结果，`_teammate_loop()` 会自动给 lead 发一条状态消息：

- `to="lead"`
- `msg_type="status"`
- `extra={"status": "completed"}`

然后无论成功或异常，最终都会把成员状态切回 `idle`。

所以队友的最小生命周期就是：

```text
spawn -> working -> send update/result -> idle
```

---

## 十、`agent_loop()` 这节课最关键的变化：inbox 注入

`agent_loop()` 在 `agents/s09_agent_teams.py:538`。

这一课里它最关键的新能力是：

- 每轮请求模型前，先检查当前 agent 的 inbox

对应逻辑是：

1. `bus.drain(state.name)` 收取本 agent 的消息
2. 如果有消息，打印 `[name] inbox N message(s)`
3. 调用 `state.inject_inbox(inbox_messages)`
4. 把消息包装成一条新的 `user` 消息插入上下文

注入后的形式大概是：

```text
<inbox>
[
  {
    "type": "status",
    "from": "bob",
    "to": "lead",
    "content": "finished assignment",
    "timestamp": 1234567890.0
  }
]
</inbox>
```

这个设计的妙处在于：

- 不需要修改模型 API
- 不需要发明新的消息角色
- 不需要真正的事件中断

harness 只是把“新收到的队友消息”变成当前 agent 下一轮推理前的新上下文事实。

这和第 8 课的 background result 注入是同一思路，但这次注入的不是命令结果，而是 agent 间通信。

---

## 十一、`AgentState` 为什么要按成员隔离

`AgentState` 在 `agents/s09_agent_teams.py:116`。

看起来它很简单，但在这一课里意义非常大，因为每个成员都有自己独立的：

- `name`
- `role`
- `messages`

这意味着：

- lead 的上下文不会被 teammate 的全部历史污染
- alice 和 bob 各自有自己的会话轨迹
- 每个 agent 只围绕自己的局部任务继续推理

这正是多 agent 架构最常见的收益：

- 用更多 agent，不是为了“更聪明”
- 而是为了把上下文拆开

所以第 9 课的“团队”不只是组织学概念，本质上也是上下文管理策略。

---

## 十二、lead 和 teammate 的差异，其实主要靠系统提示词和工具权限

这节课没有写一个庞大的角色系统，而是用两种很轻量的方法来区分 lead 和 teammate。

### 1）不同的 system prompt

文件开头定义了：

- `LEAD_SYSTEM`
- `TEAMMATE_SYSTEM`

lead 被提示去：

- `spawn_teammate`
- `send_message`
- `broadcast_message`
- `read_inbox`
- `list_team`

teammate 被提示去：

- 检查 inbox
- 做聚焦工作
- 必要时 `send_message`
- 完成后 `end_turn`

### 2）不同的工具权限

`build_tools(can_spawn=True)` 和 `build_tools(can_spawn=False)` 会生成不同工具集。

最重要的权限差异是：

- 只有 lead 能 `spawn_teammate`

此外，`read_inbox()` 在 handler 层还有二次限制：

- lead 可以读自己的 inbox，也可以指定其他 inbox
- 非 lead 只能读自己的 inbox

这说明角色治理不是只靠 prompt，也要靠代码里的硬权限边界。

---

## 十三、`/team` 和 `/inbox`：为什么 CLI 命令也很重要

`main()` 在 `agents/s09_agent_teams.py:621`。

这一课新增了两个很实用的交互命令：

- `/team`
- `/inbox`

### `/team`

直接输出当前 roster，也就是：

- 谁在团队里
- 每个人是什么角色
- 当前状态是 `working` 还是 `idle`

### `/inbox`

手动读取 lead 的收件箱。

这两个命令的重要性在于，它们让“团队状态”和“消息流”对人类操作者也可见。

教学版 harness 如果所有状态都只藏在内部，很难理解系统到底有没有工作。`/team` 和 `/inbox` 正好把这些内部结构暴露成了可观测接口。

---

## 十四、这节课的测试验证了什么

测试文件是 `tests/test_s09_agent_teams.py`。

它不是只测“能不能跑”，而是把第 9 课最核心的四条链路都锁住了。

### 1）邮箱 send / drain 正常工作

`tests/test_s09_agent_teams.py:35`

这个测试验证：

- `send()` 会把消息正确写入目标 inbox
- `drain()` 能读出消息
- 再次 `drain()` 会得到空列表

也就是验证“append-only + drain-on-read”语义成立。

### 2）`spawn()` 后成员配置会持久化

`tests/test_s09_agent_teams.py:46`

这个测试验证：

- 队友会被写入 `.team/config.json`
- `role` 会落盘
- 状态能从 `working` 回到 `idle`
- lead 能收到队友回信

这条链路说明团队不只是内存对象。

### 3）inbox 会在调用模型前被注入

`tests/test_s09_agent_teams.py:68`

这个测试直接 mock 掉模型调用，然后断言：

- `messages` 里确实出现了带 `<inbox>` 的 `user` 消息

这条测试很关键，因为它验证的是“消息最终进入模型上下文”，而不是仅仅存在于文件里。

### 4）非 lead 不能越权读别人 inbox

`tests/test_s09_agent_teams.py:158`

这个测试验证权限边界：

- 普通 teammate 不能随便读取其他成员 inbox

这防止了团队成员之间出现任意窥视。

---

## 十五、真实跑通时，终端里能看到什么

根据这节课已经记录的测试，真实运行时能看到一条很典型的链路：

1. lead 收到用户任务
2. 模型调用 `spawn_teammate`
3. 终端打印 `[team] spawning bob (notifier)`
4. bob 在线程里进入自己的 agent loop
5. bob 用 `send_message` 给 lead 发出 `"hello-from-bob"`
6. bob 结束自己的回合，状态回到 `idle`
7. 当 lead 下一轮再次推理前，终端打印 `[lead] inbox 1 message(s)`
8. 这条消息被注入 lead 的上下文，lead 基于它做汇总

所以 lesson 9 真正跑通时，你观察到的不是“两个线程都在忙”这么抽象的事情，而是：

- 团队成员被创建
- 消息被写入邮箱
- lead 在下一轮收到回信
- roster 状态随着执行发生变化

这就是可观测的团队协作闭环。

---

## 十六、为什么这节课还没有进入“协议化协作”

虽然第 9 课已经有结构化消息字段，但它还没有真正进入第 10 课那种“协议驱动协作”。

目前的消息更像：

- “我做完了”
- “请你看这个”
- “帮我处理某件事”

但还没有严格定义：

- 请求 ID
- 回复对应哪个请求
- ACK / reject / timeout
- 统一的状态字段约束

所以第 9 课的定位要非常清楚：

- 它解决的是“有队友、有邮箱、能发消息”
- 还没有解决“团队消息一定规范且可追踪”

换句话说：

- `s09` 建立通信通道
- `s10` 才开始建立通信协议

---

## 十七、这一课做了哪些简化

为了教学清晰，当前实现故意做了不少简化：

- 使用线程，不是独立进程或分布式 worker
- 邮箱是 JSONL 文件，不是消息中间件
- 队友生命周期只有最小版 `working/idle`
- 没有 supervisor、重试、超时恢复、故障转移
- 消息没有正式协议版本号或 request/response 约束

这些简化不是缺点，而是为了把核心思想突出出来：

- 多 agent 不是先从复杂编排开始
- 而是先从“身份、状态、通信”三件事做稳

---

## 十八、你应该记住的 5 个工程要点

如果只记这节课最重要的内容，建议记住下面 5 点：

1. `spawn_teammate` 拉起的是完整 agent loop，不是一次性 shell job
2. `MessageBus` 的本质是 append-only 邮箱 + drain-on-read 收件
3. `TeammateManager` 负责的是团队名册和生命周期，不是推理本身
4. 每个 agent 都有独立 `AgentState.messages`，团队协作同时也是上下文拆分
5. 队友消息必须在下一轮注入模型上下文，否则通信对模型来说不存在

---

## 十九、一句话总结

第 9 课实现的不是“多开几个 agent”这么简单，而是：

- 让 agent 拥有持久身份
- 让团队状态落盘
- 让成员之间通过邮箱异步通信
- 并让这些消息在下一轮真正进入各自的模型上下文

这就是一个最小但已经很像真实系统的 Agent Teams harness。
