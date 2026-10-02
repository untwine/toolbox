from tests.fake_world import config_files
from tests.helpers import TempTest, write
from untwine_cli import versions


class VersionsTest(TempTest):
    def test_bump_and_leftovers(self):
        write(self.tmp, config_files("v26.08"))
        changed = versions.bump(self.tmp, "v26.08", "v26.11")
        self.assertEqual(sorted(changed), ["CMakeLists.txt", "README.md", "cmake/pxr-foo-config.cmake.in",
                                           "conanfile.py", "pyproject.toml"])
        self.assertEqual({rel: (self.tmp / rel).read_text() for rel in config_files("v26.11")}, config_files("v26.11"))
        self.assertEqual(versions.leftovers(self.tmp, "v26.08"), [])
        self.assertIn('"pxr-tbb==2023.1.0.*"', (self.tmp / "pyproject.toml").read_text())

    def test_same_tag_is_a_no_op(self):
        write(self.tmp, config_files("v26.08"))
        self.assertEqual(versions.bump(self.tmp, "v26.08", "v26.08"), [])

    def test_leftovers_reported(self):
        write(self.tmp, {"README.md": "see 26.8 and 0.26.8 and v26.08 but not 26.80 or 2026.8\n"})
        self.assertEqual(len(versions.leftovers(self.tmp, "v26.08")), 1)
