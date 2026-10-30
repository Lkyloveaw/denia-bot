# Mnemosyne 长期记忆实现调研

调研日期：2026-10-28。范围：[lxfight/astrbot_plugin_mnemosyne](https://github.com/lxfight/astrbot_plugin_mnemosyne)，并与本项目 `nonebot/plugins/memory.py`、`nonebot/memory_logic.py` 对照。该仓库另有名称相似的插件，本文特指这个项目。其 `main.py` 注册版本为 2.3.0，README 中的版本文字可能滞后；以下以当前源码和配置为准。资料是阅读所得，不代表已部署或实测。

## Mnemosyne 如何工作

- **多轮后台总结**：默认 5 轮问答触发总结，累计消息后生成单段长期记忆，模型输出再做 embedding 并存入向量库。配置可指定总结模型、备用模型、超时与并发上限；失败回调恢复计数器，使下轮能重新触发。来源：[配置](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/_conf_schema.json)、[触发和失败回滚](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L1163-L1261)、[总结和向量化](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L1910-L1976)。
- **按相关性召回**：查询时基于问题 embedding 和过滤条件检索，源码取最多 `min(top_k * 4, 60)` 个候选，再用关键词、可选的一跳实体关系做后处理；`top_k` 默认 3。来源：[检索代码](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L1263-L1337)、[后处理](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L727-L795)、[配置](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/_conf_schema.json)。
- **隔离粒度**：默认会话过滤与人格过滤开启；群成员过滤是可选、默认关闭。尤其要注意：参与者过滤是后处理，缺失参与者元数据的旧记录仍保留；过滤后若没有匹配项，代码保留原候选。因此它不应被当成严格的用户级隐私边界。简单关闭会话过滤会在所有会话搜索，不等于安全地跨群/私聊共享同一用户的记忆。来源：[配置](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/_conf_schema.json)、[会话过滤](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L1283-L1330)、[参与者过滤](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/core/memory_operations.py#L750-L767)。
- **可靠性与资源控制**：向量库初始化失败时允许降级运行，embedding 可延迟初始化；配置中有总结并发、模型与 embedding 请求超时、上下文缓存条数和空闲过期。请求事件也使用有界记录来避免重复处理。来源：[插件初始化](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/main.py#L65-L102)、[配置](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/_conf_schema.json)。
- **内容归属与管理**：总结提示包含说话人映射和时间锚点，减少把助手的话写成用户事实或凭空编造日期；支持通过命令和管理界面操作记忆。来源：[配置](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/_conf_schema.json)、[命令入口](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/main.py#L608-L664)。

## 对现有 QQ 机器人的启发

1. **处理重复、过时和容量问题，优先级最高。** 现实现按 `user_id + text` 精确去重、每人上限 30 条，满了以后新记忆不写入；相似表述和相互矛盾的偏好会同时保留。建议记录更新时间与来源，判断相似/冲突时先生成候选，不要静默覆盖；满额时明确告知或进入可管理的候选池。Mnemosyne 的总结及检索设计可启发这一步，但上述去重和冲突策略是本项目建议，不能声称已由其实现。
2. **故障可恢复而非静默丢失。** 当前每次回复后启动内存任务；队列满、模型超时或进程重启会丢该次提取。借鉴其失败回滚，采用有限持久任务队列或从已持久化的会话记录补提取；设去重 ID、明确超限策略，并验证删除之后旧任务不能复活记忆。要保留当前项目已有 revision 防复活机制。
3. **多轮上下文改善“重要经历”。** 现只给提取器本轮用户文本，遇到“那天的事”“我决定了”等指代难以理解。可选 3–5 轮、限长度地带入当前用户原话；仅从真实用户发言提事实、标注说话人与时间，防止把机器人编造的剧情变成事实。用户先前选择每轮异步提取；批量总结省调用但会改变实时性，应作为可选模式而非擅自切换。
4. **按当前话题限量召回。** 现每次直接向提示词注入该用户最多 30 条，闲聊也会携带全部事实。先试轻量关键词筛选、最近性及固定 token/条数预算；当用户记忆数量或误召回确实成为问题时，再考虑复用现有 BGE-M3 服务提供单独 embedding 接口、SQLite 存向量进行小规模搜索。现有 `rag` 服务的 `/search` 只搜索知识语料，不应把用户记忆写入共享知识索引；可复用模型不等于能直接复用现有检索接口。不同用户必须先严格按 user_id 缩小候选，再相似度排序；不能靠向量后过滤或全局 top-k 保证隔离。
5. **私聊信息保护应由代码执行。** 当前将私聊记忆注入群聊并要求模型不主动透露，仅为提示约束，不构成可靠屏障。用户希望跨场景共享且不主动暴露私聊细节，建议记录来源和可共享范围、允许用户控制哪些事实可跨场景，并决定遇到私聊来源是否干脆不注入群聊；任何兼顾群聊使用和不泄露的方案需与用户确认体验权衡。
6. **可观测性和用户操作。** 现已有私聊查看、纠正、忘记，以及不记录内容的失败日志；可增加满额/任务失败/最近提取状态、备份与恢复。Mnemosyne 的多后端管理面板、图谱和外部向量数据库对本项目的少量用户/30 条上限不是必要依赖；当前服务本身也已有 4 GB 服务器内存压力。

## 实施注意

- 不能仅按用户输入做语义检索就视为隔离：过滤范围、群聊/私聊权限和用户名/多 Bot 的身份键必须在存取边界强制执行，身份缺失时拒绝召回，不做跨用户兜底。
- 记录新的记忆时需要防止重复、过期覆盖和提示词注入；涉及个人信息的原话/模型输出应少进日志。给用户明确的删除、纠正与关闭机制。
- 当前项目 `on_finish_chat` 成功回话后异步提取、2 并发、最多 32 个内存任务，单条输入最多 2000 字，失败只记录错误类型；数据落本地 SQLite。当前每用户 30 条上限是硬上限，达到上限后新事实不再写入。
- 该仓库 LICENSE 当前为 [AGPL-3.0](https://github.com/lxfight/astrbot_plugin_mnemosyne/blob/main/LICENSE)，研究设计思路与复制/改造代码是不同事项；如需直接复用代码，应先确认许可证义务。
