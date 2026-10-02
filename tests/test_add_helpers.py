from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo
from untwine_cli import add, upstream


class RewriteTest(TempTest):
    def test_rename_respects_boundaries(self):
        text = ('project(pxr-ar)\nadd_library(ar)\n#include <pxr/ar/ar.h>\nArResolver pyAr AR_API PXR_AR_H\n'
                'arch archive Arch ARCH json Json\n')
        self.assertEqual(add.rename(text, "ar", "pcp"),
                         'project(pxr-pcp)\nadd_library(pcp)\n#include <pxr/pcp/pcp.h>\nPcpResolver pyPcp PCP_API PXR_PCP_H\n'
                         'arch archive Arch ARCH json Json\n')

    def test_rewrite_dependency_blocks(self):
        text = (
            'find_package(pxr-arch 0.26.8 EXACT REQUIRED)\n'
            'find_package(pxr-tf 0.26.8 EXACT REQUIRED)\n'
            'if(BUILD_PYTHON_BINDINGS)\n'
            '    find_package(pxr-boost 0.26.8 EXACT REQUIRED)\n'
            'endif()\n'
            '        if self.options.python_version:\n'
            '            self.options["pxr-tf"].python_version = self.options.python_version\n'
            '            self.options["pxr-boost"].python_version = self.options.python_version\n'
            '    "pxr-arch-dev==26.8.*",\n'
            '    "pxr-tf-dev==26.8.*",\n'
            '    "pxr-boost-dev==26.8.*",\n'
        )
        python_of = {"arch": False, "tf": True, "vt": True, "boost": True}
        self.assertEqual(add.rewrite_deps(text, ["arch", "tf", "boost"], ["arch", "vt", "boost"], python_of), (
            'find_package(pxr-arch 0.26.8 EXACT REQUIRED)\n'
            'find_package(pxr-vt 0.26.8 EXACT REQUIRED)\n'
            'if(BUILD_PYTHON_BINDINGS)\n'
            '    find_package(pxr-boost 0.26.8 EXACT REQUIRED)\n'
            'endif()\n'
            '        if self.options.python_version:\n'
            '            self.options["pxr-vt"].python_version = self.options.python_version\n'
            '            self.options["pxr-boost"].python_version = self.options.python_version\n'
            '    "pxr-arch-dev==26.8.*",\n'
            '    "pxr-vt-dev==26.8.*",\n'
            '    "pxr-boost-dev==26.8.*",\n'
        ))

    def test_openusd_license_keeps_only_its_own_section(self):
        bar = "=" * 60
        text = (f"{bar}\nOpenUSD\n{bar}\n\nTerms of the license \n  continued.\t\n\n\n"
                f"{bar}\nRapidJSON\n{bar}\n\nOther terms.\n")
        self.assertEqual(add.openusd_license(text),
                         f"{bar}\nOpenUSD\n{bar}\n\nTerms of the license\n  continued.\n")

    def test_notice_trims_whitespace(self):
        upstream = "Universal Scene Description   \r\nCopyright 2016 Pixar\r\n   \r\n\r\n\r\nPixar.\r\n"
        self.assertEqual(add.notice(upstream, "pcp"),
                         "Universal Scene Description\nCopyright 2016 Pixar\n\nPixar.\n\n"
                         + add.NOTICE_PARAGRAPH.format(lib="pcp"))

    def test_pxr_h_in(self):
        text = add.pxr_h_in("pcp", ["arch", "sdf", "pegtl", "boost"])
        self.assertIn("#include <pxr/arch/pxr.h>\n#include <pxr/sdf/pxr.h>\n", text)
        self.assertNotIn("pegtl", text)
        self.assertIn("#ifdef PXR_PYTHON_SUPPORT_ENABLED\n#include <pxr/boost/python/common.hpp>\n#endif\n", text)
        self.assertIn("    using namespace SDF_INTERNAL_NS;\n#ifdef PXR_PYTHON_SUPPORT_ENABLED\n"
                      "    using namespace BOOST_INTERNAL_NS;\n#endif\n}", text)
        self.assertIn("#define PCP_VERSION (PCP_MAJOR_VERSION * 10000 \\\n"
                      "                   + PCP_MINOR_VERSION * 100   \\\n", text)
        self.assertNotIn("namespace PCP_INTERNAL_NS {\n    using", add.pxr_h_in("pcp", []))

    def test_pxr_h_in_matches_a_real_one(self):
        expected = (
            "#define JS_VERSION (JS_MAJOR_VERSION * 10000 \\\n"
            "                  + JS_MINOR_VERSION * 100   \\\n"
            "                  + JS_PATCH_VERSION)\n"
        )
        self.assertIn(expected, add.pxr_h_in("js", ["arch", "tf"]))

    def test_messages(self):
        message = add.restructure_message("pcp", ["arch"], dropped_subst=True)
        self.assertTrue(message.startswith("Restructure the 'pcp' library as a standalone package.\n\n"))
        self.assertIn("internal-namespace imports", message)
        self.assertIn("pxrpythonsubst", message)
        self.assertTrue(message.rstrip().endswith("mandated by OpenUSD."))
        self.assertIn("bound to the library", add.restructure_message("pcp", [], dropped_subst=False))
        self.assertIn("moduleDeps.cpp", add.config_message(python=True))
        self.assertTrue(add.config_message(python=False).rstrip().endswith("a .gitignore."))


@needs_filter_repo
class DeriveTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)
        self.mirror = upstream.ensure_mirror(self.world.m)

    def test_locate(self):
        self.assertEqual(add.locate(self.mirror, "v26.08", "qux"), "pxr/base/qux")
        with self.assertRaisesRegex(add.AddError, "pxr/base/dup"):
            add.locate(self.mirror, "v26.08", "dup")
        with self.assertRaisesRegex(add.AddError, "nothing"):
            add.locate(self.mirror, "v26.08", "absent")

    def test_derive_and_missing(self):
        library = add.upstream_library(self.mirror, "v26.08", "pxr/base/qux")
        self.assertEqual(add.derive(self.world.m, self.mirror, "v26.08", "qux", library).pxr, ("arch", "foo"))
        zed = add.upstream_library(self.mirror, "v26.08", "pxr/base/zed")
        with self.assertRaisesRegex(add.AddError, "in this order: nope$"):
            add.derive(self.world.m, self.mirror, "v26.08", "zed", zed)

    def test_nearest_sibling(self):
        self.assertEqual(add.nearest_sibling(self.world.m, {"arch", "foo", "boost"}, "optional").name, "pxr-foo")
