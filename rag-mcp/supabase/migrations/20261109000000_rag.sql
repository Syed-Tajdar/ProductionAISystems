-- Documents, chunks with 768-dimension embeddings, an embedding cache, and hybrid search.
-- Works on Supabase (enable the extension in the dashboard or let this create it) and on any
-- Postgres 15+ with pgvector 0.7+. The backend connects as the database owner; RLS is on with
-- no policies, so the anon and authenticated API roles cannot read anything.

create extension if not exists vector;

create table documents (
  id            uuid primary key default gen_random_uuid(),
  path          text not null unique,            -- relative to the documents folder
  title         text not null,
  language      text not null check (language in ('en', 'ar')),
  format        text not null check (format in ('markdown', 'pdf', 'docx')),
  content_hash  text not null,                   -- sha256 of the file: unchanged files are skipped
  chunk_count   int not null default 0,
  meta          jsonb not null default '{}'::jsonb,
  ingested_at   timestamptz not null default now()
);

create table chunks (
  id            bigint generated always as identity primary key,
  document_id   uuid not null references documents (id) on delete cascade,
  chunk_index   int not null,
  section       text not null,
  heading_path  text not null default '',
  language      text not null check (language in ('en', 'ar')),
  page          int,
  page_end      int,
  content       text not null,
  content_hash  text not null,                   -- sha256 of the embedded text
  embedding     vector(768) not null,
  -- Keyword search uses the stemmer of the chunk's own language.
  tsv           tsvector generated always as (
                  setweight(to_tsvector(case when language = 'ar' then 'arabic'::regconfig else 'english'::regconfig end,
                                        heading_path), 'A') ||
                  setweight(to_tsvector(case when language = 'ar' then 'arabic'::regconfig else 'english'::regconfig end,
                                        content), 'B')) stored,
  unique (document_id, chunk_index)
);

create index chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);
create index chunks_tsv_idx on chunks using gin (tsv);
create index chunks_language_idx on chunks (language);

-- Embeddings by content hash, so re-ingesting unchanged text makes no API calls. Kept apart from
-- chunks so deleting or re-chunking a document does not throw embeddings away.
create table embedding_cache (
  provider      text not null,
  model         text not null,
  dim           int not null,
  task          text not null check (task in ('document', 'query')),
  content_hash  text not null,
  embedding     vector not null,
  created_at    timestamptz not null default now(),
  primary key (provider, model, dim, task, content_hash)
);

-- Hybrid search: the nearest chunks by cosine distance and the best keyword matches, merged with
-- reciprocal rank fusion: score = sum of 1 / (rrf_k + rank) over the lists.
-- Keywords are OR-ed (any stemmed word of the question can match) and ranked with ts_rank_cd.
-- alt_text is the question translated into the other language: a second keyword list, so a
-- clause that exists only in the other language can win on keywords too.
-- max_per_document keeps one document from filling every slot.
create or replace function hybrid_search(
  query_embedding  vector(768),
  query_text       text,
  query_language   text default 'en',
  match_count      int default 6,
  candidates       int default 20,
  rrf_k            int default 60,
  only_language    text default null,
  alt_text         text default null,
  alt_language     text default null,
  max_per_document int default null
)
returns table (
  chunk_id      bigint,
  document_path text,
  title         text,
  section       text,
  heading_path  text,
  language      text,
  page          int,
  page_end      int,
  content       text,
  vector_rank   int,
  keyword_rank  int,
  alt_keyword_rank int,
  similarity    float,
  score         float
)
language sql stable
as $$
  with q as (
    select to_tsquery('simple', coalesce(string_agg(quote_literal(lexeme), ' | '), '')) as tsq
      from unnest(tsvector_to_array(to_tsvector(
             case when query_language = 'ar' then 'arabic'::regconfig else 'english'::regconfig end, query_text))) as lexeme
  ),
  q2 as (
    select to_tsquery('simple', coalesce(string_agg(quote_literal(lexeme), ' | '), '')) as tsq
      from unnest(tsvector_to_array(to_tsvector(
             case when alt_language = 'ar' then 'arabic'::regconfig else 'english'::regconfig end, coalesce(alt_text, ''))))
           as lexeme
  ),
  vec as (
    select c.id, row_number() over (order by c.embedding <=> query_embedding) as rank,
           1 - (c.embedding <=> query_embedding) as similarity
      from chunks c
     where only_language is null or c.language = only_language
     order by c.embedding <=> query_embedding
     limit candidates
  ),
  kw as (
    select c.id, row_number() over (order by ts_rank_cd(c.tsv, q.tsq) desc, c.id) as rank
      from chunks c, q
     where numnode(q.tsq) > 0 and c.tsv @@ q.tsq
       and (only_language is null or c.language = only_language)
     order by ts_rank_cd(c.tsv, q.tsq) desc, c.id
     limit candidates
  ),
  kw2 as (
    select c.id, row_number() over (order by ts_rank_cd(c.tsv, q2.tsq) desc, c.id) as rank
      from chunks c, q2
     where alt_text is not null and numnode(q2.tsq) > 0 and c.tsv @@ q2.tsq
       and (only_language is null or c.language = only_language)
     order by ts_rank_cd(c.tsv, q2.tsq) desc, c.id
     limit candidates
  ),
  ids as (
    select id from vec union select id from kw union select id from kw2
  ),
  fused as (
    select i.id, v.rank as vector_rank, k.rank as keyword_rank, k2.rank as alt_keyword_rank, v.similarity,
           coalesce(1.0 / (rrf_k + v.rank), 0) + coalesce(1.0 / (rrf_k + k.rank), 0)
             + coalesce(1.0 / (rrf_k + k2.rank), 0) as score
      from ids i
      left join vec v on v.id = i.id
      left join kw k on k.id = i.id
      left join kw2 k2 on k2.id = i.id
  ),
  ranked as (
    select f.*, c.document_id,
           row_number() over (partition by c.document_id order by f.score desc, f.vector_rank nulls last) as doc_rank
      from fused f join chunks c on c.id = f.id
  )
  select c.id, d.path, d.title, c.section, c.heading_path, c.language, c.page, c.page_end, c.content,
         r.vector_rank::int, r.keyword_rank::int, r.alt_keyword_rank::int, r.similarity::float, r.score::float
    from ranked r
    join chunks c on c.id = r.id
    join documents d on d.id = c.document_id
   where max_per_document is null or r.doc_rank <= max_per_document
   order by r.score desc, r.vector_rank nulls last
   limit match_count;
$$;

alter table documents enable row level security;
alter table chunks enable row level security;
alter table embedding_cache enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on function hybrid_search(vector, text, text, int, int, int, text, text, text, int) from public, anon, authenticated';
  end if;
end;
$$;
