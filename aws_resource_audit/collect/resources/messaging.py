"""Queues and topics: SQS and SNS."""


from ..arns import probable_resource_id_from_arn
from ..calls import epoch_seconds_to_dt
from ..cloudwatch import activity_note, cw_activity
from ..iam_policy import parse_policy_document
from ... import coverage
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_from_activity
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture


def collect_sqs_queues(session, region):
    """SQS queues. The signal is abandonment: undrained messages mean a missing consumer,
    and a RedrivePolicy target is a dead-letter queue, idle by design."""
    sqs = session.client("sqs", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    urls, page_error = paged(sqs, "list_queues", "QueueUrls", service="SQSQueue")
    if page_error and not urls:
        return [error_row("SQSQueue", region, "ERROR", page_error)]

    for url in urls:
        name = url.rsplit("/", 1)[-1]
        attrs_resp = safe_call(sqs.get_queue_attributes, QueueUrl=url, AttributeNames=["All"])
        attr_error = attrs_resp.get("__error__")
        attrs = attrs_resp.get("Attributes", {}) if not attr_error else {}

        created = epoch_seconds_to_dt(attrs.get("CreatedTimestamp"))
        modified = epoch_seconds_to_dt(attrs.get("LastModifiedTimestamp"))
        backlog = int(attrs.get("ApproximateNumberOfMessages") or 0)
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible") or 0)
        is_fifo = attrs.get("FifoQueue") == "true"

        tags_resp = safe_call(sqs.list_queue_tags, QueueUrl=url)
        tags = tags_resp.get("Tags", {}) if "__error__" not in tags_resp else {}

        evidence = cw_activity(
            cw, "AWS/SQS", "NumberOfMessagesSent",
            [{"Name": "QueueName", "Value": name}], stat="Sum"
        )
        last_used = evidence.last_activity
        inferred = not evidence.used
        ref_days = days_ago(last_used) if last_used else days_ago(created)

        notes = activity_note(evidence, "NumberOfMessagesSent used as activity proxy")
        if attr_error:
            notes += (f" Could not read queue attributes ({attr_error}) - backlog, redrive "
                      "policy and creation date are all unavailable for this queue.")
        if backlog:
            notes += (f" {backlog} message(s) waiting and {in_flight} in flight. A backlog on a "
                      "queue with no recent traffic usually means the consumer is gone, not that "
                      "the queue is idle - check what was meant to read this before deleting. ")
        notes += ("Deleting a queue discards every message still in it, and the name cannot be "
                  "reused for 60 seconds afterwards. ")

        flag = flag_from_activity(evidence, days_ago(created))
        if backlog and not evidence.used:
            flag = "STALE (UNDRAINED BACKLOG - consumer likely gone)"

        row = new_row(
            "SQSQueue", region, name, name,
            created, last_used, ref_days, inferred, flag, notes,
            tags=tags,
            description=f"{'FIFO' if is_fifo else 'standard'} queue"
                        + (f", last modified {modified}" if modified else ""),
            arn=attrs.get("QueueArn", ""),
        )
        # A queue has no describe payload - get_queue_attributes IS the resource.
        raw_capture.record("SQSQueue", region, name, attrs)

        # The redrive policy is a JSON *string* inside the attribute map.
        redrive = parse_policy_document(attrs.get("RedrivePolicy"))
        dlq_arn = (redrive or {}).get("deadLetterTargetArn")
        if dlq_arn:
            dlq_id, dlq_service = probable_resource_id_from_arn(dlq_arn)
            add_edge(row, dlq_id, "dead-letters to", "SQS RedrivePolicy deadLetterTargetArn",
                     conn_type="sqs.sqs.redrive-policy", target_service=dlq_service)
        rows.append(row)
    return rows


def collect_sns_topics(session, region):
    """SNS topics. No creation date exists, so silence is UNKNOWN; a topic with no
    confirmed subscribers publishes into nothing."""
    sns = session.client("sns", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    topics, page_error = paged(sns, "list_topics", "Topics", service="SNSTopic")
    if page_error and not topics:
        return [error_row("SNSTopic", region, "ERROR", page_error)]

    for topic in topics:
        arn = topic.get("TopicArn", "")
        if not arn:
            continue
        name = arn.rsplit(":", 1)[-1]

        attrs_resp = safe_call(sns.get_topic_attributes, TopicArn=arn)
        attrs = attrs_resp.get("Attributes", {}) if "__error__" not in attrs_resp else {}
        confirmed = int(attrs.get("SubscriptionsConfirmed") or 0)
        pending = int(attrs.get("SubscriptionsPending") or 0)

        tags_resp = safe_call(sns.list_tags_for_resource, capability=coverage.TAGS, ResourceArn=arn)
        tags = tags_to_dict(tags_resp.get("Tags", [])) if "__error__" not in tags_resp else {}

        evidence = cw_activity(
            cw, "AWS/SNS", "NumberOfMessagesPublished",
            [{"Name": "TopicName", "Value": name}], stat="Sum"
        )
        last_used = evidence.last_activity
        ref_days = days_ago(last_used) if last_used else None

        subs_resp = safe_call(sns.list_subscriptions_by_topic, TopicArn=arn)
        subscriptions = subs_resp.get("Subscriptions", []) if "__error__" not in subs_resp else []

        notes = activity_note(evidence, "NumberOfMessagesPublished used as activity proxy")
        notes += (" SNS exposes no creation timestamp at all, so there is no creation date to fall "
                  "back on when no messages were published - absence of a signal here means "
                  "genuinely unknown, not old. ")
        if confirmed == 0:
            notes += ("No confirmed subscriptions - anything published to this topic goes nowhere. ")
        if pending:
            notes += f"{pending} subscription(s) still pending confirmation. "

        if evidence.used:
            flag = flag_from_activity(evidence, None)
        elif confirmed == 0:
            flag = "STALE (NO SUBSCRIPTIONS - publishes into nothing)"
        else:
            flag = "UNKNOWN"

        # Email/SMS/HTTP endpoints link to no row but still explain the topic.
        unlinkable = [f"{s.get('Protocol')}:{s.get('Endpoint')}" for s in subscriptions
                      if s.get("Protocol") not in ("lambda", "sqs")]

        row = new_row(
            "SNSTopic", region, name, attrs.get("DisplayName") or name,
            "", last_used, ref_days, not evidence.used, flag, notes,
            tags=tags,
            description=f"{len(subscriptions)} subscription(s)",
            connections=(f"non-resource subscribers: {', '.join(unlinkable[:5])}"
                         if unlinkable else ""),
            arn=arn,
        )
        raw_capture.record("SNSTopic", region, name,
                           {"Attributes": attrs, "Subscriptions": subscriptions})
        for sub in subscriptions:
            endpoint = sub.get("Endpoint")
            if sub.get("Protocol") not in ("lambda", "sqs") or not endpoint:
                continue
            sub_id, sub_service = probable_resource_id_from_arn(endpoint)
            add_edge(row, sub_id, f"fans out to ({sub.get('Protocol')})",
                     "SNS list_subscriptions_by_topic Endpoint",
                     conn_type="sns.any.subscription", target_service=sub_service)
        rows.append(row)
    return rows
