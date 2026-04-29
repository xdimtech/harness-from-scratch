# Harness Engineering for Real Agents - 12 节课课程大纲

目标：参考 `learn-claude-code/docs/zh` 的 12 节课，在当前仓库中从零实现一个逐步进化的 agent harness。学完后，你应能独立设计、实现、调试并扩展一个面向真实任务的多 agent harness。

学习方式：每节课都按同一节奏推进：

1. 先理解这一课要解决什么问题
2. 再实现当前课的最小版本
3. 运行并观察行为
4. 做一两个小练习巩固
5. 通过验收标准后再进入下一课

测试记录文档：`docs/lesson-test-log-zh.md`

---

## 阶段 1：最小可用 Agent

### 第 1 课：The Agent Loop

- 主题：一个循环 + 一个工具，就是最小 agent
- 参考：`docs/zh/s01-the-agent-loop.md`
- 要解决的问题：模型会推理，但没有循环就无法持续与真实世界交互
- 你会学到：
  - `messages` 如何保存上下文
  - `stop_reason == "tool_use"` 如何驱动循环
  - `tool_result` 为什么必须回灌给模型
- 本课产出：
  - `agents/s01_agent_loop.py`
  - 最小 smoke test
- 验收标准：
  - 能让 agent 用 `bash` 创建文件、列目录、读取 git 状态

### 第 2 课：Tool Use

- 主题：新增工具时，不改循环，只扩展 dispatch
- 参考：`docs/zh/s02-tool-use.md`
- 要解决的问题：如果每加一个工具都改主循环，系统会越来越脆弱
- 你会学到：
  - 工具 schema 设计
  - `TOOL_HANDLERS` / dispatch map
  - 统一工具调用入口
- 本课产出：
  - `agents/s02_tool_use.py`
  - `bash` 之外的 1-2 个工具，例如 `read_file`、`write_file`
- 验收标准：
  - 新增工具时只需注册，不需要重写 agent loop

### 第 3 课：TodoWrite

- 主题：让 agent 先计划，再执行
- 参考：`docs/zh/s03-todo-write.md`
- 要解决的问题：没有显式计划时，agent 容易边做边忘、执行跳跃
- 你会学到：
  - 待办列表作为外显工作记忆
  - 任务拆分与状态更新
  - “先列计划，再逐项完成”的行为塑形
- 本课产出：
  - `agents/s03_todo_write.py`
  - `TodoWrite` 工具
- 验收标准：
  - agent 能先输出步骤，再逐步打勾完成

### 第 4 课：Subagent

- 主题：大任务拆小任务，每个小任务有独立上下文
- 参考：`docs/zh/s04-subagent.md`
- 要解决的问题：所有问题都塞进一个长对话，会造成上下文污染
- 你会学到：
  - 子 agent 的独立 `messages[]`
  - 主 agent 与子 agent 的职责切分
  - 子任务结果回传机制
- 本课产出：
  - `agents/s04_subagent.py`
  - 一个简单的 `spawn_subagent` 机制
- 验收标准：
  - 能把某个子问题交给独立上下文处理，并把结果带回主线

---

## 阶段 2：让 Agent 能长期工作

### 第 5 课：Skill Loading

- 主题：按需加载知识，而不是把所有知识塞进 system prompt
- 参考：`docs/zh/s05-skill-loading.md`
- 要解决的问题：system prompt 越来越长，模型越难聚焦
- 你会学到：
  - skill 文件组织方式
  - 运行时发现与加载 skill
  - 通过工具结果注入知识
- 本课产出：
  - `agents/s05_skill_loading.py`
  - `skills/` 目录与示例 skill
- 验收标准：
  - agent 能在需要时加载特定 skill，而不是预装全部知识

### 第 6 课：Context Compact

- 主题：上下文总会满，必须设计压缩策略
- 参考：`docs/zh/s06-context-compact.md`
- 要解决的问题：长对话会拖慢性能，也会推高成本和遗忘风险
- 你会学到：
  - 对话压缩时机
  - 摘要、保留、丢弃三类信息分层
  - 如何避免压缩后丢关键事实
- 本课产出：
  - `agents/s06_context_compact.py`
  - 对话摘要或压缩模块
- 验收标准：
  - 多轮任务后，agent 仍能记住关键目标与已完成事项

### 第 7 课：Task System

