# Bilingual RAG + MCP

[![rag-mcp](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/rag-mcp.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/rag-mcp.yml)

Question answering over company documents in English and Arabic, with a citation on every statement and an honest "not in the documents" when the answer is not there. Hybrid retrieval (pgvector plus Postgres full-text search) and Claude for the answers, available over HTTP and as a read-only MCP server.

Stack: Python 3.11 or 3.12 (3.12 on Vercel), FastAPI, psycopg 3 (async), Postgres with pgvector (Supabase or local), Gemini embeddings, Claude `claude-sonnet-5-5`, MCP Python SDK 2.3.

Live demo: https://syed-rag.vercel.app

Spec: [03-bilingual-rag-mcp.md](03-bilingual-rag-mcp.md)

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Ingestion pipeline and pgvector schema | Done |
| 2 | Hybrid retrieval and cited answers (English) | Done |
| 3 | Arabic answers, language detection, cross-language retrieval | Done |
| 4 | Eval suite with results table | Done (first version, see the scope note) |
| 5 | MCP server (stdio and streamable HTTP) | Done; Claude Desktop video pending |
| 6 | Web page, public demo limits in Postgres, public MCP endpoint, Vercel deploy files | Done, live at https://syed-rag.vercel.app; video pending |

## How it works

```mermaid
flowchart LR
    D[documents/ md, pdf, docx] --> P[parse: headings, tables, pages]
    P --> C[chunk by section, overlap]
    C --> H{content hash in cache?}
    H -->|no| G[Gemini batchEmbedContents]
    H -->|yes| S
    G --> S[(Postgres: chunks + tsvector + vector 768)]
    Q[question] --> QE[query embedding] --> HS[hybrid_search: vector + full-text + translated full-text, RRF]
    Q --> TR[Haiku: translate to the other language] --> HS
    S --> HS
    HS --> A[Claude: statements with source + quote]
    A --> V{quote found in that chunk?}
    V -->|yes| R[answer with numbered sources]
    V -->|no| X[statement dropped]
    X -->|nothing left| N[not in the documents]
```

## Layout

```
rag/documents.py     parsers: Markdown (front matter, tables), PDF (pypdf, headings by font size), DOCX
rag/chunking.py      section chunks with heading path and overlap
rag/embeddings.py    Embedder interface, Gemini implementation, content-hash cache
rag/db.py            psycopg queries: documents, chunks, embedding cache, hybrid_search()
rag/ingest.py        folder ingestion: skip unchanged files, embed in one batch, replace chunks
rag/retrieval.py     query embedding, query translation, hybrid search
rag/answer.py        prompt, Claude call with JSON schema, citation check, rendering
rag/api.py           FastAPI: the question page at /, /documents, /health, /search, /ask (?trace=1), and /mcp when enabled
rag/demo.py          per-address rate limit, daily cap and answer cache for the public demo (Postgres or memory)
rag/templates/, rag/static/  the page (Jinja2), its stylesheet and a five-line script
rag/service.py       read-only search, ask and list for the MCP server
rag/mcp_server.py    MCP server: tools, stdio and streamable HTTP (bearer token, or public under the demo limits)
rag/cli.py           ingest, search, ask from the command line
documents/           the document set (en/, ar/), 6 files
sources/             Markdown sources of the PDF and DOCX files; scripts/build_documents.py renders them
supabase/migrations/ schema and demo limits (not applied to the live project yet)
scripts/live_check.py  live run on a local pgvector database
evals/               questions.jsonl (32), runner, metrics, LLM judge
vercel.json          Vercel project settings (Fluid compute, Frankfurt region); entrypoint in pyproject.toml
DEPLOY.md            Vercel deploy steps: migrations, ingestion, environment variables, checks, MCP connector
Dockerfile           container image (slim Python, non-root); Cloud Run steps in docs/cloud-run/
tests/               parsing, chunking, embeddings (mocked HTTP), Postgres (Docker), answers, API, evals
```

## Documents

A synthetic set for a fictional Dubai property company, Palm Grove Realty. Three documents exist in English and Arabic with the same facts; two exist in only one language, so cross-language questions have something to prove:

| Document | English | Arabic |
| --- | --- | --- |
| Tenant Handbook: moving in, payment plans, deposits, returned cheques, maintenance response times, building rules, moving out | `en/tenant-handbook.pdf` (3 pages) | `ar/tenant-handbook.md` |
| Working Time and Leave Policy: hours, remote work, leave table, carry-over, probation | `en/leave-policy.docx` | `ar/leave-policy.docx` |
| Customer Service FAQ: office hours, viewings, agency fee, landlord packages, complaints | `en/service-faq.md` | `ar/service-faq.md` |
| Residents' Amenities Guide: pool, gym, barbecue area, function room, service lift | `en/amenities-guide.md` | none |
| Travel and Expenses Policy: approvals, flights, hotels, daily allowance, mileage, claims | none | `ar/travel-expenses-policy.md` |

Numbered clauses (`3.3.1`), sections and subsections, and five tables. The Arabic tenant handbook is Markdown rather than PDF because text extracted from Arabic PDFs often comes out in the wrong order or unshaped. Rebuild the PDF and DOCX files with `pip install -e ".[documents]" && python scripts/build_documents.py`.

## Ingestion (Phase 1)

```bash
python -m rag.cli ingest                 # documents/ by default; --prune removes deleted files, --force re-chunks
```

- **Parsing:** Markdown headings and YAML front matter (title, language); DOCX heading styles and tables in document order; PDF text with headings found by font size (larger than the body text, ranked into levels), running headers and footers removed (lines that repeat on every page), and table cells rebuilt from their x positions, including cells that wrap onto a second line. Every table row becomes `Header: value; Header: value`, so a row still makes sense on its own after chunking.
- **Chunking:** one chunk per section when it fits in 1,200 characters; longer sections are split at paragraph, then sentence boundaries, and each window repeats up to 200 characters from the end of the previous one. Metadata per chunk: document, section, heading path (`4. Maintenance > 4.2 Response times`), language, page and last page (PDF only). The document set gives 111 chunks (55 English, 56 Arabic).
- **Embeddings:** behind one `Embedder` interface (`provider`, `model`, `dim`, `embed(texts, task, titles)`); Gemini `gemini-embedding-2` is the implementation, so another provider is one class and one line in `make_embedder()`. Requests go through `batchEmbedContents`, 100 texts each, with `RETRIEVAL_DOCUMENT` or `RETRIEVAL_QUERY` and 768 output dimensions; vectors are L2-normalized. The embedded text is the document title, heading path and chunk, so a short clause keeps its context.
- **Free tier:** requests are paced (10 a minute by default). A 429 waits for the delay Gemini asks for (`RetryInfo`) when it is under a minute; a longer delay, such as an exhausted daily quota, stops the run with a clear error instead of hanging. 5xx is retried with backoff.
- **Cache:** every embedding is stored in `embedding_cache` under (provider, model, dimensions, task, sha256 of the text). Unchanged files are skipped by file hash; a changed file is re-chunked and only its changed chunks are embedded. Query embeddings are cached the same way.
- **Storage:** `documents` and `chunks` (with `vector(768)`, an HNSW cosine index, and a stored `tsvector` column that uses the English or Arabic stemmer by the chunk's language). A document and its chunks are replaced in one transaction.

## Retrieval and answers (Phase 2)

```bash
python -m rag.cli search "how much notice before moving out"
python -m rag.cli ask "What is the fee for a returned cheque?"
uvicorn rag.api:app --reload          # page at http://localhost:8000/, POST /search {"query"}, POST /ask {"question"}
                                      # POST /ask?trace=1 also returns "trace": how the answer was built
```

- **Hybrid search** is one SQL function, `hybrid_search()`: the 20 nearest chunks by cosine distance and the 20 best keyword matches, merged with reciprocal rank fusion, `score = 1/(60 + vector rank) + 1/(60 + keyword rank)`. The keyword side turns the question into its stemmed words joined with OR (an AND of every word in a full question rarely matches anything) and ranks with `ts_rank_cd`, headings weighted above body text. Exact terms such as "returned cheque" or "AED 500" come from the keyword side, paraphrases from the vector side.
- **Diversity:** at most 3 chunks per document in the results (`MAX_PER_DOCUMENT`), so a question that needs two documents is not filled by one.
- **Answers:** the top 6 chunks go to `claude-sonnet-5-5` (effort `medium`, server-side refusal fallback on) as numbered sources with document, section and page. Structured output with a JSON schema: a list of statements, each with citations `{source, quote}`, plus `answerable` and `not_covered`.
- **Every claim cites a chunk:** a citation counts only if its source number was retrieved and its quote (at least 8 characters) appears in that chunk, ignoring case, spacing and punctuation. Statements without a valid citation are dropped and listed in `dropped`. If the model says the sources do not answer, or nothing survives the check, the answer is "The documents do not contain an answer to this question." A partly answerable question gets the supported part plus `not_covered`.
- Sources are renumbered in the order they are first cited and come back as `{n, title, section, heading_path, page, label, quotes}`, for example `[1] Tenant Handbook, 3. Rent and payments > 3.3 Late and returned payments, page 2`.

### Phase 2 live run

`scripts/live_check.py` on a local pgvector container, with real Gemini embeddings and real Claude answers (6 November 2026, before the Phase 3 changes, with the first six documents):

- Ingestion: 96 chunks from 6 documents in 1 Gemini request; the forced re-ingest took 0 requests (96 cache hits) and 0.2 s.
- Six English questions:

| Question | Result | Top retrieved section (vector rank, keyword rank) |
| --- | --- | --- |
| Returned cheque fee and time to replace | AED 500, replace within 7 days by transfer or certified cheque, cited | 3.3 Late and returned payments (1, 1) |
| Response to a water leak | Emergency, first response within 2 hours 24/7, hotline 800 7256, cited | 4.2 Response times (1, 1) |
| Annual leave and carry-over | 25 working days; up to 5 days carried over, used by 31 March | 4. Leave entitlements (2, 1) |
| Agency fee for a new tenancy | 5% of annual rent, minimum AED 5,000, plus 5% VAT; none on renewal | 2.3 What is the agency fee? (1, 1) |
| Dog allowed, and a pet deposit? | Rules answered from two documents; `not_covered`: no separate pet deposit is mentioned | 5.1 Are pets allowed? (1, 2) |
| Car rentals for tenants (out of scope) | "The documents do not contain an answer to this question." | |

Answer latency 1.5 to 4.1 s for the model call. Cost at list price ($2 / $10 per million input / output tokens): $0.0034 to $0.0074 per question, $0.0330 for all six. One request hit a Gemini 429 during the run and went through after the 35 s delay Gemini asked for, which is why the default pace is 10 requests a minute.

## Arabic and cross-language (Phase 3)

- **Answer language:** detected from the question (share of Arabic letters) or set with `language` on `/ask`. The answer and `not_covered` are written in that language; "not in the documents" has a fixed text per language (`لا تحتوي المستندات على إجابة لهذا السؤال.`). Quotes stay in the source's language, so an Arabic answer can cite an English clause and the reverse.
- **Right to left:** `/ask` returns `language` and `direction` (`rtl` for Arabic). The text itself stays plain: no bidi control characters, citation markers like `[1]` as they are; the client sets the direction.
- **Quote check in Arabic:** before matching, both quote and source drop diacritics and tatweel and fold alef forms (أ إ آ ٱ to ا), taa marbuta (ة to ه), alef maqsura (ى to ي), hamza seats (ؤ ئ) and Arabic-Indic digits, so `السنويّة ٢٥` matches `السنويه 25`.
- **Cross-language retrieval:** the vector side is multilingual, but keyword search only matches chunks in the question's language, so in the first eval run an Arabic question about pool hours got five Arabic chunks and never the English-only clause. Each question is now also translated into the other language by `claude-haiku-5-5` (cached per question, about $0.00005 a call) and searched as a second keyword list; RRF sums three lists. If the translation fails, search goes on without it. `QUERY_TRANSLATION=false` turns it off.

## Evals (Phase 4)

```bash
python -m evals.run                 # all 32 questions; python -m evals.run x-ar-01 m-02 for a few
```

`evals/questions.jsonl` holds 32 questions with the expected document and section (`tenant-handbook#3.3`; either language copy counts) and the key facts a complete answer contains: 5 English, 5 Arabic, 3 Arabic questions about English-only facts, 3 English questions about Arabic-only facts, 4 paraphrased, 4 table lookups, 3 that need two documents, and 5 the documents cannot answer. A test checks that every expected section exists in the document set.

The runner ingests (unchanged files skipped), embeds all 32 questions in one cached request, then for each question retrieves, answers and grades:

- **Retrieval hit rate:** every expected section in the top 5. Also how often the first result is from an expected section.
- **Faithfulness:** `claude-opus-5-5`, a different and stronger model than the one answering, grades each statement against the full text of the sources it cites, with a fixed rubric (`evals/judge.py`): supported 1, partial 0.5, unsupported 0, averaged per answer. It also marks each key fact covered or missing.
- **Manual check:** `evals/results/manual_check.md`, a fixed random sample of 6 answers with every statement, its quotes, the judge's verdict, the full cited text and an empty box for your own verdict ([the file from the last run](evals/results/manual_check.md), boxes still empty).
- **Citation validity:** citations whose quote was found in the cited chunk, out of all the model returned.
- **Refusals:** out-of-scope questions answered with "not in the documents", and in-scope questions wrongly refused.
- **Language match**, **latency** (retrieval, translation and answer) and **cost** at list prices (translation and answer; the judge is reported separately; Gemini embeddings are on the free tier).

Results, 6 November 2026, answers by `claude-sonnet-5-5`, judge `claude-opus-5-5`, embeddings `gemini-embedding-2`. The first run is before the cross-language and per-document changes; the second is the current code. Embedding requests per run: 4 for the first (111 chunks and 32 questions, from an empty cache), 0 for the later ones.

| Metric | First run | Current |
| --- | --- | --- |
| Retrieval hit rate (every expected section in the top 5) | 23/27 (85%) | 26/27 (96%) |
| Top result from an expected section | 20/27 (74%) | 25/27 (93%) |
| Answer faithfulness (judge, mean per answer) | 100% | 100% |
| Statements fully supported by their sources | 60/60 (100%) | 67/67 (100%) |
| Key facts covered | 44/50 (88%) | 49/50 (98%) |
| Citation validity (quote found in the cited chunk) | 60/63 (95%) | 67/67 (100%) |
| Out-of-scope questions refused | 5/5 (100%) | 5/5 (100%) |
| In-scope questions wrongly refused | 2/27 (7%) | 0/27 (0%) |
| Answer in the question's language | 32/32 (100%) | 32/32 (100%) |
| Avg latency per question (max) | 2.9 s (5.7 s) | 3.6 s (7.6 s) |
| Avg cost per question (translation + answer) | $0.0060 | $0.0064 |
| Judge cost for the run | $0.20 | $0.21 |

| Category | Questions | Retrieval hit, first run | Retrieval hit, current |
| --- | --- | --- | --- |
| English | 5 | 5/5 | 5/5 |
| Arabic | 5 | 5/5 | 5/5 |
| Arabic question, English-only fact | 3 | 2/3 | 3/3 |
| English question, Arabic-only fact | 3 | 2/3 | 3/3 |
| Paraphrased | 4 | 4/4 | 4/4 |
| Table lookup | 4 | 4/4 | 4/4 |
| Two documents | 3 | 1/3 | 2/3 |

- **The remaining miss:** m-03 ("how do I report a maintenance problem, and how do I complain if it is not fixed?") gets the reporting clause but not the FAQ's complaints answer. The English and Arabic copies of the tenant handbook count as two documents for the per-document cap, so between them they take five places, and Postgres's English stemmer keeps "complain" and "complaint" apart, so the FAQ section gets no keyword match. The answer gives the reporting part and says the complaints part is not covered.
- **First-run failures, now fixed:** two cross-language questions where same-language chunks crowded out the only right clause (the model then correctly said "not in the documents"), and two multi-document questions where one document filled the top 5.

**Scope:** 32 questions over 8 short synthetic documents (111 chunks), written by the same author as the documents, so their wording is closer to the source text than real users' would be. The first run was used to find the two retrieval weaknesses and the current numbers are after fixing them on the same questions, so they are optimistic; a held-out question set and real documents would give lower numbers. Faithfulness comes from one LLM judge that only checks statements against the sources they cite, not whether the answer is complete; the key-facts check covers part of that. The manual sample has not been checked by a person yet. Every number is from a single run.

## MCP server (Phase 5)

Three read-only tools, built with the official MCP Python SDK (`MCPServer`), each with a description, a typed input schema and a structured output schema, and marked read-only (`readOnlyHint`). Ingestion is not exposed; the only thing a call writes is a cached query embedding.

| Tool | Input | Returns |
| --- | --- | --- |
| `search_documents` | `query` (1 to 1,000 characters), `language` (`any`, `en`, `ar`; default `any`), `top_k` (1 to 10, default 5) | Passages with `document`, `title`, `section` (heading path), `page`, `language`, `text`, `score` |
| `ask` | `question` (English or Arabic) | `answer` with `[n]` markers in the question's language, `answerable`, `language`, `direction`, `sources` (number, document, section, page, exact quotes), `not_covered` |
| `list_documents` | none | `path`, `title`, `language`, `format`, `chunk_count`, `ingested_at` per document |

Errors (no database configured, provider down, a value outside the schema) come back as MCP tool errors, not as crashes. For setup and provider failures the client gets one fixed sentence and the reason goes to the server log, because an upstream error text can name hosts or keys; the JSON API does the same with its 503.

```bash
pip install -e .                                     # installs the rag-mcp command
rag-mcp                                              # stdio
MCP_BEARER_TOKEN=$(openssl rand -hex 32) rag-mcp --transport http --host 0.0.0.0 --port 8765   # http://<host>:8765/mcp
```

- **stdio** is for Claude Desktop and other local clients. Logs go to stderr, so stdout carries only the protocol.
- **Streamable HTTP** at `/mcp`, stateless with JSON responses, so any instance can serve any request. Every request needs `Authorization: Bearer <MCP_BEARER_TOKEN>` (compared in constant time); without one the server answers 401, and it refuses to start without a token of at least 24 characters. It listens on 127.0.0.1 unless `--host` says otherwise, and on localhost the SDK also rejects requests whose Host or Origin header is not local (DNS rebinding protection). Put it behind HTTPS before exposing it.

### On the web app (Vercel)

The web app serves the same server at `/mcp` too (streamable HTTP, stateless, JSON responses), mounted next to the page and started in its lifespan:

- **Public demo** (`MCP_PUBLIC=true`, no token): read-only and anonymous. `search_documents` and `ask` count against the same per-visitor limit and daily cap as the page, and `ask` shares the page's answer cache, so repeated questions are free; `list_documents` is always free. A refused call comes back as a tool error with the page's message ("Too many questions from your address...").
- **Private** (`MCP_BEARER_TOKEN` set): every request needs the token, as with `rag-mcp --transport http`, and the demo limits do not apply.
- Neither: no `/mcp`. `/health` reports `"mcp": "public"`, `"private"` or `"off"`.

To use the public endpoint from Claude: **Settings > Connectors > Add custom connector** with the URL `https://<app>.vercel.app/mcp` and no authentication. Other MCP clients: add a remote server with that URL over streamable HTTP. Details in [DEPLOY.md](DEPLOY.md).

### Claude Desktop

**Settings > Developer > Edit Config** opens `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`). Add the server with the absolute path to the virtualenv's `rag-mcp` (on Windows `...\.venv\Scripts\rag-mcp.exe`) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "palm-grove-docs": {
      "command": "/absolute/path/to/rag-mcp/.venv/bin/rag-mcp",
      "env": {
        "DATABASE_URL": "postgresql://postgres:dev@localhost:55433/postgres",
        "GEMINI_API_KEY": "<key>",
        "ANTHROPIC_API_KEY": "<key>"
      }
    }
  }
}
```

The documents must be ingested first (`python -m rag.cli ingest`). Claude Desktop starts the server itself, so the `env` block replaces the `.env` file.

### Example session

The real server over stdio, called through the SDK client (6 November 2026, local pgvector, Gemini embeddings, Claude answers). `list_documents` returned the 8 documents; `search_documents` with `{"query": "notice period before moving out", "top_k": 3}` returned `6.2 Notice to vacate` (page 3) first.

`ask` with an English question:

```json
{
  "question": "How much notice do I need to give if I don't want to renew my lease?",
  "answer": "If you do not wish to renew, you must give written notice at least 60 days before the contract end date. [1]",
  "answerable": true, "language": "en", "direction": "ltr",
  "sources": [{"n": 1, "document": "en/tenant-handbook.pdf", "title": "Tenant Handbook",
               "section": "6. Renewal and moving out > 6.2 Notice to vacate", "page": 3,
               "quotes": ["If you do not wish to renew, give written notice at least 60 days before the contract end date."]}],
  "not_covered": ""
}
```

`ask` with an Arabic question, answered from the Arabic-only travel policy:

```json
{
  "question": "ما البدل اليومي عند السفر في مهمة عمل إلى أوروبا؟",
  "answer": "البدل اليومي للسفر إلى أوروبا هو 400 درهم عن كل يوم سفر. [1] يُصرف البدل عن كل يوم سفر لتغطية الوجبات والتنقلات المحلية. [1] لا يُصرف البدل عن الأيام التي توفر فيها الجهة المضيفة الوجبات كاملة. [1]",
  "answerable": true, "language": "ar", "direction": "rtl",
  "sources": [{"n": 1, "document": "ar/travel-expenses-policy.md", "title": "سياسة السفر ومصروفات العمل",
               "section": "5. البدل اليومي", "page": null,
               "quotes": ["الوجهة: أوروبا; البدل اليومي: 400 درهم",
                          "يُصرف للموظف بدل يومي عن كل يوم سفر لتغطية الوجبات والتنقلات المحلية حسب الوجهة",
                          "لا يُصرف البدل اليومي عن الأيام التي توفر فيها الجهة المضيفة الوجبات كاملة."]}],
  "not_covered": ""
}
```

### Testing with the MCP Inspector

The Inspector starts the server with only the variables passed with `-e`. With the web UI (it prints a local URL to open):

```bash
npx @modelcontextprotocol/inspector@2.12.0 .venv/bin/rag-mcp -e DATABASE_URL=<url> -e GEMINI_API_KEY=<key> -e ANTHROPIC_API_KEY=<key>
```

Or from the command line, one call at a time:

```bash
npx @modelcontextprotocol/inspector@2.12.0 --cli .venv/bin/rag-mcp -e DATABASE_URL=<url> --method tools/list
npx @modelcontextprotocol/inspector@2.12.0 --cli .venv/bin/rag-mcp -e DATABASE_URL=<url> --method tools/call --tool-name list_documents
npx @modelcontextprotocol/inspector@2.12.0 --cli .venv/bin/rag-mcp -e DATABASE_URL=<url> -e GEMINI_API_KEY=<key> -e ANTHROPIC_API_KEY=<key> \
  --method tools/call --tool-name ask --tool-arg question="What is the agency fee?"
