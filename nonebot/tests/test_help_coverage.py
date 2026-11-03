# 移植自 SX0YYYY/astrbot_plugin_statuscard v2.18.8（提交 fa6d0f5），原作者：烬辞。
# 本文件与上游同源衍生，按 AGPL-3.0 分发；详见 docs/third-party/statuscard-NOTICE.md
"""`.help` 覆盖度：所有插件指令都必须出现在指令表里。

这套检查是补出来的——`.help` 的分组早就预留了「解析 / 天气 / 话题与记忆」，
但对应插件一直没往表里注册，用户看到的是三个空分组；状态卡则整块没进表。
以后新增指令忘了注册，这里会直接红。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

try:  # 整份用例同进程跑时复用已初始化的 NoneBot（见 tests/_bootstrap.py）
    from tests._bootstrap import ensure_nonebot
except ImportError:  # discover 以 tests 为顶层时模块名不带包前缀
    from _bootstrap import ensure_nonebot

ensure_nonebot()

import gate_logic as gl

# 导入即注册帮助行；这些插件都是「有聊天指令」的
import plugins.group_topics  # noqa: F401
import plugins.media_parser  # noqa: F401
import plugins.memory  # noqa: F401
import plugins.model_pref  # noqa: F401
import plugins.statuscard  # noqa: F401
import plugins.weather  # noqa: F401
import plugins.epic_free  # noqa: F401
import plugins.disaster_warning  # noqa: F401
import plugins.game_events  # noqa: F401
import plugins.endfield  # noqa: F401
from plugins import media_parser, statuscard


class HelpCoverageTest(unittest.TestCase):
    """覆盖判定分三种，缺一种都会误报：

    - 精确一条（`查看状态`）
    - 带参数的一行（`天气 <城市>` 覆盖 `天气`、`限流 查看 [群号]` 覆盖 `限流 查看`）
    - 写在说明里的别名（`blogin` 的说明里提到 `登录b站`）
    """

    def setUp(self):
        self.rows = gl.all_help_rows()
        self.commands = [cmd for cmd, _ in self.rows]
        self.descriptions = [desc for _, desc in self.rows]

    def covered(self, command):
        if command in self.commands:
            return True
        if any(row.startswith(command + ' ') for row in self.commands):
            return True
        if any(row.startswith(command) for row in self.commands):
            # `群话题 开/关/状态/…` 这种把多个子指令合并写成一行的
            return True
        return any(command in desc for desc in self.descriptions)

    def describe(self, command):
        if command in self.commands:
            return dict(self.rows)[command]
        for row, desc in self.rows:
            if row.startswith(command + ' '):
                return desc
        return ' '.join(desc for desc in self.descriptions if command in desc)

    def test_no_duplicate_rows(self):
        duplicated = sorted({cmd for cmd in self.commands if self.commands.count(cmd) > 1})
        self.assertEqual(duplicated, [], '同一条指令不能出现两次')

    def test_every_group_has_entries(self):
        grouped = gl.grouped_help_rows(self.rows)
        empty = [name for name, rows in grouped if not rows]
        self.assertEqual(empty, [], '分组存在却没有条目，等于菜单上多了一块空白')
        self.assertEqual({name for name, _ in grouped}, {name for name, _ in gl.HELP_GROUPS},
                         '声明的每个分组都要有内容')

    def test_statuscard_commands_are_all_listed(self):
        missing = [cmd for cmd in statuscard.COMMANDS if not self.covered(cmd)]
        self.assertEqual(missing, [], '状态卡的每个指令（含别名）都要能在 .help 里看到')

    def test_parser_commands_are_all_listed(self):
        missing = [cmd for cmd in media_parser.CONTROL_COMMANDS if not self.covered(cmd)]
        self.assertEqual(missing, [], '解析器控制指令（含别名）都要能在 .help 里看到')

    def test_other_plugin_commands_are_listed(self):
        expected = (
            'help', 'kb', '限流 查看', '限流 默认', '限流 群内只@',
            '模型 查看', '模型 首选', '模型 次选',
            '开启解析', '关闭解析', 'blogin', '登录b站',
            '天气', '天气预报',
            '群话题 查看', '群话题 开',
            '记忆 查看', '记忆 忘记', '记忆 纠正',
            '查看状态', '状态卡', '状态卡诊断', '测试通知', '立即检测', '预览日报', '预览周报',
            '地震列表', '气象预警', '台风', '海啸', '灾害源状态',
        )
        missing = [cmd for cmd in expected if not self.covered(cmd)]
        self.assertEqual(missing, [], f'这些指令没进 .help：{missing}')

    def test_disaster_commands_are_all_listed(self):
        from plugins import disaster_warning
        missing = [cmd for cmd in disaster_warning.COMMANDS if not self.covered(cmd)]
        self.assertEqual(missing, [], f'灾害预警指令没进 .help：{missing}')

    def test_admin_commands_are_marked(self):
        """只有主人能用的指令，说明里要写清楚，否则普通群友会反复试。"""
        admin_only = ('限流 查看', '模型 查看', '开启解析', '关闭解析', 'blogin',
                      '查看状态', '预览日报')
        for command in admin_only:
            self.assertIn('主人', self.describe(command), f'{command} 应标注仅主人可用')


if __name__ == '__main__':
    unittest.main()
