from tests.helpers import TempTest, commit, init, sh_git
from untwine_cli import transform


class TextTest(TempTest):
    def test_include_rules(self):
        src = (
            '#include "pxr/pxr.h"\n'
            '#include "pxr/base/tf/token.h"\n'
            '#include "pxr/usd/sdf/path.h"\n'
            '#include "pxr/external/boost/python/def.hpp"\n'
            '#include "pxr/foo/own.h"\n'
            '  #  include "pxr/base/arch/api.h"\n'
            '///  #include "pxr/base/arch/export.h"\n'
            '#include <string>\n'
            'const char* s = "pxr/base/tf/token.h";\n'
        )
        self.assertEqual(transform.transform_text(src, "foo"), (
            '#include <pxr/foo/pxr.h>\n'
            '#include <pxr/tf/token.h>\n'
            '#include <pxr/sdf/path.h>\n'
            '#include <pxr/boost/python/def.hpp>\n'
            '#include <pxr/foo/own.h>\n'
            '  #  include <pxr/arch/api.h>\n'
            '///  #include <pxr/arch/export.h>\n'
            '#include <string>\n'
            'const char* s = "pxr/base/tf/token.h";\n'
        ))

    def test_namespace_macros(self):
        src = "PXR_NAMESPACE_OPEN_SCOPE\nPXR_NAMESPACE_CLOSE_SCOPE\nPXR_NAMESPACE_USING_DIRECTIVE\nPXR_NS::X\n"
        self.assertEqual(transform.transform_text(src, "tf"),
                         "TF_NAMESPACE_OPEN_SCOPE\nTF_NAMESPACE_CLOSE_SCOPE\nTF_NAMESPACE_USING_DIRECTIVE\nPXR_NS::X\n")

    def test_pxrpythonsubst(self):
        self.assertEqual(transform.transform_text("#!/pxrpythonsubst\n#\n# Copyright\nimport x\n", "tf"),
                         "# Copyright\nimport x\n")
        self.assertEqual(transform.transform_text("#!/usr/bin/env python\n#\nimport x\n", "tf"),
                         "#!/usr/bin/env python\n#\nimport x\n")

    def test_whitespace_preserved(self):
        src = '#endif // PXR_BASE_ARCH_ALIGN_H \r\n#include "pxr/base/arch/defines.h" \n\n'
        self.assertEqual(transform.transform_text(src, "arch"),
                         '#endif // PXR_BASE_ARCH_ALIGN_H \r\n#include <pxr/arch/defines.h> \n\n')

    def test_binary_passthrough(self):
        data = b"\x89PNG\r\n\x1a\n\x00#include \"pxr/pxr.h\"\xff"
        self.assertEqual(transform.transform_bytes(data, "foo"), data)

    def test_special_is_identity(self):
        data = b'#include "pxr/pxr.h"\n'
        self.assertEqual(transform.transform_bytes(data, "boost", special=True), data)


class PathTest(TempTest):
    def test_default_rules(self):
        rules = transform.DEFAULT_RULES
        self.assertEqual(transform.rule_target(rules, "tf", "testenv/a/b.py")[0], "test/a/b.py")
        self.assertEqual(transform.rule_target(rules, "tf", "wrapToken.cpp")[0], "src/python/wrapToken.cpp")
        self.assertEqual(transform.rule_target(rules, "tf", "module.cpp")[0], "src/python/module.cpp")
        self.assertEqual(transform.rule_target(rules, "tf", "__init__.py")[0], "src/python/__init__.py")
        self.assertEqual(transform.rule_target(rules, "tf", "token.h")[0], "src/pxr/tf/token.h")
        self.assertEqual(transform.rule_target(rules, "tf", "sub/x.h")[0], "src/pxr/tf/sub/x.h")

    def test_learn_from_restructure(self):
        repo = init(self.tmp / "repo")
        big = "".join(f"line {i}\n" for i in range(40))
        commit(repo, {"bar.h": big, "tiny.h": '#include "pxr/pxr.h"\n', "CMakeLists.txt": "x\n",
                      "keep.txt": "k\n", "stub.cpp": "\n"}, "upstream")
        (repo / "src/pxr/foo").mkdir(parents=True)
        sh_git(repo, "mv", "bar.h", "src/pxr/foo/bar.h")
        sh_git(repo, "rm", "-q", "tiny.h", "stub.cpp")
        (repo / "src/pxr/foo/tiny.h").write_text("#include <pxr/foo/pxr.h>\n")
        restructure = commit(repo, {}, "Restructure")
        pm = transform.learn(repo, restructure, "foo")
        self.assertEqual(pm.standalone("bar.h"), ("src/pxr/foo/bar.h", "learned"))
        self.assertEqual(pm.upstream("src/pxr/foo/tiny.h"), "tiny.h")
        self.assertEqual(pm.upstream("CMakeLists.txt"), "CMakeLists.txt")
        self.assertIsNone(pm.upstream("src/pxr/foo/stub.cpp"))
        self.assertEqual(pm.standalone("new.h"), ("src/pxr/foo/new.h", r"rule (.*)"))

    def test_extra_rules_take_precedence(self):
        pm = transform.PathMap("gf", {}, (("(.*)\\.template\\.(h|cpp)", r"resources/templates/\1.template.\2"),
                                          *transform.DEFAULT_RULES))
        self.assertEqual(pm.standalone("vec.template.h")[0], "resources/templates/vec.template.h")
