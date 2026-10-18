# 0071 - the agent's LLM sees tokens, not identifiers

Status: accepted. Replaces the account-id masking in `backend/app/masking.py`.

## Context

The agent sends scan rows, uploaded-document passages and the user's question
to a third-party LLM. Only 12-digit account ids were masked, and with an
unkeyed SHA-256 prefix: there are 10^12 account ids, so the placeholder could
be reversed by trying them all. Resource ids, ARNs, names, tag values, IP
addresses and project names went out as they were, and so did the user's
question.

## Decision

`backend/app/privacy/` replaces every identifier with a token before anything
reaches the model, and swaps the tokens back before the user reads a reply.

- **A token is `KIND-` plus the first 10 hex characters of an HMAC-SHA256** of
  the value, keyed with 32 random bytes kept in `agent_pseudonym.key` on the
  data volume (mode 0600). The key makes a token impossible to reverse by
  guessing. Kinds: `ACCT`, `RES`, `NAME`, `PROJ`, `TAG`, `ID`. The kind tells
  the model what sort of value it is, which it needs in order to reason.
- **Two passes per string.** First, shapes: ARNs, email addresses, host
  names, access key ids, UUIDs, AWS-style ids (`i-0abc…`), IPv4 and CIDR,
  12-digit numbers, long opaque strings. Then every value the scan recorded
  (ids, ARNs, names, project ids and names, tag values, reference targets) is
  matched as a whole word.
- **Some values are sent as they are.** Fields this program fills from a
  fixed vocabulary (service, region, tier, flag and so on), category tag
  values (`production`, `dev`), and generic names (`default`, `main`) in
  free text.
- **Reversal is local.** The streamed reply goes through `RevealStream`,
  which holds back a tail that could be the start of a split token. The
  document search tool reveals the model's query before searching the local
  index, and masks the hits before returning them.
- **The document index stores text as written.** It is on the same volume
  as the full snapshot, and the embedding model runs locally (decision 0063).

## Consequences

- Nothing that identifies the account reaches the provider. What does reach
  it: resource types, regions, dates, costs, flags, and the program's own
  explanations with every identifier replaced by a token.
- The model cannot use meaning carried by a name (`payments-prod-db` says
  more than `RES-3f…`). Answers can be less specific; that is the price.
- The `app.agent.payload` debug log now shows tokens, as the model saw them.
  The RAG search line still logs hits before masking.
- Deleting `agent_pseudonym.key` changes every token, which breaks only the
  history of conversations already in progress.
- Free-text matching covers what the scan recorded and the listed shapes. A
  name that appears only in an uploaded document and matches no shape (a
  person's name in prose, say) is still sent.
- `backend/apitests/test_masked_rows.py` runs the whole
  synthetic corpus through the store and fails if any recorded identifier
  appears in what the agent receives.