```

Over HTTP, start the server with a token and pass the header (in the web UI: transport Streamable HTTP, URL `http://127.0.0.1:8765/mcp`, header `Authorization: Bearer <token>`):

```bash
npx @modelcontextprotocol/inspector@2.12.0 --cli http://127.0.0.1:8765/mcp --transport http \
  --header "Authorization: Bearer $MCP_BEARER_TOKEN" --method tools/list
```

Checked with Inspector 2.12.0 here: `tools/list` and `list_documents` over stdio, and `tools/list` over HTTP with the token. Without the header the server's 401 makes the Inspector start an OAuth login instead.

## Web page and public demo (Phase 6)

`GET /` is a question page served by the same FastAPI app, in the portfolio site's look (same palette, type and logo mark, a "Back to portfolio" link from `PORTFOLIO_URL`); light and dark follow the system, and `color-scheme` keeps phone browsers from force-darkening it.

- A short header names the documents as chips, with the languages each exists in. A question box (`dir="auto"`, so Arabic types right to left) and five example questions: two English, two Arabic, and one the documents cannot answer. On phones the examples are one row that scrolls sideways.
- While the answer is being written the page shows a loading state (spinner and placeholder lines) and disables the buttons; coming back with the browser's back button clears it.
- The answer has `[n]` markers. Tapping one opens the exact passage it came from in a sheet (a bottom sheet on phones, a dialog on wider screens) with the quoted words highlighted. Below the answer, the sources are cards with the document, section, page and a short snippet, each expanding to the full passage. Without JavaScript the markers jump to the cards.
- Arabic answers and Arabic passages are set `dir="rtl" lang="ar"` on their own blocks; the page chrome stays left to right. The layout uses logical CSS properties (`margin-inline-start`), so indents flip with the direction.
- "The documents do not contain an answer to this question." (or the Arabic text) shows as an amber banner, with what the documents do not cover underneath.
- **How this answer was built:** a collapsed panel under every answer, from the real pipeline run: the question as searched (trimmed, detected language, the stemmed keyword terms Postgres searches for, and the translated query with its terms), each ranked list's candidates (by meaning with similarity, by keywords, by translated keywords), the passages given to the model with their ranks and fused score (sum of 1 / (60 + rank)) and the [n] each is cited as, every quote check (found, or why not: no such source, too short, not in the source; a quote cited by several statements is listed once with how many use it), and the steps on a timeline. Embedding the question and translating it run at the same time (they do not depend on each other), and the timeline shows them side by side. A cached answer shows the trace of its first run. The candidate lists come from a second call of the same `hybrid_search` asked for its whole fused pool, so they are what the SQL ranked, not a reimplementation; ranking and answers are unchanged with or without the trace.
- **Documents** (`/documents`): every document with its language, number of sections and passages, and when it was last indexed.
- **Use with MCP:** when the public MCP endpoint is on (`MCP_PUBLIC=true`, no token), a collapsed panel gives the endpoint URL, a client config to copy and the tools with their parameters, read from the MCP server itself so it cannot drift. With a token, or with MCP off, the panel is left out.
- The demo bar shows the day's questions as a small meter ("149/150 left today · 5 per 10 min each").
- The footer, the "What this demo shows" card (closed state remembered in the browser) and the empty, error and rate-limit states are the shared ones from [`demo-ui/`](../demo-ui/README.md), also used by the Call QA demo.
- Server-rendered with Jinja2 (autoescaped); the page sends a strict Content-Security-Policy (only its own scripts and stylesheets, no inline styles: bars use width classes in 5% steps), and every response has `X-Frame-Options: DENY` and `nosniff`.

