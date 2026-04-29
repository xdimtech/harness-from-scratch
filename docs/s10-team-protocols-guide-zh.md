# 第 10 课讲解：Team Protocols 是如何实现的

这节课要解决的问题是：当第 9 课已经有了 team lead、teammate、邮箱通信和团队名册之后，为什么还不够？

答案是：因为“能发消息”和“能协作”之间，还差一层协议。

如果没有协议，lead 和 teammate 虽然能互相发文本，但很多关键动作仍然是不可靠的：

- lead 想让 teammate 安全退出，但对方到底有没有看到、有没有收尾、是不是拒绝了，都不清楚
- teammate 想做高风险改动，但只是口头说一声，lead 很难明确批准或拒绝
- 一旦同类请求同时出现多次，没有唯一编号就很容易串线

所以 `s10` 做的事情，可以概括成一句话：

- 把自由文本团队沟通，升级成“带 request_id 的结构化 request-response 协议”

对应实现文件：`agents/s10_team_protocols.py`

---

## 一、先理解这节课的目标

第 10 课不是在发明一个复杂的多 agent 框架，而是在第 9 课的基础上补上最小协议层。

这节课要达到的目标有 3 个：

1. 同类协作动作必须可追踪  
   也就是每个请求都要能知道“是哪一次请求、现在是什么状态、是谁发的、谁处理的”。

2. 协作动作必须可确认  
   不能只靠“我发过一条消息”来判断；要明确区分 `pending`、`approved`、`rejected`。

3. lead 和 teammate 的职责要更清晰  
   lead 负责发起关机请求、审批计划；teammate 负责提交计划、响应关机请求。

这就是 Team Protocols 的本质：

- 不是增强模型智力
- 而是给 agent team 增加协作规矩

这也是 Harness Engineering 的典型思路：当自然语言协作开始不稳定时，就要把关键交互收敛成结构化协议。

---

## 二、为什么第 9 课的 Agent Teams 还不够

第 9 课已经解决了很多问题：

- 有持久 teammate
- 有 `.team/config.json` 团队名册
- 有 `.team/inbox/*.jsonl` 邮箱
- 每个 teammate 都能在独立线程里跑自己的 agent loop

但它依然有一个根本限制：

- 消息“能到达”，不等于动作“有语义”

举个例子。

如果 lead 发给 bob 一条消息：

```text
Please shut down gracefully.
```

理论上 bob 可以看懂，但系统本身并不知道：

- 这是不是一次正式关机请求
- bob 是否已经处理
- bob 是批准退出还是拒绝退出
- lead 之后该检查哪一个状态

同样地，如果 bob 发给 lead 一条消息：

```text
I plan to refactor auth in two steps.
```

lead 也许会回一句“ok”，但 harness 并不知道：

- 这是一次正式的 plan approval
- `ok` 对应的是哪一个计划
- 当前计划状态到底是 `approved` 还是只是“看到了”

所以第 10 课解决的不是“如何继续通信”，而是“如何把通信变成可以被 harness 追踪和约束的协议行为”。

---

## 三、这节课的核心设计：request_id + FSM + structured inbox message

这节课的核心设计可以概括成 3 个关键词：

- `request_id`
- `FSM`
- `structured message`

### 1）`request_id`

每次正式请求都生成一个唯一短 ID。

这意味着：

- lead 发起关机请求时，不再只是发一段文本
- teammate 提交计划时，也不再只是发一段文本

而是要附带一个唯一的 `request_id`。

这样后续所有响应都能引用同一个 ID，形成闭环。

### 2）FSM

这节课引入了最小状态机：

```text
pending -> approved
pending -> rejected
```

看起来很简单，但它的意义非常大，因为它第一次把“团队协作状态”从模糊语言变成了明确状态。

### 3）structured message

消息不再只是：

- `from`
- `to`
- `content`

还会带上：

- `type`
- `request_id`
- `approve`
- `feedback`
- `reason`
- `plan`

于是收件箱不再只是“留言板”，而开始变成“协议传输层”。

---

## 四、`ProtocolRegistry`：这节课真正新增的状态层

这节课最核心的新组件是 `ProtocolRegistry`，定义在 `agents/s10_team_protocols.py`。

它内部维护了两张表：

- `shutdown_requests`
- `plan_requests`

你可以把它理解为两个最小 tracker：

```text
shutdown_requests = {
  request_id: {
    target,
    status,
    created_at,
    ...
  }
}

plan_requests = {
  request_id: {
    from,
    plan,
    status,
    created_at,
    ...
  }
}
```

它们做的事情很像一个轻量协议数据库：

- 创建请求时登记为 `pending`
- 响应到来时切换到 `approved` 或 `rejected`
- 支持按 `request_id` 查询
- 支持打印当前所有协议状态

这就是为什么第 10 课新增了 `/protocols` 入口：你可以直接看到当前所有 shutdown / plan request 的状态。

