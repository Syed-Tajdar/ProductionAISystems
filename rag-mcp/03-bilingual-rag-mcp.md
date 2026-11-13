# Project 3: Bilingual RAG Assistant + MCP Server

**One line for CV:** English/Arabic question-answering assistant over company documents with cited answers, an evaluation suite, and an MCP server so Claude Desktop or any agent can use it as a tool. Python, FastAPI, pgvector, Claude/OpenAI.

## Problem

Companies want staff and customers to get answers from policies, manuals and FAQs, in Arabic and English, with sources they can trust. They also want those answers available to their AI agents.

## Scope (v1)

1. Ingest PDFs, DOCX and Markdown from a folder. Use public documents only, for example a public government services guide or your own written policy pack.
2. Chunk, embed, store in Supabase pgvector (or Postgres + pgvector in Docker).
3. Ask endpoint: retrieves top chunks (hybrid: vector + keyword), answers with citations (doc name + page/section). If the answer is not in the documents, say so.
4. Language: detect question language; answer in the same language; Arabic and English documents both searchable.
5. Simple chat UI with citations as clickable chips.
6. MCP server exposing two tools: `search_documents(query)` and `answer_question(question)`. Show it working inside Claude Desktop in the demo video.

## Evals

- `evals/questions.jsonl`: 30 questions (15 EN, 15 AR) with the expected source document and key facts.
- Metrics: retrieval hit rate (correct doc in top 5), answer faithfulness (LLM-as-judge plus manual spot check), refusal correctness on 5 out-of-scope questions.
- Print results table; put it in README.

## Tech

- Python 3.11+, FastAPI, async httpx
- Embeddings: OpenAI text-embedding model or a multilingual open model
- Supabase pgvector
- Claude or OpenAI for answers (provider switch via env)
- MCP Python SDK for the server
- Next.js or plain HTML chat UI

## Phases (gate after each)

1. Ingestion pipeline + pgvector schema.
2. Retrieval + answer with citations (EN first).
3. Arabic support and language detection.
4. Eval suite with results table.
5. MCP server + Claude Desktop demo.
6. UI, deploy, README with diagram and video.

## Done means

- Every answer shows its sources; out-of-scope questions get an honest "not in documents".
- Eval numbers in README.
- MCP tools usable from Claude Desktop, shown on video.
