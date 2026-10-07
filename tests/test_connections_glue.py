"""Connection types for AWS Glue: roles and script and crawl buckets. Each in
all three states (connections_base)."""

import unittest

from .fakes import FakeSession, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION


class GlueConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _job(**job):
        session = FakeSession({"glue": {"get_jobs": {"Jobs": [dict({"Name": "etl", "CreatedOn": recent()}, **job)]},
                                        "get_job_runs": {"JobRuns": []}}})
        return audit.collect_glue_jobs(session, REGION)

    @staticmethod
    def _crawler(**crawler):
        session = FakeSession({"glue": {"get_crawlers": {"Crawlers": [
            dict({"Name": "lake", "CreationTime": recent()}, **crawler)]}}})
        return audit.collect_glue_crawlers(session, REGION)

    def test_gluejob_iamrole_role(self):
        self.assert_three_states("gluejob.iamrole.role",
                                 lambda: self._job(Role=f"arn:aws:iam::{ACCOUNT}:role/glue-role")
                                 + [make_row("IAMRole", "glue-role", region="global")], "etl", "glue-role")

    def test_gluejob_s3bucket_script(self):
        self.assert_three_states("gluejob.s3bucket.script",
                                 lambda: self._job(Command={"ScriptLocation": "s3://glue-scripts/etl.py"})
                                 + [make_row("S3Bucket", "glue-scripts")], "etl", "glue-scripts")

    def test_gluecrawler_iamrole_role(self):
        """A bare role name, which Glue accepts as well as an ARN."""
        self.assert_three_states("gluecrawler.iamrole.role",
                                 lambda: self._crawler(Role="crawl-role")
                                 + [make_row("IAMRole", "crawl-role", region="global")], "lake", "crawl-role")

    def test_gluecrawler_s3bucket_target(self):
        self.assert_three_states("gluecrawler.s3bucket.target",
                                 lambda: self._crawler(Targets={"S3Targets": [{"Path": "s3://raw-lake/events/"}]})
                                 + [make_row("S3Bucket", "raw-lake")], "lake", "raw-lake")


if __name__ == "__main__":
    unittest.main()