从教学角度看，这一步非常关键，因为它把“模型之间的协商”落到了 harness 自己可见、可查、可断言的状态上。

---

## 五、第一条协议：Graceful Shutdown

第 10 课的第一条协议是安全关机握手。

它的目标不是“强制杀线程”，而是“请求队友自己完成收尾后退出”。

完整链路如下：

```text
lead 调用 shutdown_request
-> ProtocolRegistry 创建 request_id
-> shutdown_requests[request_id] = pending
-> MessageBus 向 teammate inbox 写入 shutdown_request
-> teammate 读取 inbox
-> teammate 调用 shutdown_response(approve/reject)
-> ProtocolRegistry 更新状态
-> MessageBus 把 shutdown_response 发回 lead
-> 如果 approve=True，teammate 设置 should_exit=True
```

这里最重要的不是“发消息”本身，而是这 3 件事同时成立：

1. 请求和响应共享同一个 `request_id`
2. harness 知道状态从 `pending` 变成了什么
3. teammate 的退出不是外部暴力终止，而是内部自愿收尾退出

这就是 graceful shutdown 的意义。

相比“直接停掉线程”，这种设计的好处是：

- 更安全，避免写文件写到一半
- 更可解释，状态变化可追踪
- 更容易扩展，将来可以加“拒绝原因”或“延迟退出”

---

## 六、第二条协议：Plan Approval

第二条协议是计划审批。

它的目标是：teammate 在做高风险改动前，先把计划显式提交给 lead，由 lead 批准或拒绝。

完整链路如下：

```text
teammate 调用 submit_plan
-> ProtocolRegistry 创建 request_id
-> plan_requests[request_id] = pending
-> MessageBus 向 lead inbox 写入 plan_approval_request
-> lead 在下一轮看到 inbox 注入
-> lead 调用 review_plan(approve/reject)
-> ProtocolRegistry 更新状态
-> MessageBus 把 plan_approval_response 发回 teammate
-> teammate 根据审批结果决定是否继续
```

和 shutdown protocol 对比一下，你会发现两者几乎是同一个模式：

- 一方发起 request
- 另一方引用同一个 `request_id` 返回 response
- 状态统一走 `pending -> approved/rejected`

这就是第 10 课最值得学的抽象：

- 不要把“关机”和“计划审批”看成两个孤立功能
- 它们本质上是同一个 request-response FSM 的两个具体实例

一旦你掌握这个抽象，后面还可以继续扩展：

- 代码 review approval
- 权限申请
- 资源锁申请
- 发布审批

只要是“发起请求 -> 等待确认 -> 状态闭环”的动作，都能套进这个协议模型。

---

## 七、消息总线没有重写，但语义升级了

第 10 课并没有推翻第 9 课的 `MessageBus`。

它仍然保留：

- `.team/inbox/<name>.jsonl`
- `send()` 追加 JSON
- `drain()` 读取后清空

但消息类型升级了。

这节课定义了更明确的 `VALID_MSG_TYPES`，例如：

- `message`
- `broadcast`
- `status`
- `shutdown_request`
- `shutdown_response`
- `plan_approval_request`
- `plan_approval_response`

这个变化看似不大，实际上非常关键。

因为从这一刻开始，收件箱里的消息不再只是“有人说了一句话”，而是“系统知道这是哪一种交互语义”。

这意味着 harness 后续可以：

- 针对不同类型做不同渲染
- 针对协议消息做额外检查
- 在测试里直接断言消息类型
- 在 UI 层把协议消息和普通聊天消息区分开

也就是说，s10 的变化虽然还停留在教学版，但已经开始具备真正 agent platform 的雏形了。

---

## 八、为什么这次特别强调 teammate 要“持续存活”

这次本地实现里有一个特别值得注意的点：teammate 在完成初始任务后，不能立刻退出线程。

如果它一做完事就结束，那么会出现一个很典型的问题：

```text
bob 完成任务
-> bob 线程退出
-> lead 之后发起 shutdown_request
-> bob 已经不在了
-> 请求永远没人响应
```

这说明什么？

说明“团队协议”并不只是多几个 tool name，而是会反过来约束 teammate 的生命周期设计。

所以当前 `agents/s10_team_protocols.py` 里，teammate 的逻辑变成了两段：

1. 先处理初始 assignment
2. 然后继续保持存活，轮询 inbox，等待后续协议消息

只有当：

- 收到了批准的 shutdown_request
- 并把 `state.should_exit = True`

线程才会真正结束，并把状态切到 `shutdown`。

这是第 10 课很重要的工程含义：

- 当你引入协议，运行时生命周期也必须跟着调整

否则协议只是“写在 prompt 里的规则”，却没有运行时保证。

---

## 九、lead 和 teammate 的工具边界是怎么重新划分的

这节课另一个很重要的变化，是工具边界更明确了。

