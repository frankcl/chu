---
name: ppt
description: Generate a PowerPoint (.pptx) presentation from an outline. Use when the user asks you to make slides, build a deck, create a PPT/PowerPoint, or turn content into a presentation.
---

# PowerPoint Builder

Use this skill when the user wants a slide deck / PowerPoint file. You design
the content and structure; the bundled `build.py` script turns a JSON spec into
a real `.pptx` file using python-pptx.

## FIRST decide: build a deck, or just explain?

Before doing anything, judge what the user actually wants:

- **Only asking HOW / for the method / the steps** — questions like “如何制作
  PPT / 怎么做 PPT / 怎样用这个工具 / PPT 怎么生成 / how do I make a ppt”.
  Here the user wants an **explanation**, NOT a file. Answer in words
  (describe the process, options, themes) and **do NOT call `build.py` at all**.
  Building a deck they didn't ask for is wrong and wastes the call budget.
- **Actually wants a deck** — “帮我做一个关于 X 的 PPT / 生成一份 X 的演示 /
  make me slides about X / 把这些内容做成 PPT”. There is a concrete topic or
  content to turn into slides. Only THEN proceed with the steps below.

If it's ambiguous, ask one short clarifying question instead of building.

## Steps

0. Gather the content FIRST. The current date is **{{CURRENT_DATE}}** — resolve
   "今年/今天/最新" against it (e.g. "今年" = the current year, NOT your
   training-data year) when choosing the topic, title, and search terms. If the
   topic is factual, current, or news-like (markets, prices, "今年/今天"
   summaries, a company, a technology), use the web-research skill to collect
   real facts BEFORE building — otherwise the deck will be thin and generic.
   Build the deck from what you gathered plus your own knowledge.

1. Decide the deck's content. Aim for a SUBSTANTIVE deck, not a single slide:

   - A title slide PLUS **at least 4–6 content slides** (more for a rich topic).
   - **Do NOT make every content slide just a list of bare bullets.** Mix in a
     short prose `paragraph` that explains/contextualizes, and use a `table`
     whenever you have comparisons, figures, or structured data (车企对比、数据
     汇总、时间线、优缺点等). A page that is only scattered one-line bullets is
     too thin — combine 成段阐述 + 要点 + 表格 where it fits.
   - A good default structure: 概述/背景 → 几个主题分节（每节一页）→ 关键数据
     /事件 → 总结/展望. Adapt to the topic. Use sub-bullets (`level`) for detail.
   - "简单/simple" means clear and well-organized — it still needs real content
     across several slides, NOT just one slide.

   Produce ONE JSON object (the "spec") with this shape:

   ```json
   {
     "title": "Deck title (title slide)",
     "subtitle": "Optional subtitle / author / date",
     "output": "deck.pptx",
     "slides": [
       {"title": "Slide heading", "bullets": ["point one", "point two"]},
       {"title": "Rich slide", "blocks": [
         {"type": "paragraph", "text": "一段完整的文字阐述，介绍背景或解释要点……"},
         {"type": "bullets", "items": ["要点一", {"text": "子要点", "level": 1}]},
         {"type": "table", "headers": ["车企", "销量", "份额"],
          "rows": [["比亚迪", "300万", "35%"], ["特斯拉", "60万", "7%"]]}
       ], "notes": "speaker notes"}
     ]
   }
   ```

   - Prefer this object shape. (A bare list of slide objects is also accepted —
     it's treated as `slides` with a default deck title — but the object form
     lets you set the title/subtitle/output.)
   - `title` is recommended; if omitted it defaults to the first slide's
     heading. `subtitle`, `output`, and per-slide `notes` are optional.
   - Each slide needs a `title`. For its body, use EITHER:
     - `bullets`: a list of strings (a bullet may be `{"text": "...", "level": 1}`
       to indent it; level 0 = top, 1/2 = nested), OR
     - `blocks`: an ordered list mixing block types for a richer page —
       - `{"type": "paragraph", "text": "..."}` — 成段正文（支持 `\n` 换行）。
       - `{"type": "bullets", "items": [...]}` — 要点（同 bullet 规则，支持 `level`）。
       - `{"type": "table", "headers": [...], "rows": [[...], ...]}` — 表格；
         `headers` 可省略，`rows` 是二维数组。用于对比/数据。
       - `{"type": "chart", "chart_type": "bar|column|line|pie",
          "categories": [...], "series": {"系列名": [数值, ...]}}` — 原生可编辑
          图表。有趋势/占比/对比的数值时优先用图表而非纯文字（折线=趋势、
          饼图=占比、柱状=对比）。`series` 的每个数组长度应与 `categories` 一致。
       - `{"type": "image", "src": "<图片URL或本地路径>", "caption": "图注"}` —
          图片。`src` 可为 http(s) 链接（会被下载）或 `generated/` 下的文件名。
          也可只给 `{"type": "image", "prompt": "<画面描述>"}`，在配置了
          DASHSCOPE_API_KEY 时自动生成配图（取不到图则该块自动跳过，不影响其余）。
     Keep each page within roughly one screen so it does not overflow. An image
     placed on a slide that also has paragraph/bullets text is laid out
     **side by side** (text on the left, image on the right), so it is fine to
     pair an image with a normal amount of text on the same slide — just avoid a
     full-width table or chart on that same slide (those take the whole width).
   - If `output` is omitted it defaults to `deck.pptx`. Use a `.pptx` name.

   Make it look polished with a **theme** (top-level `theme` field):
   - **Let the user choose the style (人工介入).** Before building, call
     `request_user_choice` with `prompt="请选择 PPT 模板风格"`,
     `options=["default", "business-blue", "tech-dark", "minimal"]`, and
     `preview_kind="ppt-theme"` (so the UI shows a visual preview of each template).
     Call it exactly once. Use the returned value verbatim as the top-level `theme`
     in the spec — do not pick the theme yourself when this tool is available.
   - Built-in names: `default`, `business-blue`, `tech-dark`, `minimal`
     (`tech-dark` suits tech, `business-blue` 商务/财经, `minimal` clean/academic;
     `default` is the plain look).
   - A named theme gives the deck real design: a **gradient background**, a
     **colored header band behind each slide title** (white title text), and a
     **footer bar with page numbers** — so prefer setting a `theme` whenever you
     want a designed/non-blank background. `default` stays plain (no background).
   - A theme sets title color, body text color, background, font, and the table
     header color — applied consistently across all slides. Example:
     `{"theme": "business-blue", "title": "...", "slides": [...]}`.
   - For fine control you may pass an inline object instead of a name:
     `"theme": {"base": "minimal", "title": "1F4E79", "table_header": "2E75B6"}`
     (hex colors, no leading `#`).
   - Optional `template`: the name of a `.pptx` in `skills/ppt/templates/` to use
     as the design base. Only set this if such a file exists; otherwise the theme
     alone already gives a rich look.

