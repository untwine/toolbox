import io
from contextlib import redirect_stdout

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo
from untwine_cli import cli, report, sync

TAG = "v26.11"


@needs_filter_repo
class ReportTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"include-conflict", "new-file"})
        sync.sync(self.world.m, TAG, ["pxr-foo"])
        self.report = report.collect(self.world.m, TAG, "pxr-foo")

    def test_counts_and_table(self):
        self.assertEqual(self.report.counts["auto"], 1)
        self.assertEqual(self.report.counts["clean"], 2)
        text = report.table(self.world.m, TAG, [self.report])
        self.assertIn("v26.08 → v26.11", text)
        self.assertIn("pxr-foo", text)
        self.assertIn("verified", text)
        self.assertIn("3 (2/1/0/0)", text)
        waiting = report.RepoReport("pxr-foo", 1, self.report.status, [], [], 0, checks="fail", waiting=["pxr-arch"])
        self.assertIn("waits pxr-arch", report.table(self.world.m, TAG, [waiting]))

    def test_detail(self):
        text = report.detail(self.report, self.world.m, TAG)
        self.assertIn("Release 26.11", text)
        self.assertIn("auto", text)
        self.assertIn("src/pxr/foo/bar.cpp", text)
        self.assertIn("transform(old upstream)", text)
        self.assertIn("baz.h", text)

    def test_pr_body(self):
        body = report.pr_body(self.report, self.world.m, TAG)
        self.assertIn("## Commit mapping", body)
        self.assertIn("## Range diff of Untwine commits", body)
        self.assertIn("## Version changes", body)
        self.assertIn("- [ ] new upstream file baz.h", body)
        self.assertIn("0.26.11", body)

    def test_cli_status(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--manifest", str(self.world.manifest_path), "status", "--no-github"]), 0)
            self.assertEqual(cli.main(["--manifest", str(self.world.manifest_path), "status", "pxr-foo", "--no-github"]), 0)
        self.assertIn("3 (2/1/0/0)", out.getvalue())
        self.assertIn("Untwine commits:", out.getvalue())
