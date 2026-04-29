# Harness Engineering for Real Agents - 12 课毕业总复盘

这份文档不是重复每一课的细节，而是站在“学完整套课之后”的视角，回头看这 12 节课到底拼出了什么、各课之间是什么关系、以及你现在已经具备了哪些真实的 harness 设计能力。

如果只用一句话概括整套课程，可以记成：

- 这 12 课不是在教你“怎么写一个会聊天的模型调用脚本”，而是在教你“怎么把模型组织成一个可以长期工作、可观测、可恢复、可协作、可隔离的 agent system”

测试记录见：`docs/lesson-test-log-zh.md`

---

## 一、12 节课最后拼出了什么

学完以后，你手里的已经不是一个单点脚本，而是一套逐步成型的 agent harness：

- 能让模型进入稳定的工具调用循环
- 能通过工具系统访问文件、shell、任务板、后台任务和团队协议
- 能在长任务中保留计划、压缩上下文、恢复关键记忆
- 能把任务持久化到磁盘，而不是只存在当前对话里
- 能组织多个 agent 分工、发消息、协作推进
- 能让 teammate 在空闲时自主认领任务继续工作
- 能为不同任务创建独立 git worktree，避免并行执行互相污染

也就是说，课程最终产出的不是“更聪明的 prompt”，而是更完整的运行时结构：

```text
模型能力
  -> 工具能力
  -> 计划能力
  -> 记忆与压缩
  -> 任务持久化
  -> 多 agent 协作
  -> 自治调度
  -> 工作目录隔离
```

这条链路基本覆盖了一个真实 agent harness 从 demo 走向工程原型时最核心的一组台阶。

---

## 二、课程的 4 个阶段

### 阶段 1：让模型“真的能做事”

对应 `s01 ~ s04`

这一阶段解决的是最底层问题：模型不是天然 agent，只有在你给它循环、工具和任务拆分机制之后，它才会从“回答问题”变成“持续执行”。

- `s01` 建立 agent loop，让模型能在 `tool_use -> tool_result -> continue` 中工作
- `s02` 把工具调用做成可扩展 dispatch，而不是每加一个工具都改主循环
- `s03` 引入显式 todo，让 agent 有外显工作记忆
- `s04` 引入 subagent，让复杂任务可以切成独立上下文的小任务

这一阶段的关键词是：

- loop
- tools
- planning
- context isolation

如果没有这 4 课，后面所有“团队协作”“自治运行”都没有地基。

### 阶段 2：让 agent“能持续工作”

对应 `s05 ~ s08`

这一阶段开始从一次性 demo 走向长期运行系统。重点不再只是“会做事”，而是“不会越做越乱”。

- `s05` 解决知识装载问题：skill 按需加载，而不是把所有知识塞进 system prompt
- `s06` 解决长对话必然爆炸的问题：上下文压缩、历史转存、关键事实保留
- `s07` 解决任务在进程结束后消失的问题：任务板持久化
- `s08` 解决慢操作阻塞的问题：后台任务与前台 loop 解耦

这一阶段的关键词是：

- lazy knowledge
- compact memory
- persistent tasks
- background execution

到这里，agent 才开始具备“能跑久一点”的基础。

### 阶段 3：让多个 agent“像团队一样协作”

对应 `s09 ~ s10`

这一阶段把视角从“单个 agent”提升到“团队系统”。

- `s09` 让多个 agent 有身份、有收件箱、有消息传递能力
- `s10` 让协作不再靠自然语言随意喊话，而是靠结构化协议推进

这一阶段的关键词是：

- teammates
- mailbox
- protocol
- coordination

也就是从“多开几个模型实例”升级成“有通信规范的 agent team”。

### 阶段 4：让团队“更自治、更安全”

对应 `s11 ~ s12`

这是最接近真实工程运行的一段。

- `s11` 让 teammate 在 idle 时自己去看 inbox、扫描 task board、认领 ready task
- `s12` 让每个任务拥有独立 worktree，把逻辑隔离推进到目录隔离

这一阶段的关键词是：

- autonomy
- idle loop
- task claiming
- workspace isolation

到这里，课程终于补上了两个真实系统最重要的能力：

- 没人盯着时还能继续往前走
- 多任务并行时不把彼此的工作区搞乱

---

## 三、12 节课逐课复盘

### `s01` The Agent Loop

- 你学到的本质：agent 不是模型本身，而是“模型 + 循环 + 工具回灌”
- 最重要的结构：`stop_reason == tool_use`
- 长期价值：以后不管换哪个模型供应商，这一层 loop 思想都不变

### `s02` Tool Use

