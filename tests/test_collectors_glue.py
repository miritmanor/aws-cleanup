"""Collector tests for AWS Glue: jobs and crawlers."""

import unittest

from .fakes import FakeSession, ancient, audit, recent
from .collectors_base import REGION, only


class GlueCollectorTests(unittest.TestCase):
    def test_a_job_is_judged_by_its_last_run(self):
        session = FakeSession({"glue": {
            "get_jobs": {"Jobs": [{"Name": "etl", "Role": "glue-role", "CreatedOn": ancient(),
                                   "Command": {"Name": "glueetl", "ScriptLocation": "s3://scripts/etl.py"}}]},
            "get_job_runs": {"JobRuns": [{"StartedOn": recent(), "JobRunState": "SUCCEEDED"}]},
        }})
        row = only(audit.collect_glue_jobs(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("GlueJob", "usage"))
        self.assertTrue(row["flag"].startswith("ACTIVE"))
        self.assertIn("last run succeeded", row["description"])

    def test_an_old_job_that_never_ran_is_stale(self):
        session = FakeSession({"glue": {"get_jobs": {"Jobs": [{"Name": "etl", "CreatedOn": ancient()}]},
                                        "get_job_runs": {"JobRuns": []}}})
        self.assertEqual(only(audit.collect_glue_jobs(session, REGION))["flag"], "STALE (never run)")

    def test_a_scheduled_crawler_says_it_bills_every_run(self):
        session = FakeSession({"glue": {"get_crawlers": {"Crawlers": [{
            "Name": "lake", "CreationTime": ancient(), "DatabaseName": "raw",
            "Schedule": {"ScheduleExpression": "cron(0 1 * * ? *)", "State": "SCHEDULED"},
            "LastCrawl": {"StartTime": recent(), "Status": "SUCCEEDED"}}]}}})
        row = only(audit.collect_glue_crawlers(session, REGION))
        self.assertEqual(row["service"], "GlueCrawler")
        self.assertIn("bills on every run", row["notes"])


if __name__ == "__main__":
    unittest.main()