- 主题：把目标和任务图持久化到磁盘
- 参考：`docs/zh/s07-task-system.md`
- 要解决的问题：单次对话结束后，任务状态会丢失
- 你会学到：
  - 任务实体建模
  - JSON 文件持久化
  - 依赖关系、状态流转、任务列表
- 本课产出：
  - `agents/s07_task_system.py`
  - `.tasks/` 任务目录
- 验收标准：
  - 关闭进程后重新启动，任务状态还能恢复

### 第 8 课：Background Tasks

- 主题：慢操作放后台，agent 前台不停顿
- 参考：`docs/zh/s08-background-tasks.md`
- 讲解：`docs/s08-background-tasks-guide-zh.md`
- 要解决的问题：长时间 shell 命令会卡住整个 agent
- 你会学到：
  - 后台任务模型
  - 轮询 / 通知式结果回收
  - 前台继续思考与后台完成提醒
- 本课产出：
  - `agents/s08_background_tasks.py`
  - `background_jobs/` 或类似状态存储
- 验收标准：
  - agent 能启动耗时任务并继续处理别的工作

---

## 阶段 3：多 Agent 协作与隔离执行

### 第 9 课：Agent Teams

- 主题：一个 agent 不够时，组织一个团队
- 参考：`docs/zh/s09-agent-teams.md`
- 讲解：`docs/s09-agent-teams-guide-zh.md`
- 要解决的问题：复杂任务单 agent 处理效率低，且上下文拥堵
- 你会学到：
  - agent 身份与职责
  - 邮箱式异步通信
  - 团队成员的持久化状态
- 本课产出：
  - `agents/s09_agent_teams.py`
  - team inbox / outbox 机制
- 验收标准：
  - 多个 agent 能并行认领和推进不同任务

### 第 10 课：Team Protocols

- 主题：团队不是靠喊话，而是靠协议协作
- 参考：`docs/zh/s10-team-protocols.md`
- 要解决的问题：没有统一消息协议时，多 agent 协作很快混乱
- 你会学到：
  - request / response 协议
  - 消息结构化字段
  - 协议驱动的可靠协作
- 本课产出：
  - `agents/s10_team_protocols.py`
  - 统一的团队消息协议
- 验收标准：
  - agent 之间的请求、回复、确认格式一致且可追踪

### 第 11 课：Autonomous Agents

- 主题：队友自己从任务池认领工作，而不是等领导逐个分配
- 参考：`docs/zh/s11-autonomous-agents.md`
- 要解决的问题：如果所有分配都靠中心调度，系统会形成瓶颈
- 你会学到：
  - 自主认领机制
  - 周期性扫描任务池
  - 自组织协作模式
- 本课产出：
  - `agents/s11_autonomous_agents.py`
  - 自动巡检与认领逻辑
- 验收标准：
  - 多 agent 能自行发现可做任务并开始推进

### 第 12 课：Worktree + Task Isolation

- 主题：任务系统管理目标，worktree 管理目录隔离
- 参考：`docs/zh/s12-worktree-task-isolation.md`
- 要解决的问题：多 agent 并行改代码时，文件冲突会迅速放大
- 你会学到：
  - worktree 与 task 绑定
  - 每个任务独立工作目录
  - 任务隔离与后续合并策略
- 本课产出：
  - `agents/s12_worktree_task_isolation.py`
  - `.worktrees/` 与 task-worktree 映射
- 验收标准：
  - 不同任务能在不同 worktree 并行执行，互不干扰

---

## 建议的仓库结构

```text
harness-from-scratch/
  agents/
    s01_agent_loop.py
    s02_tool_use.py
    ...
    s12_worktree_task_isolation.py
  skills/
  tests/
  docs/
    course-outline-zh.md
  .tasks/
  .worktrees/
```

## 每节课统一模板

每次我会按下面这个模板带你学：

1. 这节课解决什么问题
2. 和上一课相比新增了什么机制
3. 先实现最小版本
4. 运行演示命令
5. 做 1-2 个练习
6. 检查验收标准
7. 最后再进入下一课

## 最终毕业目标

完成 12 节课后，你应该能：

- 说清楚 agent 和 harness 的边界
- 从零实现一个可运行的 agent loop
- 设计可扩展的工具系统、skill 系统和任务系统
- 做多 agent 协作、协议通信、后台任务和上下文压缩
- 用 worktree 做真实代码任务隔离
- 基于这些模式，继续扩展到你自己的真实 agent 产品
