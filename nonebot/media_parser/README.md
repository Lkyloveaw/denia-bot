# 链接解析器适配

基于 Zhalslar/astrbot_plugin_parser 的提交 `e226dfd2250b4855ad67c29da7965353ad9e1379`，原项目 MIT 许可证见 `LICENSE`。`core` 保留平台解析器、下载、卡片渲染和防抖逻辑；`transport.py` 与 `plugins/media_parser.py` 负责 OneBot 接线。

启动后会生成 `nonebot/data/media_parser/config.json`。管理页面的「链接解析」栏目可修改通用设置与各平台选项，保存后重启机器人生效。默认启用原模板中已注册的 B 站、抖音、快手、微博、小红书、小黑盒；其他平台可在管理页面启用。原模板里未注册的 jmcomic 条目不会加载。部分平台需配置 Cookie、代理或独立服务；请勿公开配置文件中的凭证。

群聊和私聊默认自动解析支持的链接或 JSON 分享卡片，群内会执行原版表情仲裁。针对其他机器人的 @ 不解析；引用解析默认关闭，可在配置中开启 `enable_reply_parse`。相同会话内的链接和资源默认 300 秒去重。解析插件与 MuiceBot 聊天闸门独立，解析不会计入聊天回复配额；@ 机器人时也可能触发聊天回复。

群管理员、群主或超级用户可在当前会话使用 `开启解析`、`关闭解析`；`blogin` 可扫码登录 B 站。群聊标识采用 `group:群号`，私聊采用 `private:QQ号`，适用于 `whitelist` 和 `blacklist`。

音视频合并需要 `ffmpeg` 和 `ffprobe`。可用 `media_tools_paths` 在配置中添加它们所在的目录，插件启动时会把目录加入进程 PATH；本机已配置为 Altair 附带的程序。SnowLuma 的合并转发、文件上传和表情仲裁是否可用还取决于 QQ 账号和运行环境。
