"""
Tests for config.py: what is excluded from counting, and why.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover tests
"""
import os
import unittest

from usemeup import config


class Exclusion(unittest.TestCase):
    def test_refresh_folder_slug_matches_claude_code_naming(self):
        # Claude Code turns every non-alphanumeric character into "-".
        self.assertEqual(config.project_slug("/Users/you/.usemeup/refresh"),
                         "-Users-you--usemeup-refresh")
        self.assertEqual(config.project_slug("/Users/you/Library/Application Support/x_y"),
                         "-Users-you-Library-Application-Support-x-y")

    def test_the_tools_own_refresh_pings_are_excluded(self):
        slug = config.project_slug(config.REFRESH_CWD)
        self.assertTrue(config.excluded(os.path.join(config.CLI_ROOT, slug)))
        self.assertTrue(config.excluded(os.path.join(config.CLI_ROOT, slug, "abc.jsonl")))
        self.assertTrue(config.excluded(os.path.join(config.CLI_ROOT, slug, "abc", "subagents")))

    def test_work_inside_a_checkout_of_this_repo_is_counted(self):
        # The old substring rule dropped all of these. None may be excluded.
        for p in ("-Users-you-Developer-usemeup", "-Users-you-code-usemeup",
                  "-Users-you-Developer-usemeup-refresh-notes", "-home-you-aiusage"):
            self.assertFalse(config.excluded(os.path.join(config.CLI_ROOT, p, "abc.jsonl")), p)

    def test_cowork_trees_are_never_excluded_by_accident(self):
        p = ("/Users/you/Library/Application Support/Claude/local-agent-mode-sessions/"
             "org/ws/local_123/.claude/projects/-Users-you-Library-outputs/session.jsonl")
        self.assertFalse(config.excluded(p))


class Synthetic(unittest.TestCase):
    def test_placeholder_model_is_not_a_call(self):
        self.assertTrue(config.synthetic("<synthetic>"))
        self.assertFalse(config.synthetic("claude-opus-5"))
        self.assertFalse(config.synthetic(None))


if __name__ == "__main__":
    unittest.main()
