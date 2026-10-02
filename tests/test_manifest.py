import textwrap
from pathlib import Path

from tests.helpers import TempTest
from untwine_cli import manifest

SAMPLE = textwrap.dedent("""\
    workspace = ".."
    upstream_url = "git@example.com:OpenUSD.git"
    github_org = "untwine"

    [release]
    openusd = "v26.08"
    tbb = "2023.1.0"

    [repos.pxr-tbb]
    kind = "support"

    [repos.pxr-arch]
    upstream = "pxr/base/arch"

    [repos.pxr-tf]
    upstream = "pxr/base/tf"
    python = "optional"
    deps = ["arch"]

    [repos.pxr-js]
    upstream = "pxr/base/js"
    deps = ["arch", "tf"]
    extra_deps = { arch = "includes <pxr/arch/...> headers directly" }
    """)


class ManifestTest(TempTest):
    def load(self, text: str) -> manifest.Manifest:
        (self.tmp / "toolbox").mkdir(exist_ok=True)
        path = self.tmp / "toolbox" / "untwine.toml"
        path.write_text(text)
        return manifest.load(path)

    def test_load(self):
        m = self.load(SAMPLE)
        self.assertEqual(m.workspace, self.tmp)
        self.assertEqual(m.repos["pxr-js"].lib, "js")
        self.assertEqual(m.repos["pxr-js"].extra_deps, {"arch": "includes <pxr/arch/...> headers directly"})
        self.assertTrue(m.repos["pxr-tf"].has_python)
        self.assertEqual(m.repo_path("pxr-tf"), self.tmp / "pxr-tf")
        self.assertEqual(m.state_dir, self.tmp / ".untwine")
        self.assertEqual([r.name for r in m.libraries()], ["pxr-arch", "pxr-tf", "pxr-js"])

    def test_levels_and_selection(self):
        m = self.load(SAMPLE)
        self.assertEqual(manifest.levels(m), [["pxr-arch", "pxr-tbb"], ["pxr-tf"], ["pxr-js"]])
        self.assertEqual(manifest.level_map(m)["pxr-js"], 2)
        self.assertEqual([r.name for r in manifest.selected(m, [])], ["pxr-arch", "pxr-tf", "pxr-js"])
        self.assertEqual([r.name for r in manifest.selected(m, ["pxr-js", "pxr-arch"])], ["pxr-arch", "pxr-js"])
        self.assertEqual(manifest.transitive_deps(m, m.repos["pxr-js"]), {"arch", "tf"})
        with self.assertRaises(manifest.ManifestError):
            manifest.selected(m, ["pxr-nope"])

    def test_version_forms(self):
        self.assertEqual(manifest.version_forms("v26.08"), ("0.26.8", "26.8"))
        self.assertEqual(manifest.version_forms("v26.11"), ("0.26.11", "26.11"))
        with self.assertRaises(manifest.ManifestError):
            manifest.version_forms("26.08")

    def test_rejects_bad_manifests(self):
        cases = {
            "unknown dependency": SAMPLE.replace('deps = ["arch"]', 'deps = ["nope"]'),
            "cycle": SAMPLE.replace('upstream = "pxr/base/arch"', 'upstream = "pxr/base/arch"\ndeps = ["js"]'),
            "bad tag": SAMPLE.replace('"v26.08"', '"26.08"'),
            "unknown key": SAMPLE.replace('python = "optional"', 'python = "optional"\ncolour = "red"'),
            "missing upstream": SAMPLE.replace('upstream = "pxr/base/tf"\n', ""),
            "extra not in deps": SAMPLE.replace('{ arch = ', '{ gf = '),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(manifest.ManifestError):
                self.load(text)

    def test_real_manifest_loads(self):
        m = manifest.load(Path(__file__).resolve().parent.parent / "untwine.toml")
        self.assertEqual(manifest.levels(m)[0], ["pxr-arch", "pxr-boost", "pxr-pegtl", "pxr-tbb"])
        self.assertEqual(manifest.levels(m)[-1], ["pxr-pcp"])