### lead 侧新增工具

- `spawn_teammate`
- `broadcast_message`
- `list_team`
- `shutdown_request`
- `check_shutdown`
- `review_plan`
- `check_plan`

### teammate 侧新增工具

- `shutdown_response`
- `submit_plan`

这里体现出一个很真实的 harness 设计原则：

- 不是所有 agent 都应该有相同权限

例如：

- 只有 lead 能审批计划
- 只有 lead 能发起 shutdown_request
- 只有 teammate 能提交自己的计划
- 只有收到 shutdown request 的 teammate 能决定 approve/reject

这种工具级权限划分，比只靠 prompt 说“你是 lead”更稳，因为 harness 本身已经把权限边界编码进去了。

这也是从“角色设定”走向“角色约束”的一步。

---

## 十、agent loop 在这一课的关键变化是什么

从 agent loop 角度看，第 10 课并没有彻底改写主循环，而是在第 9 课的基础上强化了 inbox 注入的重要性。

每一轮调用模型前，如果开启 `poll_inbox=True`，就会：

1. `drain()` 当前成员的 inbox
2. 把收到的结构化消息格式化为 JSON
3. 注入成一条 `<inbox> ... </inbox>` 用户消息

于是模型每轮都会看到最新协议消息。

这意味着：

- lead 能在下一轮直接看到 `plan_approval_request`
- teammate 能在下一轮直接看到 `plan_approval_response`
- teammate 也能在后续轮次看到 `shutdown_request`

所以第 10 课不是在 API 层发明新协议，而是利用现有消息历史机制，把结构化 inbox 消息重新喂回模型。

这是一种很典型的 harness 技巧：

- 模型 API 不变
- 上下文组织方式改变
- 行为就会发生明显变化

---

## 十一、如何在终端里观察这节课

这节课很适合做交互式演示：

```bash
python3 agents/s10_team_protocols.py
```

然后你可以重点观察 3 类入口：

- `/team`：看成员状态，比如 `working`、`idle`、`shutdown`
- `/inbox`：看 lead 收到了什么消息
- `/protocols`：看所有协议请求当前是什么状态

你可以尝试这类 prompt：

1. `Spawn alice as a coder. Then request her shutdown.`
2. `Spawn bob with a risky refactor task. Review and reject his plan.`
3. `Spawn charlie, have him submit a plan, then approve it.`

观察时重点看 4 个现象：

- 是否生成了 `request_id`
- inbox 里消息类型是否已经结构化
- `/protocols` 是否能看到 `pending -> approved/rejected`
- teammate 最终状态是否从 `working/idle` 变成 `shutdown`

如果这 4 点都看到了，就说明你已经真正理解了这节课的协议闭环。

---

## 十二、这节课的测试为什么比前几课更重要

第 10 课很适合写单元测试，因为协议天然就是“可断言状态机”。

当前本地测试 `tests/test_s10_team_protocols.py` 重点覆盖了这些内容：

- `ProtocolRegistry` 能否正确创建和更新 shutdown request
- `ProtocolRegistry` 能否正确创建和更新 plan request
- lead 发起 `shutdown_request` 后，消息是否真的进入 teammate inbox
- teammate 响应 `shutdown_response` 后，状态是否真的更新并触发退出意图
- teammate 提交计划、lead 审批计划时，request-response 是否形成闭环
- inbox 是否会在模型调用前被正确注入
- 一个完整 mocked team flow 是否能从 spawn 一路走到 approved shutdown

这说明：到了第 10 课，测试不再只是“脚本能不能跑”，而是开始验证“协议是否保持一致性”。

这是一个非常重要的转折点。

因为真实世界里，很多 agent 系统问题都不是“模型不会回答”，而是：

- 消息串线
- 状态不一致
- 多轮之后协议失效
- 生命周期与协议不匹配

而这些问题，恰恰最适合通过测试暴露出来。

---

## 十三、你应该从第 10 课带走什么

如果只记一件事，请记这句：

- 多 agent 系统一旦进入协作阶段，关键动作就不能只靠自然语言，必须逐步协议化

第 10 课真正教会你的，不只是两个工具：

- `shutdown_request/shutdown_response`
- `submit_plan/review_plan`

而是一种更通用的 harness 设计方法：

1. 找出关键协作动作
2. 为动作定义 request 和 response
3. 给每次交互分配 `request_id`
4. 用最小 FSM 跟踪状态
5. 把状态放在 harness 自己可见的 registry 中
6. 让 agent 生命周期与协议要求保持一致

这套方法一旦掌握，后面做更复杂的 agent governance、team orchestration、autonomous workflow 时，都会反复用到。

从课程推进角度看：

- `s09` 让你有了“团队”
- `s10` 让这个团队开始“按规矩协作”
- `s11` 则会继续把这种协作推向更自主的 agent 行为

所以第 10 课是一个很关键的承上启下节点。
