"""The resource Q&A agent: LangGraph over two tools (document RAG, scan-data lookup).
No module here may import boto3 or aws_resource_audit.collect (a test guards this)."""

# The LLM traffic, verbatim as the model saw it (identifiers already tokens), under one
# logger name. OFF by default (INFO). The RAG search line logs hits before masking.
PAYLOAD_LOGGER = "app.agent.payload"
