# 0063 - The agent is chosen at build time and runs without torch

Status: accepted. Moves the provider out of `agent_config.json`. The default
is `none`: switching the agent on sends masked scan data (0071) to a third
party, so the user chooses it.

## Context

The backend image installed every agent package unconditionally:

- all three chat providers (`langchain-openai`, `langchain-anthropic` and
  `langchain-aws`), although `agent_config.json` selects one and switching
  also means a different key secret and a restart;
- `torch`, `sentence-transformers` and `langchain-huggingface` (which also
  install `transformers`, `scipy`, `scikit-learn` and more), to run one small
  embedding model, all-MiniLM-L6-v2.

No record says why torch was chosen. It arrived with `HuggingFaceEmbeddings`,
LangChain's usual route to a local model. The recorded choice is the one in
`embeddings.py`: a local open-source model, so no key and no per-query cost.

`chromadb`, already installed for the document index, ships the same model in
ONNX form and requires `onnxruntime` to run it. Torch was a second runtime for
a model the image could already run.

The agent is optional: without an API key it is hidden. But its packages were
always installed, and `api/agent.py` imported langchain at server start.

## Decision

1. **`AUDIT_AGENT_PROVIDER` chooses the provider when the image is built.**
   It is set in `.env`, defaults to `openai`, and can be `openai`, `anthropic`,
   `bedrock` or `none`. Compose passes it as a build argument. The Dockerfile
   rejects any other value, because pip only warns about an unknown extra and
   would silently build an image with no agent. It installs
   `backend[agent,<provider>]`, or neither extra for `none`, and keeps the value
   as `ENV`. `app/agent/install.py` reads that and checks the packages exist,
   without importing them.
2. **`none` builds the image without the agent.** The status endpoint reports
   it unavailable with the reason, so the UI hides the panel as it does with no
   key. A question or a reindex gets the same reason as an error, not an import
   failure.
3. **`provider` and `embedding_model` leave `agent_config.json`.** One place
   names the provider. An old file that names either is refused with a message
   saying where the setting went. `model` stays, with one default (an OpenAI
   model): whoever changes the provider sets `model` too.
4. **Embeddings use Chroma's ONNX all-MiniLM-L6-v2.** `OnnxMiniLmEmbeddings`
   names `ONNXMiniLM_L6_V2` and not `DefaultEmbeddingFunction`, so a chromadb
   upgrade cannot change the model under an existing index. Torch,
   `sentence-transformers` and `langchain-huggingface` are removed, along with
   the Dockerfile's CPU-only torch step and `HF_HUB_OFFLINE`.
5. **The collection is renamed to `account_documents_onnx`.** The same model
   gives slightly different numbers in ONNX than in torch, so old vectors must
   not be mixed with new ones. An existing index reads as "not indexed" until
   the next upload or delete rebuilds it.
6. **Langchain is imported when a question arrives, not at server start.**
7. **Tests that need the agent's packages skip in an image built without
   them.** The status, error-frame and `none` tests run in every image.

## Consequences

- The image no longer contains torch, transformers, scipy or scikit-learn,
  and the build has no torch step. A local install of the agent with the
  `openai` extra, without torch, is 570 MB of packages. That was measured on
  macOS and is not an image size.
- The embedding model is fixed. To use a different one later (larger, or
  multilingual), `fastembed` runs many models in ONNX without torch. Returning
  to torch is not required.
- The model now comes from Chroma's storage
  (`chroma-onnx-models.s3.amazonaws.com`, about 79 MB, checksum verified) on
  first use. It is cached in the container user's home directory, as the
  Hugging Face model was, so a recreated container downloads it again.
- Embeddings now depend on chromadb. Replacing Chroma means replacing the
  embedding model as well.
- Switching provider is: edit `.env`, set `model` in `agent_config.json`,
  then `docker compose up --build`.
