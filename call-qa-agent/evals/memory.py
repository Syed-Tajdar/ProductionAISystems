"""An in-memory stand-in for the Supabase client, with the calls the pipeline makes during an eval.

`python -m evals.run --no-db` uses it, so the eval needs only the Deepgram and LLM keys. The tests use it too.
"""


class MemoryDB:
    def __init__(self):
        self.calls, self.rubrics, self.n = {}, {}, 0

    def insert_call(self, **fields):
        self.n += 1
        call = {"id": f"c{self.n}", "error": None, "duration_sec": None, "transcripts": None, **fields}
        self.calls[call["id"]] = call
        return call

    def update_call(self, call_id, **fields):
        self.calls[call_id].update(fields)

    def save_transcript(self, call_id, utterances, provider="deepgram"):
        self.calls[call_id]["transcripts"] = {"utterances": utterances}

    def get_call(self, call_id):
        return self.calls.get(call_id)

    def delete_call(self, call_id):
        self.calls.pop(call_id)

    def get_rubric(self, slug, version):
        return self.rubrics.get(slug)

    def insert_rubric(self, **fields):
        self.rubrics[fields["slug"]] = {"id": f"r-{fields['slug']}", **fields}
        return self.rubrics[fields["slug"]]

    def delete_rubric(self, rubric_id):
        self.rubrics = {k: v for k, v in self.rubrics.items() if v["id"] != rubric_id}

    def insert_score(self, **fields):
        return {"id": "s1", **fields}