**Public demo, no login.** So the Anthropic key cannot be drained:

- At most 5 questions per 10 minutes per client address, and 150 a day in total (UTC day), shared by the page, `/ask` and `/search` (`DEMO_QUESTIONS_PER_CLIENT`, `DEMO_WINDOW_SECONDS`, `DEMO_DAILY_CAP`). Over the limit the page says when to try again and the API answers 429 with `Retry-After`. The demo bar shows how many questions are left today.
- The same question (ignoring case and spacing) is answered from a cache for a day, without counting against either limit, so clicking the examples costs nothing after the first time.
- **Warm examples:** `python -m rag.cli warm` answers the five example questions straight into that cache (`rag/warm.py`), so the example chips answer at once instead of in 10-12 s. It never goes through the limiter, so no visitor's limit and nothing of the daily cap is used. The `warm-rag-cache` workflow runs it after each successful production deploy of the RAG app on Vercel (with `--force`, since a deploy may change how answers are made), every day at 05:17 UTC (refreshing answers older than 20 hours, before the 24 h expiry) and by hand. Secrets: `SUPABASE_DB_URL`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, as for setup-supabase. About $0.03 a run, so roughly $1 a month.
- **Shared by every instance:** with a database configured, the limits and the cache are in Postgres (`demo_requests`, `demo_answers`; migration `20261110000000_demo_limits.sql`). `demo_take` checks both limits and counts the question in one call, under a transaction-scoped advisory lock, so concurrent serverless instances never exceed the daily cap (tested with 20 concurrent questions from 4 separate pools against a cap of 7). If the limits cannot be checked, the question is refused rather than answered uncounted; a cache failure is only a miss. Without a database (`DEMO_STORE=memory`) they are in memory, per process.
- The client address comes from `CLIENT_IP_HEADER` (Vercel: `x-real-ip`, which Vercel sets itself), otherwise the socket address. The daily cap is what bounds spending: about $1 a day at $0.0064 a question.
- Under the question box: "Questions are processed by third-party AI services (Anthropic and Google). Don't enter personal information."

