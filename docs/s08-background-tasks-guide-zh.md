# 第 8 课讲解：Background Tasks 是如何实现的

这节课要解决的问题很具体：如果 agent 用普通 `bash` 去执行一个很慢的命令，那么整个 agent loop 都会被卡住，前台无法继续做别的事。

`s08` 的解法不是“让模型并发思考”，而是把“耗时工具执行”改造成“后台执行 + 下一轮结果回注”。

对应实现文件：`agents/s08_background_tasks.py`

---

## 一、先理解这节课的目标

在前几课里，agent 的工具调用基本都是同步的：

1. 模型发起 tool call
2. harness 执行工具
3. 等工具执行完
4. 把 `tool_result` 回灌给模型
5. 模型再继续下一步

这种方式对于 `read_file`、`write_file` 很适合，但对于下面这类命令就不够用了：

- 编译
- 跑测试
- 长时间抓取数据
- 任何包含 `sleep`、长轮询、慢 I/O 的 shell 命令

所以第 8 课新增了一个能力：

- 前台 agent 可以启动一个后台任务
- 后台线程自己慢慢跑
- agent 立刻继续处理别的工作
- 等后台跑完后，再把结果自动通知给模型

这就是最小版 background task harness。

---

## 二、核心设计：把“长命令”从主循环里拆出去

这节课的核心思路可以概括成一条链路：

```text
user 发出任务
-> 模型决定调用 background_run
-> BackgroundManager 创建后台线程
-> 主 agent 立即继续工作
-> 后台线程跑完 subprocess.run(...)
-> 结果进入 notification queue
-> 下一轮请求模型前，结果被注入 messages
-> 模型感知“后台任务已完成”
```

这里最关键的，不是“开线程”本身，而是两件事同时成立：

1. 前台不阻塞
2. 后台结果最终还能重新回到模型上下文

少了第 1 点，只是普通同步执行。少了第 2 点，后台任务即使完成，模型也不知道。

---

## 三、模型是怎么被教会使用后台工具的

系统提示词在 `agents/s08_background_tasks.py:38`：

- 长耗时命令用 `background_run`
- 通过 `check_background` 查看后台任务状态

也就是说，模型并不是天然知道“什么该异步、什么该同步”，而是 harness 在 system prompt 里明确给它行为规范。

这是 Harness Engineering 很重要的一点：

- 模型能力只是基础
- 真正决定行为模式的是 prompt + tools + loop 的组合

如果没有这段 system prompt，模型很可能仍然会习惯性地使用阻塞式 `bash`。

---

## 四、`BackgroundManager` 是这节课的核心组件

后台任务系统由 `BackgroundManager` 负责，定义在 `agents/s08_background_tasks.py:49`。

它内部维护了四类状态：

- `tasks`：任务表，记录每个任务的 `status`、`command`、`result`、`returncode`，见 `agents/s08_background_tasks.py:54`
- `_notification_queue`：通知队列，专门存放“已经完成、但还没告诉模型”的结果，见 `agents/s08_background_tasks.py:55`
- `_threads`：线程表，保存每个后台线程，便于测试和收尾，见 `agents/s08_background_tasks.py:56`
- `_lock`：线程锁，防止多线程同时修改任务状态，见 `agents/s08_background_tasks.py:57`

你可以把它理解成一个最小任务调度器：

- `tasks` 负责“当前状态”
- `_notification_queue` 负责“新完成事件”

这两个结构分开很重要，因为“查看当前所有任务”和“通知模型刚刚发生了什么”是两件不同的事。

---

## 五、`background_run` 为什么不会阻塞主流程

工具注册在 `agents/s08_background_tasks.py:275`，实际处理逻辑通过 `TOOL_HANDLERS` 映射到 `BG.run(...)`，见 `agents/s08_background_tasks.py:223`。

`run()` 的行为在 `agents/s08_background_tasks.py:59`：

1. 先做危险命令拦截
2. 生成短任务 id
3. 在任务表里登记为 `running`
4. 创建后台线程，目标函数是 `_execute(...)`
5. 立刻返回“任务已启动”

关键点在第 5 步。

真正耗时的是 `_execute()` 里的 `subprocess.run(...)`，但它是在后台线程中执行，而不是主线程中执行。所以：

- `background_run` 这个工具很快就返回
- agent loop 不需要等待命令完成
- 模型可以继续调用别的工具

这也是为什么真实测试里可以出现这样的顺序：

- 先启动一个 `sleep(2)` 的后台命令
- 再继续 `write_file` 创建 `bg_demo.txt`
- 最后再去 `check_background`

也就是说，前台 agent 的工作没有被慢命令卡住。

