"""The raw capture file is written as it goes and describes exactly one scan."""

import json
import os
import tempfile
import unittest

from aws_resource_audit.collect import raw_capture


class RawCaptureTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(raw_capture.close)
        self.path = os.path.join(self.tmp.name, "aws_raw_capture.jsonl")

    def _lines(self):
        with open(self.path) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_it_records_the_fields_a_row_can_be_joined_on(self):
        raw_capture.start(self.path)
        raw_capture.record("EC2Instance", "us-east-1", "i-123",
                           {"InstanceId": "i-123", "State": {"Name": "running"}})
        raw_capture.close()
        self.assertEqual(self._lines(), [{
            "service": "EC2Instance", "region": "us-east-1",
            "resource_id": "i-123",
            "raw": {"InstanceId": "i-123", "State": {"Name": "running"}},
        }])

    def test_one_line_per_resource(self):
        """JSON Lines, not one array: a truncated file is still every resource
        captured before the truncation."""
        raw_capture.start(self.path)
        for n in range(3):
            raw_capture.record("S3Bucket", "us-east-1", f"bucket-{n}", {"Name": f"bucket-{n}"})
        raw_capture.close()
        self.assertEqual([line["resource_id"] for line in self._lines()],
                         ["bucket-0", "bucket-1", "bucket-2"])

    def test_each_record_is_on_disk_before_the_scan_ends(self):
        """The property the whole design is for. Read the file WITHOUT closing
        it: a scan that dies here must still have left what it had captured."""
        raw_capture.start(self.path)
        raw_capture.record("DynamoDBTable", "us-east-1", "Bakery-stores", {"TableName": "x"})
        self.assertEqual(len(self._lines()), 1)
        raw_capture.record("DynamoDBTable", "us-east-1", "Bakery-products", {"TableName": "y"})
        self.assertEqual(len(self._lines()), 2)

    def test_a_new_scan_discards_the_previous_one(self):
        """A second start() truncates: the file describes one scan."""
        raw_capture.start(self.path)
        raw_capture.record("S3Bucket", "us-east-1", "from-the-old-scan", {})
        raw_capture.start(self.path)
        raw_capture.record("S3Bucket", "us-east-1", "from-the-new-scan", {})
        raw_capture.close()
        self.assertEqual([line["resource_id"] for line in self._lines()],
                         ["from-the-new-scan"])

    def test_recording_outside_a_scan_is_a_no_op(self):
        """Collectors call record() unconditionally. A test driving one directly
        never calls start(), and must not have to know that."""
        raw_capture.close()
        raw_capture.record("S3Bucket", "us-east-1", "nobody-is-listening", {})
        self.assertFalse(os.path.exists(self.path))

    def test_close_is_safe_twice(self):
        raw_capture.start(self.path)
        raw_capture.close()
        raw_capture.close()

    def test_a_payload_json_cannot_serialise_does_not_stop_the_scan(self):
        """Datetimes and Decimals serialise; a bad value costs a line, never the scan."""
        from datetime import datetime, timezone
        raw_capture.start(self.path)
        raw_capture.record("EC2Instance", "us-east-1", "i-456",
                           {"LaunchTime": datetime(2024, 1, 1, tzinfo=timezone.utc)})
        raw_capture.record("EC2Instance", "us-east-1", "i-789", {"Fine": True})
        raw_capture.close()
        captured = self._lines()
        self.assertEqual([line["resource_id"] for line in captured], ["i-456", "i-789"])
        self.assertIn("2024-01-01", captured[0]["raw"]["LaunchTime"])

    def test_an_unopenable_path_warns_rather_than_raising(self):
        """Losing a real scan because its optional notes could not be opened
        would be an absurd trade."""
        raw_capture.start(os.path.join(self.tmp.name, "no", "such", "dir", "x.jsonl"))
        raw_capture.record("S3Bucket", "us-east-1", "b", {})   # must not raise


if __name__ == "__main__":
    unittest.main()
