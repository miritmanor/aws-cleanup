"""The resource Q&A agent: LangGraph over two tools (document RAG, scan-data lookup).
No module here may import boto3 or aws_resource_audit.collect (a test guards this)."""

# The raw LLM traffic, verbatim, under one logger name. OFF by default (INFO) and NOT
# masked: only 12-digit account ids are scrubbed, and never the user's own message.
PAYLOAD_LOGGER = "app.agent.payload"
