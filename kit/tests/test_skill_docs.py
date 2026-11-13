"""文档与代码一致性（计算型检查）：skill 的命令文档里提到的子命令，必须真的存在。
防止 SKILL 与 references 写着一套、代码是另一套。"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SKILL = KIT.parent / "references" / "commands.md"


def _help(script, args=()):
    r = subprocess.run([sys.executable, str(KIT / script), *args, "--help"], capture_output=True, text=True, cwd=KIT)
    return r.stdout + r.stderr


class SkillDocsMatchCode(unittest.TestCase):
    def test_every_documented_subcommand_exists(self):
        if not SKILL.exists():
            self.fail("references/commands.md 不存在")
        text = SKILL.read_text(encoding="utf-8")
        # 形如 "python3 products.py create ..." 或 "python3 loop.py gen ..."
        pairs = set(re.findall(r"python3 (\w+\.py) ([a-z][a-z-]+)", text))
        self.assertTrue(pairs, "commands.md 里没有找到命令")
        missing = []
        for script, sub in sorted(pairs):
            if not (KIT / script).exists():
                missing.append(f"{script} 文件不存在")
                continue
            if sub not in _help(script) and sub not in _help(script, [sub]):
                missing.append(f"{script} 没有子命令 {sub}")
        self.assertEqual(missing, [], "文档与代码不一致")

    def test_removed_entry_points_stay_removed(self):
        help_text = _help("loop.py")
        self.assertNotIn("select", help_text)
        self.assertNotIn("produce", help_text)
        self.assertFalse((KIT / "studio.py").exists())

    def test_board_explains_current_selection_commands(self):
        import dashboard
        html = dashboard.render({"research": {"groups": [], "cases": []}}, "测试")
        self.assertNotIn("selection.json", html)
        self.assertIn("products.py select", html)
        self.assertIn("intake.py plan --pick", html)


if __name__ == "__main__":
    unittest.main()