Checked in Chromium with real answers: English (light), Arabic citing the English amenities guide (dark), Arabic on a 390 px phone, and the refusal. That check also showed the model summarising what the documents do cover in `not_covered`, which is shown without citations, so the prompt now limits `not_covered` to naming what is missing.

**Hosting:** a Vercel project (Root Directory `rag-mcp`, entrypoint `rag.api:app`, Python 3.12, Fluid compute, Frankfurt), with Supabase Postgres on the free plan. Connections go through Supabase's transaction pooler, open on demand (at most `DB_POOL_MAX`, 3, per instance) and close after `DB_POOL_MAX_IDLE_SECONDS` (30) idle; prepared statements are off for the pooler. Steps: [DEPLOY.md](DEPLOY.md).

**Container:** `Dockerfile` follows call-qa-agent's: slim Python 3.11, dependencies from `pyproject.toml` in their own layer, only `rag/` copied in, a non-root user, uvicorn on `$PORT` with proxy headers. Built and run locally (355 MB): page, stylesheet and `/health` answer, and a question without keys gets the plain "not available" message. Cloud Run steps: [docs/cloud-run/rag-mcp.md](../docs/cloud-run/rag-mcp.md).

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker run -d --name rag-db -e POSTGRES_PASSWORD=dev -p 55433:5432 pgvector/pgvector:pg17
cp .env.example .env                      # set GEMINI_API_KEY and ANTHROPIC_API_KEY
python scripts/live_check.py --setup      # applies the migrations (local only), ingests, asks
```

Or apply `supabase/migrations/20261109000000_rag.sql` yourself and use the CLI. On Supabase, use the transaction pooler connection string (port 6543) as `DATABASE_URL`; the migration enables `vector` and turns on row level security with no policies, so only the backend's database connection can read the tables.

## Tests

```bash
pytest
```

One test asks two questions in a real browser and checks the thread they build, down to the cited passage (`tests/test_smoke_browser.py`): it runs the app on a free port with a stubbed answer service, drives it in Chromium and fails on any script error or blocked resource, so the page is tested under its own security policy. It needs `playwright install chromium` once and is skipped when Playwright or its browser is not installed, except in CI.

CI installs the exact versions in `requirements.lock` instead of the ranges in `pyproject.toml`: `pip install --require-hashes -r requirements.lock && pip install --no-deps -e .`. To refresh the lock, run the `uv pip compile` command in its first lines.

The database tests start a throwaway pgvector container with Docker. To use an existing empty pgvector database instead, set `TEST_DATABASE_URL` (CI does this with a service container); the migrations are applied to it at the start.

129 tests. Parsing of all eight documents (PDF headings, pages, footer removal, wrapped table cells; DOCX styles and tables; Markdown front matter), chunking and overlap, the Gemini client against a mocked HTTP transport (request format, batches of 100, pacing, 429 with a short and a long retry delay, 5xx, wrong dimensions), the cache, ingestion and hybrid search on a throwaway pgvector container (skipping unchanged files, re-embedding only changed chunks, prune, RRF scores, OR keywords, Arabic stemming, language filter), the citation check (including Arabic spelling variants), answer language, translation, the Claude request shape with a fake client, the API, the eval code (section matching, judge parsing, the runner end to end with fakes, stopping on an overloaded provider), and the MCP server through the SDK's client: tool list, schemas and read-only hints, each tool against the test database, English and Arabic answers, tool errors, the real stdio transport as a subprocess, and streamable HTTP with missing, wrong and correct bearer tokens. The page: examples, citations with highlighted quotes, right-to-left Arabic, the refusal, escaping, security headers, the per-address limit, the daily cap with its UTC reset, the answer cache, and the plain message when the provider is down. The Postgres limits and cache on the pgvector container: per-client window, daily cap and UTC midnight, concurrent instances against the cap, cleanup, refusal when the database is down, and that the public API roles cannot call `demo_take`. `/mcp` on the web app over real HTTP: public without a token and counted per visitor (by `x-real-ip`), sharing the daily cap and answer cache with the page, private with the token and no limits, and off by default.

## Not yet

- Deploying to Vercel (the code and settings are ready).
- The demo video (Claude Desktop and the web page).
