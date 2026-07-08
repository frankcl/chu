---
name: web-research
description: Research a topic on the web and produce a sourced summary. Use when the user asks you to look something up, find current information, or summarize what's online about a topic.
---

# Web Research

Use this skill when the user needs up-to-date information from the web rather than
relying on your own knowledge.

Web search is available ONLY through this skill's bundled `search.py` script —
there is no separate search tool. Always search via the steps below.

## Steps

1. The current date is **{{CURRENT_DATE}}**. For time-sensitive questions
   (prices, news, "今年/今天/最新/this year/latest"), resolve relative time
   words against THIS date and include the current year (and month if relevant)
   in your search query. Never assume an older year — do NOT default to your
   training-data year (e.g. 2024) when the user says "今年/this year".
2. Search by running the bundled script. If you need to look up SEVERAL things,
   pass ALL queries in ONE call — the script searches them concurrently and
   returns them together. Do NOT make one call per query.
   `run_skill_script(skill="web-research", script="search.py", script_args=["<query 1>", "<query 2>", ...])`
   It returns JSON `{"searches": [{query, results: [{title, url, content}]}]}`.
   At most 5 queries per call (extras are dropped and noted).
3. Read the returned snippets. Prefer recent, authoritative sources.
4. Write a concise summary (3–6 sentences) that directly answers the user's
   question, and list the source URLs you relied on.

## Search budget — STOP and answer

You may run the search script **at most 3 times** per request (each call may
batch up to 5 queries). This is a hard cap enforced by the runtime, not just a
goal — once reached, further calls are refused and you must answer with what you
already have.

- Prefer to get everything in ONE batched call. After you have usable results,
  STOP searching and write the summary. Do NOT search again just to look for a
  "better" or more complete answer.
- Only run a 2nd/3rd call to fix a genuinely failed query (no results, or
  clearly wrong topic), each time with **meaningfully different** queries — never
  re-run a near-identical query.
- Many questions have **no definite answer** — especially predictions or
  forecasts about the future (e.g. "明天/下周 走势如何", "will X go up
  tomorrow"). No amount of searching will surface a certain answer. Do NOT keep
  searching for one. Instead, after 1–2 searches, summarize the relevant current
  facts (latest data, analyst views, market sentiment) and **explicitly state
  that the future outcome is uncertain / cannot be predicted**. That is a
  complete, correct answer — return it.
- If after 3 searches you still lack a confident answer, stop and answer with
  what you found, clearly noting the limitation. Never exceed the cap.

Always ground claims in the search results — do not invent facts or URLs.