- 你学到的本质：循环要稳定，工具要可插拔
- 最重要的结构：工具 schema + handler registry
- 长期价值：后面新增任务工具、后台工具、协议工具，都是这层思想的延续

### `s03` TodoWrite

- 你学到的本质：让计划显式化，agent 才更稳
- 最重要的结构：todo 列表就是外显短期记忆
- 长期价值：很多“agent 看起来聪明但做事跳步”的问题，最终都要靠外显计划约束

### `s04` Subagent

- 你学到的本质：上下文隔离不是靠 prompt，而是靠独立子会话
- 最重要的结构：父 agent 委派子任务，只拿回摘要结果
- 长期价值：这是多 agent 与后续 team 协作的雏形

### `s05` Skill Loading

- 你学到的本质：知识不应该永远常驻，而应该按需注入
- 最重要的结构：skill discovery + `load_skill`
- 长期价值：这是控制 prompt 膨胀的第一层手段

### `s06` Context Compact

- 你学到的本质：上下文一定会满，所以压缩不是优化项，而是必选项
- 最重要的结构：摘要、保留、转存 transcript
- 长期价值：没有 compact，就谈不上长期运行

### `s07` Task System

- 你学到的本质：任务要成为系统对象，而不是聊天里的临时句子
- 最重要的结构：`.tasks/*.json`
- 长期价值：任务板是后面团队协作、自主认领、目录隔离的共同地基

### `s08` Background Tasks

- 你学到的本质：agent 的思考流程和耗时执行要分开
- 最重要的结构：前台 loop + 后台 job 状态
- 长期价值：没有后台模型，agent 会被一个长 shell 命令卡死

### `s09` Agent Teams

- 你学到的本质：团队不是多开几个 agent，而是要有身份和消息通道
- 最重要的结构：inbox / outbox / persistent teammate
- 长期价值：这是从 single-agent tool runner 走向组织化系统的拐点

### `s10` Team Protocols

- 你学到的本质：协作要可靠，就要把“请求/回复/确认”做成协议
- 最重要的结构：结构化 message / protocol registry
- 长期价值：没有协议，多 agent 很快会进入不可追踪状态

### `s11` Autonomous Agents

- 你学到的本质：真正的 teammate 不是总等分配，而是会在空闲时自己找事做
- 最重要的结构：`WORK -> IDLE -> WORK -> SHUTDOWN`
- 长期价值：这是把“协作”推进到“有限自治”的关键一跳

### `s12` Worktree + Task Isolation

- 你学到的本质：任务隔离如果只停留在逻辑层，真实工程里仍然不够
- 最重要的结构：task board + worktree mapping + lifecycle events
- 长期价值：这是把多 agent 从“理论可并行”推进到“工程上更安全地并行”

---

## 四、把整套课程串起来看：能力是怎样一层层长出来的

如果把 12 节课串成一张纯 ASCII 图，大致是这样：

```text
s01 loop
  -> s02 tools
  -> s03 todo planning
  -> s04 subagent isolation
  -> s05 on-demand skills
  -> s06 context compact
  -> s07 persistent task board
  -> s08 background jobs
  -> s09 agent teams
  -> s10 team protocols
  -> s11 autonomous teammates
  -> s12 task/worktree isolation
```

它不是 12 个平行知识点，而是一条明显的工程演化路线：

```text
单 agent demo
  -> 可扩展工具系统
  -> 可持续运行
  -> 多 agent 协作
  -> 自治执行
  -> 安全并行
```

所以如果以后你要自己设计一套 harness，最重要的不是记住每个文件名，而是记住这个演化顺序。

很多团队一上来就想做：

- agent team
- 多任务并发
- 自动 codegen
- 自动修 bug

但如果底层 loop、任务板、上下文压缩、协议、工作区隔离没补齐，系统会很快变成“看起来很热闹，实际上不可控”。

这套课的价值，就在于它把这些台阶按工程依赖顺序铺出来了。

---

## 五、你现在真正掌握了哪些 Harness Engineering 核心方法

学完 12 课，你至少已经掌握了下面这些方法论：

### 1. 不把 prompt 当成系统

你已经看到了，真实 agent 行为很大程度上不是由一段神奇 prompt 决定，而是由下面这些运行时结构决定：

- loop
- tools
- task state
- message protocol
- file persistence
- execution isolation

这是一种非常重要的工程视角转变：

- 从“优化提示词”
- 转向“设计运行机制”

### 2. 把不可见状态变成可见状态

整套课反复在做同一件事：

- todo 可见
- task 可见
- inbox 可见
- background job 可见
- compact transcript 可见
- worktree lifecycle 可见

这说明一个真实 harness 的核心原则是：

- 能落盘的就尽量落盘
- 能观测的就尽量观测

