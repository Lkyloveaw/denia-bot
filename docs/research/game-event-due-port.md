# 游戏活动日历移植研判

研判日期：2026-10-31（Asia/Shanghai）。状态：**设计待确认，未实施**。

上游为 [XinDuKW/astrbot_plugin_game_event_due](https://github.com/XinDuKW/astrbot_plugin_game_event_due)，本报告固定到提交 `daeb265ef42b7a576cf70f646d2859a68ec01fd6`，其 [metadata.yaml](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/metadata.yaml#L1-L13) 声明版本 1.1.1。所有源码判断均针对这一快照，不推断后续版本已修复或仍存在相同问题。

## 1. 结论与边界

值得引入的是“跨游戏活动聚合 + 每会话订阅 + 分级到期提醒”的产品能力与公开协议。它能补足本仓库现有游戏查询功能的活动发现与截止时间提醒，但不是把 AstrBot 插件文件拷入 NoneBot 就能运行：命令入口、会话标识、生命周期、存储和后台都需要重新接线。上游已经把纯提醒决策和数据源拆出，概念边界可供参考；AstrBot 的消息组件、KV、Plugin Pages 不适用于本项目。[上游入口](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/main.py)、[提醒逻辑](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/reminder_service.py#L1-L22)、[发送层](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/delivery_service.py#L18-L35)

**建议按功能与协议独立实现，不直接复制上游实现。**固定提交的递归树中未发现 LICENSE/COPYING 等授权文本，GitHub 仓库元数据的 license 为 null。这里仅记录可见授权事实，不作法律结论；若以后需要复用具体实现，先获得清楚的授权信息。[固定提交目录树](https://api.github.com/repos/XinDuKW/astrbot_plugin_game_event_due/git/trees/daeb265ef42b7a576cf70f646d2859a68ec01fd6?recursive=1)、[仓库元数据](https://api.github.com/repos/XinDuKW/astrbot_plugin_game_event_due)

建议待批准范围覆盖上游 10 个 game_id、查询、订阅、到期阈值、过滤、封面、转发和现有面板管理；紧急私聊功能可保留，但默认关闭。完整设计另见[本次设计文档](../superpowers/specs/2026-10-31-game-event-due-design.md)；下文的结构、估时和默认值都是建议，不代表已经获准写产品代码。

## 2. 上游实际能力

上游注册 8 个 SRA 游戏，加 AKEData 的终末地、PRTS 的明日方舟，共 10 个可订阅对象。蔚蓝档案三地区是三个 game_id；输入“蔚蓝档案”等共享别名会匹配多个地区。游戏别名与显示名分别处理。[SRA 游戏注册](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/sra.py#L26-L59)、[AKEData 注册](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L49-L55)、[PRTS 注册](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L65-L71)、[别名匹配](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/registry.py)

| 能力 | 当前实现及移植口径 |
| --- | --- |
| 查询 | 当前进行中/未来活动；数据拉取有短期内存复用。需要区分源错误、缓存、过期数据和正常空集合。 |
| 订阅 | 按群/私聊会话保存游戏、启停、阈值、黑白名单和封面覆盖值。订阅概览成功后建立基线。 |
| 新活动 | 第一次进入某会话视野时提醒；不是活动开始时必然再提醒。默认预告窗口 45 天、每轮每会话每游戏最多 8 条纯新活动。 |
| 倒计时 | 默认 7/3/1 天，取当前最紧急档位，只在更紧急时再发；同轮新发现与倒计时合并。 |
| 过滤 | “名称 + 描述 + 标签”不区分大小写；白名单至少命中一条，黑名单命中直接排除。 |
| 消息 | 封面图片/链接/关闭；压缩、预算、图片发送失败降级；按游戏合并，或按活动生成转发节点。 |
| 紧急私聊 | 目标、活动确认、重复间隔、静默时段、每日计数；实现边界见后文。 |
| 后台 | AstrBot Plugin Pages 查询/编辑订阅、预览和刷新；需要改造成现有 dashboard 的页面/API。 |

上述能力由 [README 指令与说明](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/README.md)、[会话模型](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/subscription_service.py#L80-L132)、[提醒决策](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/reminder_service.py#L133-L239)、[轮询](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/polling_service.py#L144-L310)、[后台](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/activity_webui.py)交叉核查。上游有源解析、提醒、订阅、封面、消息和紧急私聊的测试文件，但本次只读代码，**未运行上游测试，不据文件存在推断测试已通过**。[测试树](https://github.com/XinDuKW/astrbot_plugin_game_event_due/tree/daeb265ef42b7a576cf70f646d2859a68ec01fd6/tests)

## 3. 三个数据源：协议、依赖与验收门槛

### 3.1 SRA

官方文档说明为免费、无需账号与 API 密钥的 JSON GET 接口；活动数据由社区维护者从公告提取并核对，不等于游戏厂商提供的实时接口。路径为 `/api/v1/activity/{game}.json`，还支持语言后缀；版本与活动分别有起止时间，活动可含 kind，另有可选 pools。[SRA 官方协议与维护流程](https://starrailassistant.top/reference/public-api/)

固定上游只注册 sr/ys/zzz/ww/nte/ba-cn/ba-global/ba-jp；解析 activities 的 name、description、startTime、endTime、cover，没有结束时间的条目会被跳过，不读取 kind/pools，ID 由“名称 + 开始时间（否则结束时间）”生成。[SRA 适配器](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/sra.py#L26-L59)、[解析](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/sra.py#L112-L144)

**文档已超出上游覆盖范围。**本次读取的 SRA 官方文档还列出 ak（明日方舟）与 end（终末地）及其他游戏，并定义 kind/pools。因此“官方 SRA 只支持八款”已经不能作为当前事实。可是新增接口的数据新鲜度与完整性仍须单独验收；不能仅凭接口出现就取代 PRTS/AKEData。[当前官方文档](https://starrailassistant.top/reference/public-api/)

建议第一版保留上游的三源映射，SRA end 可作为后续评估对象，SRA ak 当前可见快照全过期，不建议据此替代 PRTS。多源自动切换会牵涉 ID 对齐、重复提醒与来源解释，不应在本轮默默加入。

### 3.2 AKEData

网站首页把数据请求源标为 data.akedata.wiki，并提示服务器数据可能延迟。公开首页不是字段级 API 保证；下面详细协议主要从固定上游调用实现核查，不能把它包装成已核验的官方稳定契约。[AKEData 首页](https://www.akedata.wiki/)、[上游 AKEData 适配器](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L123-L248)

调用流程：

1. GET `https://data.akedata.wiki/manifest.json`，读取 latest、versions[]，选择带 tableCfgPath 的版本；使用 id、gameVersion、updatedAt。
2. 在 tableCfgPath 下读取 ActivityTable.json、TimeRangeTable.json、ActivityTagTable.json、I18nTextTable_CN.json；启用寻访时再读 GachaCharPoolTable.json。
3. 活动 timeId（缺省 time_{activity_id}）关联 TimeRangeTable.timeRangeList，当前只取第一个区间的 openTime/closeTime。
4. 活动 name/desc、标签名通过文本 ID 关联中文表；无 closeTime 的常驻内容不入提醒。
5. 寻访使用 clientTopTimeId，type 映射标签，upCharIds 当前只作为角色 ID 文本展示。封面由 tabImg 拼资源路径。

以上字段与流程对应 [版本选择/拉表](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L183-L248)、[时间区间](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L75-L92)、[活动与寻访解析](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L252-L365)。上游文本表体量约 11MB 的说法来自其代码和 README；本次未下载此表，未重新量测大小。[体积说明](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L1-L12)

移植验收应检查 manifest 与关联表来自同一可用版本、文本缺失率、可解析时间区间数、有效限时活动数、寻访开关、版本跳跃和多区间行为。不能将半套新表加半套旧缓存当成完整新版本发布。

### 3.3 PRTS

PRTS 模板文档明确记录活动开始/结束、兑换结束、名称、缩短名称、标题图文件名和官网链接，并说明模板负责写页面属性；其中“活动信息/info”仅展示信息，不写同样的页面属性，因此语义查询不是所有活动页面的完备全集。[PRTS 模板:活动信息](https://prts.wiki/w/%E6%A8%A1%E6%9D%BF:%E6%B4%BB%E5%8A%A8%E4%BF%A1%E6%81%AF)

上游向 `https://prts.wiki/api.php` 发 GET：action=ask、format=json；query 查分类“有活动信息的页面”且结束日期晚于“今天减7天”，请求开始、结束、兑换结束、名称、名称缩短、分类、标题图文件名、官网链接。读取 query.results 与 query-continue-offset，最多五页；每次请求前至少间隔 20 秒。printouts 中时间以 timestamp 转 UTC 后再转 UTC+8；活动 ID 基于页面标题，fullurl 归一为详情链接。[请求协议](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L34-L63)、[查询/分页](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L157-L206)、[时间解析](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L74-L108)、[模型转换](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L208-L251)

PRTS 的限流/WAF 403 是上游描述，本次未观察到源站 403，不能写成实测结果。实现使用低频、Referer 和缓存回退；首次验收只做一次低频 ask，之后依响应与站点要求决定下一步，避免逐活动抓页面。[上游说明](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L1-L15)

PRTS 页面底部对游戏原始素材与其他站点内容分别给出版权说明；移植时需保留来源标注，并分别核对具体内容的使用条件。此处只记录页面说明，不下法律结论。[PRTS 页面声明](https://prts.wiki/w/%E6%A8%A1%E6%9D%BF:%E6%B4%BB%E5%8A%A8%E4%BF%A1%E6%81%AF)

## 4. 2026-10-31 只读探测记录

### 4.1 必须分开的两类证据

**本机直连：**2026-10-31 14:12:11 +08:00，PowerShell Invoke-WebRequest 访问 SRA sr.json 和 PRTS ask 均报“未知主机/DNS 无法解析”；此前对 AKEData manifest 同样如此。请求未取得 HTTP 响应，HTTP 状态、ETag、Last-Modified、响应体和实时活动数均为 **未取得**。这说明当前研究环境无法直连，不能推出线上服务不可用。

**浏览工具读取：**通过联网浏览服务取得下表 JSON 内容，但服务显示其抓取缓存时间；没有暴露源站 HTTP 状态与响应头。表中“数量/时间”是对可见快照的检查，**不是本机实时 HTTP 200 实测，也不是在线准确性承诺**。活动状态统一按 2026-10-31 14:12 +08:00 计算，将无时区的源时间按上游规则解释为 UTC+8。[时间规则](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L20-L23)、[时间归一](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L61-L85)

### 4.2 SRA 可见快照

| 端点 | 版本 | 活动总数 | 进行中/未来/过期 | 浏览服务标注的抓取缓存 |
| --- | --- | ---: | --- | --- |
| [sr.json](https://starrailassistant.top/api/v1/activity/sr.json) | 4.6 | 15 | 12 / 1 / 2 | 昨日 |
| [ys.json](https://starrailassistant.top/api/v1/activity/ys.json) | 7.1 | 8 | 7 / 0 / 1 | 昨日 |
| [zzz.json](https://starrailassistant.top/api/v1/activity/zzz.json) | 3.3 | 9 | 4 / 5 / 0 | 昨日 |
| [ww.json](https://starrailassistant.top/api/v1/activity/ww.json) | 3.7 | 12 | 9 / 1 / 2 | 当日 |
| [nte.json](https://starrailassistant.top/api/v1/activity/nte.json) | 1.4 | 12 | 10 / 0 / 2 | 首次两日前，补读标当日 |
| [ba-cn.json](https://starrailassistant.top/api/v1/activity/ba-cn.json) | 2026-10 | 3 | 0 / 0 / 3 | 三周前 |
| [ba-global.json](https://starrailassistant.top/api/v1/activity/ba-global.json) | 2026-10 | 1 | 0 / 0 / 1 | 三周前 |
| [ba-jp.json](https://starrailassistant.top/api/v1/activity/ba-jp.json) | 2026-10 | 1 | 0 / 0 / 1 | 三周前 |
| [end.json](https://starrailassistant.top/api/v1/activity/end.json)，仅评估额外接口 | 1.6 | 14 | 8 / 5 / 1 | 三日前 |
| [ak.json](https://starrailassistant.top/api/v1/activity/ak.json)，仅评估额外接口 | 2026-10 | 3 | 0 / 0 / 3 | 四日前 |

可见 JSON 都是对象，含版本字段和 activities 数组；所见条目均有可读名称及可解析结束时间，end/ak 可见 kind 字段。该结果只验证结构可读，不代表公告时间已逐一核实，也不覆盖官方文档里的所有可选字段。

BA 国服快照最晚结束于 **2026-10-29 14:00:59**，国际服于 **2026-10-13 10:00:59**，日服于 **2026-10-21 10:00:59**；ak 的三条均于 **2026-10-30 03:59:59** 结束。浏览缓存老旧和源数据老旧尚不能区分；上线前必须从部署环境重新拉取并核查。当前不能承诺这四个快照能产出正在进行的活动提醒。[国服快照](https://starrailassistant.top/api/v1/activity/ba-cn.json)、[国际服快照](https://starrailassistant.top/api/v1/activity/ba-global.json)、[日服快照](https://starrailassistant.top/api/v1/activity/ba-jp.json)、[方舟快照](https://starrailassistant.top/api/v1/activity/ak.json)

### 4.3 AKEData 与 PRTS

| 对象 | 本次结果 | 能确认什么 | 不能确认什么 |
| --- | --- | --- | --- |
| AKEData 首页 | 浏览服务可读 HTML；前端显示加载数据和请求源 | 网站存在该数据源配置与延迟提示 | manifest 当前版本、表数据结构/体积、当前活动数 |
| AKEData manifest | 本机 DNS 失败，浏览服务报不可访问 | 当前研究环境未取得 JSON | HTTP 状态、当前版本、是否更新、接口线上健康 |
| PRTS 模板文档 | 浏览服务可读（缓存三日前） | 模板字段及语义信息边界 | ask 当前响应、最新活动全集 |
| PRTS ask | 浏览服务报不可访问；本机一次低频请求 DNS 失败 | 无有效 API 响应 | WAF 是否触发、HTTP 状态、分页实况、当前有效活动数 |

未下载 AKEData 的中文文本表与角色表，未高频重试 PRTS，未抓取活动封面。源站连通性验收应在获准实施后的部署网络执行，并把结果单独记录；不能把上表空缺补写成成功或失败状态。

## 5. README 与实现差异、具体缺陷

以下为静态调用链核查；“可推导”表示从源码可构造触发条件，未运行上游复现。

| 问题 | 证据与实际影响 | 移植决策 |
| --- | --- | --- |
| AKEData 未处理 304（可推导） | 通用 HTTP 客户端 304 只返回 not_modified=True/data=None；AKEData manifest 直接 _pick_version(manifest.data)，会将正常未修改记成“内容异常，使用缓存”。_table 同样把 data=None 变成空字典。参见 [base.get_json](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L377-L428)、[manifest 分支](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L123-L151)、[表解析](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L243-L248)。SRA 已有显式304分支，不能把问题泛化到全部源。 | 304 必须返回已验证缓存实体；无缓存的304算错误。缓存键/验证器持久化。 |
| AKEData 清理前缀错误（可推导） | _cleanup_cache 用 cache_filename(“akedata_table_版本_”) 构造前缀，但 cache_filename 会追加 .json；真实表文件名在下划线后还有表名，无法 startswith 这个带 .json 的“前缀”。成功抓取后会把当前原始表缓存也删掉，只保留精简快照/manifest。参见 [清理](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L406-L426)、[文件名生成](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L334-L337)。 | 用结构化缓存键或精确文件清单清理；验证当前版本仍保留。 |
| README“大版本才重下表”与调用不符 | fetch 以 version.id 判断是否拉表；变化时拉所有表，只有文本缓存 scope 以 gameVersion 共享。注释不能替代实际条件。[fetch](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L144-L171)、[拉表](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L203-L241) | 明确区分 manifest 修订与游戏版本，按表内容校验/ETag复用，不能固定忽略所有 hotfix。 |
| AKEData 多区间丢失 | _first_time_range 只取 timeRangeList[0]；后续限时区间不会成为独立发生实例。[区间选择](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/akedata.py#L75-L92) | 模型明确 occurrence；逐有效区间解析或明确报未支持，不能静默只取第一段。 |
| PRTS 宣称 ETag，实际关闭 | README/文件头提 ETag，但请求显式 use_etag=False；每轮通常1次并非上限，代码最多5次。PRTS_PAGE_LIMIT=100 定义后没有用于 query。[请求](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L157-L203)、[常量](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L52-L63) | 采用实际可验证的分页契约；显式limit；分页截断/错误作为不完整快照。未取得线上响应前不宣称其分页一定失效。 |
| PRTS 不做兑换截止提醒 | 兑换结束只是 extra_times；相关性筛选、is_ended 和倒计时都使用 end_time，因此活动关卡结束后商店仍开放也不会继续提醒。[转换](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/prts.py#L217-L249)、[结束判断](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L199-L213)、[提醒](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/reminder_service.py#L197-L218) | 第一版明确“提醒活动结束，兑换时间仅展示”；如要兑换提醒需独立时间里程碑和状态。 |
| 紧急每日上限是逐活动 | README写“每个目标每日上限”，实际 daily_count 存在每目标每活动状态里，轮询逐活动 mark_sent。不同活动交错进入可让同一目标一天收到超过3条消息。[状态](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/urgent_dm_service.py#L52-L77)、[判断/计数](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/urgent_dm_service.py#L212-L284)、[轮询写入](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/polling_service.py#L371-L416) | 每目标本地日计数按真正发送消息数累加；活动确认另存。 |
| 指定活动确认失败会确认全部 | _ack_now 对指定scope零命中后，直接用空scope再确认一次。输入错活动名可能停止所有活动提醒。[入口](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/main.py#L930-L945) | 无命中反馈候选名，不扩大作用范围。 |
| 状态“读-改-整份保存”竞态 | run_cycle 持_cycle_lock，但订阅概览不持该锁；二者各自 get_notify_state 后跨网络等待，再分别 save_notify_state 全量覆盖。保存锁只保护写入时刻，不覆盖整个读改写过程；确认与紧急轮询也可类似交错。[轮询](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/polling_service.py#L183-L300)、[概览](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/polling_service.py#L420-L474)、[save](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/subscription_service.py#L305-L349) | 用SQLite事务按条更新；网络发送在事务外，结果落库用版本/唯一键；避免面板和bot多进程互相覆盖。 |
| “发送失败不丢失”有边界 | 状态只在发送成功后记录是正确的，但发送成功与保存之间崩溃可重发；失败后直到活动结束则不会补发。不能承诺 exactly-once 或送达用户已读。[发送成功后记账](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/polling_service.py#L275-L300)、[结束即跳过](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/reminder_service.py#L197-L200) | 承诺至少重试与已成功记录不重复；不虚构OneBot端到端恰好一次。 |
| 合并转发的封面预算逐节点重算 | 普通合并消息一次 resolve_parts；转发路径 _build_forward_nodes 对每个活动分别 resolve_parts，预算限制因此每节点重新开始，不能保证整条转发5MB。[resolve_parts](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/delivery_service.py#L82-L125)、[转发节点](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/delivery_service.py#L238-L258) | 先按整个游戏批次计算共享预算，再拆节点。 |
| 封面下载上限不是读取上限 | 先client.get取得完整response.content，再判断MAX_DOWNLOAD_BYTES，大文件在检查前已进内存。[下载](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/cover_service.py#L182-L206) | 流式下载并在读取中限制字节；内容类型/解码像素上限，超限降级链接。 |

其他边界也应写进新实现契约：SRA 改名/改开始时间会改变 ID；没有开始时间的活动被视为已开始；阈值恰好等于7天/3天/1天也会命中，文案“不足”与包含等号不完全一致；源 JSON 结构异常有些路径返回空列表而不是异常；精简缓存没有统一最大可用年龄，不能永久用旧缓存给用户“正常”状态。[SRA ID](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/sra.py#L121-L144)、[活动状态](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L199-L213)、[阈值](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/services/reminder_service.py#L133-L147)、[缓存回退](https://github.com/XinDuKW/astrbot_plugin_game_event_due/blob/daeb265ef42b7a576cf70f646d2859a68ec01fd6/sources/base.py#L377-L428)

## 6. 对本项目的移植建议

建议模块落在现有 nonebot 目录：纯模型/时间/别名/提醒决策、三个source、服务编排、SQLite store、消息构造，以及薄 plugins 命令/生命周期接线。面板沿用 nonebot/dashboard.py 与 dashboard.html 的认证、路由和外观；不引入 AstrBot、另一套Web服务或独立插件页面框架。本项目目录与边界为本次本地只读调查所得。

SQLite 应统一保存全局配置、会话订阅、活动快照/源健康、提醒状态、紧急目标/日计数、面板刷新请求。bot 拥有拉取与发送职责；dashboard 修改配置或排队刷新请求，不在独立进程直接发送消息。配置修订号、唯一键和事务防止双进程覆盖；网络操作不持长事务。

建议保留的规则：订阅时建立基线、只发当前最紧急档位、发送成功才推进状态、按游戏合并、新活动限量、UTC+8 aware时间、失败降级文字与链接。建议修正的规则：完整快照验证、缓存时效、结束/兑换语义、确认范围、目标日上限、整条消息预算、并发落库。

默认30分钟轮询，仅拉取已订阅/已查询所需游戏，PRTS全局单实例至少20秒间隔并遵循Retry-After；查询共享在途请求和5分钟短缓存，管理员强刷也服从限流。对每源独立记录最后成功、最后尝试、错误、内容时间范围、缓存年龄与验证器。8个SRA游戏不应因一个失败一起失效；源断网时只展示带时间标记的缓存，超过约定新鲜度后暂停新发现推送，查询清楚标注。

AKEData 表缓存应先下载验证完整版本再切换；不应仅凭“版本号没变”推断活动配置永远不变。PRTS分页总数、重复offset、截断、API错误体与空结果分开处理。封面需在游戏批次统一预算；转发失败后普通消息、图片失败后纯文本加链接，均保留逐批结果。用户输入不能变成可请求的任意URL。

紧急私聊默认关闭；开启后只围绕明确配置目标，停止/确认定位发送者本人，指定活动没有命中时不扩大范围。群管理权限采用本项目OneBot群角色与superuser规则，面板继续认证；真实群/私聊投递测试应单独获得明确目标授权。本次没有发送外部消息。

## 7. 工作量与验证策略

下列为独立实现的规划估算，取决于接口验收与现有面板改造程度，不是已完成工作量：

| 工作包 | 主要产物 | 估算 |
| --- | --- | --- |
| 模型、协议、缓存与源健康 | 三源适配、时间/ID/完整性验证、限流与过期状态 | 1.5–2.5 人日 |
| 订阅与提醒状态 | SQLite schema/事务、基线、档位、过滤、重启重试 | 1–1.5 人日 |
| NoneBot命令与投递 | 10game、权限、封面、转发降级、生命周期 | 1–1.5 人日 |
| 管理面板 | 配置/订阅/预览/健康/刷新请求 | 0.5–1 人日 |
| 默认关闭的紧急私聊 | 本人确认、目标日预算、静默/重复、状态显示 | 0.5–1 人日 |
| 验证与文档 | 源样本、集成测试、双进程/重启、批准的投递验收 | 1–1.5 人日 |
| 合计 | 不含等待外部源恢复、授权澄清和上线观察 | **6–9.5 人日** |

验证以用户可见行为为核心：

1. **源契约测试：**本地最小fixtures覆盖正常/空/错schema/缺字段/坏时间/常驻/多区间、SRA kind和可选pools兼容、AKE关联表缺失、PRTS分页与错误体；不运行上游实现。
2. **提醒决策：**恰好7/3/1天、刚发现剩5小时、未来未开始、跨年、结束延期、活动改名、过滤变更、重新订阅、关闭/恢复阈值、失败后重试。用冻结时钟表达精确边界。
3. **持久化与并发：**bot轮询同时面板改订阅、确认同时发送、重启后不刷旧活动、发送成功但记账前异常的重复边界、刷新请求只消费一次。
4. **消息：**整个游戏/转发共享图预算、重复图URL、超大图、下载/解码失败、OneBot拒图/拒转发、部分批次成功；确认失败不提前记状态。
5. **紧急私聊：**目标每日3条消息的硬上限、跨日重置、跨零点静默、不同活动交错进入、指定名字无命中、停止本人，且默认关闭。
6. **部署网络只读验收：**每源一次受控请求，记录确切时间/HTTP/响应头/结构/总数/有效数/内容日期。验证ETag/304需在允许频率内第二次请求；PRTS严格间隔，不猛抓；AKE仅下载必需表，保存可复用样本。
7. **真实投递验收：**仅在用户确认实施并指定测试目标后，少量消息验证图片/转发和失败降级。测试不能把“API返回message_id”写成“用户已读”。

## 8. 本次已核验与未核验范围

**已核验：**固定提交目录树与metadata；README、配置schema、main、WebUI、source/registry/base、polling/reminder/subscription/urgent/delivery/cover等关键实现；SRA官方协议当前页面；8个原支持游戏与额外ak/end的可见JSON快照；AKEData公开首页；PRTS活动模板字段；本机SRA/AKEData/PRTS DNS失败事实。源码行号均链接固定SHA，可复查。

**未核验：**上游运行效果/测试通过率；部署环境三源实时HTTP状态、响应头和ETag行为；AKEData manifest/各表实际当前值与活动数；PRTS ask响应和分页实况；各活动与游戏官方公告逐项一致性；BA/ak全过期是浏览缓存还是源站未更新；封面实际可下载性和压缩率；QQ/NapCat主动私聊/转发兼容性；上游作者未公开的授权意图。

报告只能支持“可行并值得按协议独立实现”的设计决策，不能替代上述上线验收。


## 9. 实施阶段真实网络补充（2026-10-31）

这次补充记录实施阶段从开发机以 `httpx` 对固定公开来源执行的只读 HTTP 请求，覆盖实际响应和归一化解析；不覆盖生产部署网络，不发送机器人消息，不下载封面和 CharacterTable。它是比第 4 节更晚取得的新证据，不把先前浏览缓存快照改写为当时已经直连成功。

- AKEData 与 PRTS 完整拉取成功时间分别为 **2026-10-31 14:49:35 +08:00**、**14:49:36 +08:00**。
- 八个 SRA 游戏入口逐一请求一次，开始时间为 **2026-10-31 14:51:34 +08:00**；八项均成功。下表的“当前有效”指结束时间晚于该次检查时间，包含进行中与未开始；未套用订阅关键词和 45 天预告窗口。
- 只读检查的缓存与逐源结果保存在忽略目录 `.tmp/game-source-live-check/`，不是生产数据目录。

| 来源 | game_id | 本次版本 | 解析总数 | 进行中 | 未开始 | 当前有效 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| SRA | sr | 4.6 | 15 | 12 | 1 | 13 |
| SRA | ys | 7.1 | 8 | 7 | 0 | 7 |
| SRA | zzz | 3.3 | 9 | 4 | 5 | 9 |
| SRA | ww | 3.7 | 12 | 9 | 1 | 10 |
| SRA | nte | 1.4 | 12 | 10 | 0 | 10 |
| SRA | ba-cn | 2026-10 | 4 | 1 | 3 | 4 |
| SRA | ba-global | 2026-10 | 1 | 1 | 0 | 1 |
| SRA | ba-jp | 2026-10 | 2 | 1 | 1 | 2 |
| AKEData | endfield | 1.6.4@11343132-9（启用寻访） | 145 | 15 | 3 | 18 |
| PRTS | arknights | 查询截止日 2026-10-24 | 9 | 4 | 4 | 8 |

AKEData 总数是每个有效 `timeRangeList` 区间归一化、按活动身份与开始时间去重后的期次数，含历史条目；它不等于 145 个当前活动。PRTS 一次 `ask` 返回 9 项且没有续页；线上续页、限流拒绝和异常回退的边界由离线 MockTransport 测试覆盖，未为验收主动制造高频或失败请求。

真实协议核对修正了研究阶段的概括：

1. AKEData manifest 的 `latest` 实测为 `1.6.4@11343132-9`，对应 `tableCfgPath=public/1.6.4/11343132-9/TableCfg`。Activity/TimeRange/ActivityTag/GachaCharPool 和中文文本表完整联接成功。文本引用实际使用 `{id,text}`，时间表含 `2026/10/15 7:00:00` 这类斜杠日期。
2. 实测 GachaCharPoolTable 的 17 行均未提供 `clientTopTimeId`；限时寻访可经默认 `time_{pool_id}` 关联时间表，常驻、新手和未排期卡池可能没有对应时间行，跳过此类无明确结束时间的条目。无需新增角色表。活动 `tabImg` 是无扩展名资源键时，使用固定 `public/images/assets/beyond/dynamicassets/gameplay/ui/sprites/activity/{key}.png` 路径；本次未下载图片验证视觉内容。
3. PRTS 当前模板实际写入的语义属性是 `活动开始时间`、`活动结束时间`、`兑换结束时间`、`名称`、`名称缩短`、`标题图文件名`、`官网链接`。实现的 `ask` 查询使用这些真实名称，并显式 `limit=100`；不能把第 3 节“开始/结束日期”的概括直接当成属性名。
4. AKEData HTTP 响应实测提供 ETag 与 Last-Modified。客户端将 manifest 顶层 `updatedAt/sharedRevision` 与所选版本一并纳入修订签名，同游戏版本发生修订时重新核验中文表；重启恢复、304 全实体、坏 200 不污染验证器和部分版本保留旧完整快照均有离线测试。

八项 SRA 中三项 BA 来源本次均有进行中活动，与第 4 节浏览缓存中全部已过期的旧快照不同。此处只记录本次源响应，不承诺之后的数据新鲜度。上线前仍需从**实际部署网络**复核三源连通性、活动与公告时间的一致性，以及消息封面下载；开发机本次成功不能替代部署验收。
