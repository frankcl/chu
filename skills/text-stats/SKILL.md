---
name: text-stats
description: Count characters, words, and lines of a piece of text. Use when the user asks how long a text is or for word/character/line counts.
---

# Text Stats

Use this skill when the user wants statistics about some text (character count,
word count, or line count).

## Steps

1. Identify the exact text the user wants measured.
2. Run the bundled script, passing the text as a single argument:
   `run_skill_script(skill="text-stats", script="count.py", script_args=[<the text>])`
3. The script prints a JSON object like `{"chars": N, "words": N, "lines": N}`.
4. Report the numbers back to the user in clear natural language.

Do not count by hand — always use `count.py` so the numbers are exact.