因为不可见状态一多，系统一出问题就很难调。

### 3. 把“智能”拆成多个受约束的子能力

课程没有追求一个无所不能的大 agent，而是在不断拆分能力边界：

- 计划与执行分开
- 主 agent 与 subagent 分开
- lead 与 teammate 分开
- task control 与 worktree execution 分开

这背后的思想是：

- 与其让一个大上下文 agent 什么都知道，不如让多个小能力模块按协议协作

### 4. 先解决可靠性，再追求复杂度

整套课的演进顺序非常克制：

- 先 loop
- 再 tools
- 再 memory
- 再 tasks
- 再 teams
- 最后 autonomy 和 isolation

这其实是在强调：

- agent 系统最先缺的不是“更强能力”，而是“更稳边界”

---

## 六、从课程到真实项目，中间还差什么

虽然 12 课已经把骨架搭起来了，但如果你要把它继续推进到更像生产原型，通常还需要补下面几层：

### 1. 权限与安全边界

例如：

- 不同工具的权限分级
- shell 命令白名单 / 风险审计
- secrets 管理
- 外部输入的注入防护

### 2. 更强的可观测性

例如：

- 统一事件总线
- trace id / task id / message id 关联
- dashboard
- 失败重试统计

### 3. 更稳定的恢复能力

例如：

- agent 进程意外退出后的恢复
- 后台任务重连
- inbox 幂等处理
- worktree 清理与 orphan 检测

### 4. 更成熟的评估体系

例如：

- 每课级别的 smoke tests
- 多 agent 协作回归测试
- 长对话 compact 回归
- 任务生命周期一致性检查

也就是说，12 课已经让你跨过了“从 0 到 1”的关键门槛，但距离生产级系统，还会继续往：

- security
- observability
- recovery
- evaluation

这几个方向再加深。

---

## 七、如果现在让你自己从零设计一套 harness，推荐顺序是什么

这也是整套课程最值得带走的一张“脑中施工图”：

### 第一步：先搭最小闭环

- 模型调用
- tool loop
- 至少一个真实工具

目标不是强，而是先让系统真的跑起来。

### 第二步：把工具系统做成可扩展

- tool schema
- dispatch table
- 统一错误处理

这样后面新增功能不会破坏主循环。

### 第三步：补上记忆与任务

- todo
- compact
- task board

这样 agent 才能跨多轮、多阶段工作。

### 第四步：再做团队协作

- teammate
- message bus
- team protocol

不要过早做 team，否则你只是把单 agent 的混乱复制成多 agent 的混乱。

### 第五步：最后做自治与隔离

- idle loop
- task claiming
- worktree isolation

到这一步，系统才开始接近“真实可用的 agent harness 原型”。

---

## 八、这套课程最值得反复回看的 5 个文件

如果未来你要快速复习，我建议优先回看下面 5 个实现入口：

- `agents/s01_agent_loop.py`
- `agents/s06_context_compact.py`
- `agents/s07_task_system.py`
- `agents/s10_team_protocols.py`
- `agents/s12_worktree_task_isolation.py`

原因分别是：

- `s01` 看最小闭环
- `s06` 看长期运行为什么必须压缩
- `s07` 看任务如何落地成系统对象
- `s10` 看团队为什么必须协议化
- `s12` 看真实工程为什么必须目录隔离

如果这 5 个点吃透了，你基本就抓住了整套课的主骨架。

---

## 九、毕业后的实践建议

学完课程之后，最好的下一步不是立刻再看更多理论，而是做 3 个实战练习。

### 练习 1：做一个你自己的单 agent harness

要求：

- 保留 loop
- 保留 tools
- 保留 todo
- 保留 compact

先做成一个能稳定完成本地文件任务的小系统。

### 练习 2：把它升级成双 agent 协作

要求：

- 一个 lead
- 一个 teammate
- 有 inbox
- 有 task board

重点不是人数多，而是把协作结构走通。

### 练习 3：给真实编码任务加 worktree 隔离

要求：

- 一个任务一个 worktree
- 能 create / run / keep / remove
- 能把 task 和 worktree 生命周期记录下来

这一步会让你真正体会到第 12 课为什么是“工程边界课”。

---

## 十、一句话毕业总结

如果要给这 12 课写一句毕业评语，我会这样说：

- 你学到的不是“怎么调用大模型”，而是“怎么给大模型搭一个能长期、稳定、可观测、可协作地工作的工程外壳”

这就是 Harness Engineering for Real Agents 的核心。

再换一句更工程化的话：

- prompt 决定 agent 当下怎么想，harness 决定 agent 长期怎么活

而这 12 节课，正是在一步一步把这个 “长期怎么活” 的系统骨架搭起来。