---

## 六、后台线程怎么执行命令并保存结果

后台线程真正执行命令的函数是 `_execute()`，定义在 `agents/s08_background_tasks.py:81`。

它内部调用：

```python
subprocess.run(
    command,
    shell=True,
    cwd=self.workdir,
    capture_output=True,
    text=True,
    timeout=300,
)
```

执行结束后，它会整理出三类核心结果：

- `status`：`completed` / `error` / `timeout`
- `result`：标准输出和标准错误
- `returncode`：进程退出码

然后做两件事：

1. 写回 `tasks[task_id]`，更新任务状态，见 `agents/s08_background_tasks.py:103`
2. 往 `_notification_queue` 推入一条完成通知，见 `agents/s08_background_tasks.py:108`

这一步的设计意义是：

- `tasks` 用于“随时查询”
- `notification_queue` 用于“增量提醒”

所以 lesson 8 的结果传播不是靠轮询 stdout，而是靠后台执行完成后主动留下一个通知对象。

---

## 七、`check_background` 负责“查状态”，不是“发通知”

`check_background` 的实现来自 `BG.check(...)`，定义在 `agents/s08_background_tasks.py:117`，工具描述在 `agents/s08_background_tasks.py:284`。

它支持两种模式：

- 不传 `task_id`：列出所有后台任务
- 传 `task_id`：查看某一个任务的详细状态和输出

例如它会返回类似这样的文本：

```text
3fa9e2bd: [running] python3 -c "import time; time.sleep(2); print('done')"
```

或者：

```text
[completed] python3 -c "print('done')" returncode=0
done
```

所以：

- `check_background` 是显式查询接口
- `_notification_queue` 是隐式通知接口

这两个机制一起构成了“可主动查、也可被动收”的最小后台任务模型。

---

## 八、最关键的一步：如何把后台结果重新喂给模型

第 8 课最值得学的地方，是 `inject_background_results(messages)`，定义在 `agents/s08_background_tasks.py:294`。

它的逻辑很简单，但很巧：

1. 调用 `BG.drain_notifications()` 一次性取出通知队列，见 `agents/s08_background_tasks.py:295`
2. 把所有完成通知拼成一段文本
3. 以一条新的 `user` 消息追加到 `messages`

注入后的格式大概是：

```text
<background-results>
[bg:abcd1234] completed command=python3 -c "print('done')"
done
</background-results>
```

这个设计为什么聪明？

- 它不需要改模型 API 协议
- 它不需要制造一种新的 message role
- 它只是把“后台结果”当成新的上下文事实重新告诉模型

于是模型在下一轮推理时，就会自然把这些结果当成“刚收到的新信息”。

这就是所谓的“结果回注”。

---

## 九、为什么通知是“下一轮到达”，不是“实时打断”

`agent_loop()` 在 `agents/s08_background_tasks.py:318`，每一轮开头都会先执行：

```python
inject_background_results(messages)
```

见 `agents/s08_background_tasks.py:320`。

这意味着后台结果的通知时机是：

- 不是后台线程一完成就直接打断当前模型调用
- 而是在“下一次准备请求模型之前”统一注入

所以它更准确地说是“下一轮自动带上后台结果”，而不是“即时事件推送”。

这样设计的好处是实现非常稳定：

- 不需要处理模型中途被打断
- 不需要 WebSocket 或事件循环
- 不需要复杂的并发控制

对于教学版 harness 来说，这个复杂度与效果的平衡很好。

---

## 十、`agent_loop()` 在这一课里发生了什么变化

`agent_loop()` 主体在 `agents/s08_background_tasks.py:318`。

和前几课相比，它的核心变化只有一处：

- 每轮请求模型前，先 `inject_background_results(messages)`

之后流程还是熟悉的那套：

1. 调用 `client.messages.create(...)`
2. 如果模型返回 `tool_use`
3. 用 `TOOL_HANDLERS` 分发工具
4. 打印工具执行结果
5. 把 `tool_result` 追加回 `messages`
6. 继续下一轮

这说明第 8 课的工程价值不是“重写 agent loop”，而是：

- 在尽量不破坏主循环的前提下
- 增加异步任务能力

这也是一个很好的架构信号：好的 harness 演进，往往是增量扩展，而不是推倒重来。

---

## 十一、真实执行过程可以怎样理解

你可以用下面这个例子去理解整条链路。

用户说：

```text
Run "python3 -c \"import time; time.sleep(2); print('done')\"" in the background,
then create a file called bg_demo.txt with the content hello while it runs,
and then check background task status.
```

可能出现的执行过程是：

