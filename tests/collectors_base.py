"""Shared by the test_collectors_* modules."""

REGION = "us-east-1"
ACCOUNT = "111122223333"


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def only(rows):
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}: {rows}"
    return rows[0]


def by_service(rows, service):
    return [r for r in rows if r["service"] == service]