1.5. (Optional) Add imagery — but sparingly and within a hard budget.

   A few well-chosen images make a deck vivid; an image on every slide makes it
   slow and noisy. Only illustrate the cover and 1–2 key content slides.

   Two ways to get an image's `src`, in this order of preference:
   - **Search a real photo** (preferred for factual/news/product topics): run
     `run_skill_script(skill="ppt", script="search_image.py", script_args=["<query>"])`.
     It prints `{"images": [{"url", "description"}, ...]}`. Pick the most relevant
     URL and use it as the image block's `src`. Use a concrete English or Chinese
     query (e.g. "BYD electric car 2025", "上海陆家嘷 skyline").
   - **Generate one** (for abstract/conceptual slides where no real photo fits):
     give the image block a `prompt` instead of a `src` — it is generated at build
     time when DASHSCOPE_API_KEY is set.

   **Image budget — a hard cap, not a goal:**
   - At most **3 images total** in the whole deck.
   - At most **2 `search_image.py` calls** total (each with a meaningfully
     different query — never re-run a near-identical one).
   - If a search returns nothing usable, STOP searching — skip the image and move
     on. A deck with text/tables/charts and no image is perfectly fine.
   - Never block the deck on imagery: build.py downloads/generates at build time
     and silently skips any image it cannot fetch, so the deck always completes.

2. Put ALL slides into ONE spec and run the script exactly ONCE.

   IMPORTANT: `build.py` rebuilds the WHOLE file from the spec — it OVERWRITES,
   it does NOT append. Calling it once per slide just keeps replacing the deck
   with a single slide. So the `slides` array must contain every slide, and you
   call the script a single time.

   Pass the spec as raw JSON text — do NOT wrap it in extra quotes or JSON-encode
   it a second time, and use the exact keys `title` / `slides` / `bullets`:
   `run_skill_script(skill="ppt", script="build.py", script_args=["<the JSON spec>"])`

   `script_args` must be a list with the ENTIRE spec as ONE single string element.
   Do NOT split the spec across multiple list items — a split spec is read only up
   to the first piece and fails with "invalid JSON spec".

   The script writes the file and prints JSON like
   `{"ok": true, "path": "/abs/path/deck.pptx", "slides": N, "message": ...}`.

3. On `{"ok": true, ...}`, you are DONE: do NOT call build.py again (not to add
   slides, not to "improve" it). Report the saved file path plus a short summary
   (number of slides, titles) to the user.

## If the build fails — STOP after one retry

`run_skill_script` for build.py may be called **at most twice total** per
request. This is a hard cap.

- On `{"ok": false, "error": ...}`, read the error, fix the spec ONCE, and rerun
  a single time. Do NOT keep re-running the script with small variations — that
  loops until the tool budget aborts the whole request.
- If the second attempt still fails, STOP. Tell the user the deck couldn't be
  generated and quote the error message — do not try a third time.

Keep slides concise — a heading plus 3–6 short bullets each reads best. Do not
write the .pptx by hand; always go through `build.py`.