1. 模型判断 `sleep(2)` 是慢命令，所以调用 `background_run`
2. harness 立即返回“Background task xxxx started”
3. 模型继续调用 `write_file`，创建 `bg_demo.txt`
4. 模型再调用 `check_background`
5. 如果后台已经跑完，就能看到 `[completed]` 和 `done`
6. 如果还没跑完，则会先看到 `running`
7. 下一轮进入时，若后台刚完成，通知会通过 `<background-results>` 自动注入

这就体现了两个层面：

- agent 可以主动查询后台状态
- agent 也可以在下一轮被动收到后台完成提醒

---

## 十二、这一课为什么还保留了普通 `bash`

第 8 课没有废弃同步 `bash`，而是同时保留了：

- `bash`：适合短命令，阻塞执行，见 `agents/s08_background_tasks.py:229`
- `background_run`：适合长命令，后台执行，见 `agents/s08_background_tasks.py:275`

这背后体现的是一个真实 harness 的设计原则：

- 不是所有工具都该异步化
- 只有“慢且不影响当前前台思考”的工具才适合放后台

比如：

- `cat foo.py` 这种几乎瞬时完成的命令，走同步最简单
- 长测、长构建、长抓取，才值得走后台

所以 `s08` 并不是“用后台替代一切”，而是给 agent 多一种执行策略。

---

## 十三、这节课还复用了哪些安全与文件能力

这一课不是单独造了一个后台系统，而是在前面课程基础上演进出来的。

它继续复用了：

- `safe_path()` 做工作区边界限制，见 `agents/s08_background_tasks.py:155`
- `read_file` / `write_file` / `edit_file` 文件工具，见 `agents/s08_background_tasks.py:237`
- `is_dangerous_command()` 危险命令拦截，见 `agents/s08_background_tasks.py:44`

尤其要注意一点：

- 阻塞式 `bash` 和异步 `background_run` 共用同一套危险命令校验

这很重要，因为如果只拦截同步命令、不拦截后台命令，agent 就可以通过异步路径绕过安全约束。

---

## 十四、测试到底验证了什么

测试文件是 `tests/test_s08_background_tasks.py`。

它主要锁住两条关键链路：

### 1）后台任务执行与通知入队

`tests/test_s08_background_tasks.py:4`

这个测试验证：

- `BackgroundManager.run()` 能启动后台任务
- `join_all()` 后任务能完成
- `check()` 能看到 `[completed]`
- `drain_notifications()` 能取到完成通知
- 通知取出后，队列会被清空

这相当于验证了“后台执行层”本身没有问题。

### 2）通知会被追加回 messages

`tests/test_s08_background_tasks.py:21`

这个测试验证：

- 当后台任务完成后
- `inject_background_results(messages)` 会往 `messages` 末尾追加一条新的 `user` 消息
- 这条消息里包含 `<background-results>` 和后台输出内容

这相当于验证了“后台结果重新进入模型上下文”这条链路。

---

## 十五、这节课的实现为什么已经很像真实 agent harness

虽然这是教学版，但它已经具备真实系统中的几个关键思想：

- 工具不是只有同步调用一种模式
- agent 需要处理“稍后回来”的结果
- 状态存储和事件通知要分离
- 模型上下文更新不一定只能来自用户直接输入

这几个点，已经非常接近真实生产场景里的：

- 后台 job
- 任务状态表
- completion event
- event-to-context injection

换句话说，第 8 课是从“单轮工具调用 agent”走向“能处理异步世界的 agent”的第一步。

---

## 十六、这份实现目前还做了哪些简化

为了教学清晰，当前实现仍然是简化版：

- 后台任务只保存在内存，没有持久化到磁盘
- 使用的是 `threading + subprocess.run(...)`，不是独立 worker 进程
- 通知是“下一轮注入”，不是实时推送
- 没有取消任务、重试、优先级、并发上限
- 没有跨进程恢复任务

所以它的目标不是生产可用，而是让你先掌握异步任务 harness 的最小结构。

---

## 十七、你应该记住的 4 个工程要点

如果只记这节课最重要的 4 点，建议记住下面这些：

1. `background_run` 的本质是“启动后立即返回”，而不是等待结果
2. `BackgroundManager` 需要同时维护任务表和通知队列
3. 后台完成结果必须重新注入 `messages`，否则模型无法感知
4. 真正的异步 agent 不是更复杂的模型，而是更好的 harness 设计

---

## 十八、一句话总结

第 8 课实现的不是“模型会后台思考”，而是：

- harness 让长耗时工具在后台线程执行
- 同时让 agent 前台继续工作
- 并在下一轮把后台完成结果重新喂给模型

这就是一个最小但真实的 Background Tasks 设计。
