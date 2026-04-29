# Uni-Agent

Local Codex CLI workspace for a FH Technikum Wien Moodle assistant.

The assistant is designed to:

- log in to Moodle through `agent-browser`
- index visible courses from `https://moodle.technikum-wien.at/my/`
- collect allowed course material
- generate summaries and study notes with citations
- assist with quizzes under strict no-final-submit guardrails

The agent must never perform a final Moodle submission. It can ask isolated subagents to answer visible quiz questions, fill validated answers, traverse the quiz pages, and then stop before final submission.

## Setup

```bash
npm install
npm run browser:install
python3 -m pip install -r requirements.txt
```

Create a local `.env` from `.env.example`. `.env` is ignored by Git.

## Common Commands

```bash
scripts/moodle_login.sh
scripts/moodle_snapshot.sh https://moodle.technikum-wien.at/my/
scripts/moodle_course_index.sh
scripts/moodle_download_materials.sh --course-limit 3 --download-limit 5
scripts/study_buddy.sh "find the next math quiz"
scripts/study_buddy.sh "do the next math quiz"
scripts/study_buddy.sh "do the next math quiz" --auto-answer
scripts/study_buddy.sh "do the next math quiz" --answers output/answers.json
scripts/quiz_assist.sh <quiz-url>
scripts/quiz_assist.sh <quiz-url> --fill-safe --auto-answer
scripts/quiz_assist.sh <quiz-url> --fill-safe --answers output/example-answers.json
```

## Prompt Runner

`scripts/study_buddy.sh` accepts natural-language prompts and maps them to Moodle actions. It can identify likely courses and quizzes from prompts like `next math quiz`, inspect a selected quiz, create an answer template, ask subagents to answer questions from extracted text/screenshots, or fill a quiz from a provided answer JSON.

`--auto-answer` now creates one isolated packet per visible question under `output/subagent-runs/`. Each packet contains extracted question text, visible controls/options, Moodle page metadata, local source excerpts, and a page screenshot. By default the runner calls `codex exec` as a read-only subagent when available. Set `SUBAGENT_SOLVER_COMMAND` to a custom command, or set it to `off` to only generate packets and leave questions unfilled.

Quiz filling defaults to a `--max-pages` cap of 100, so it attempts the whole quiz until Moodle has no safe next-page navigation left. Lower this value only for testing.

If the prompt is ambiguous, it writes a clarification request under `output/requests/` with the likely course/quiz choices and exact next commands.

## Guarded Quiz Filling

`--fill-safe` fills from the provided answer file by default, even if the review-only classifier would mark the page ambiguous. It still requires confidence and citations, and it never clicks final submission controls.

`--respect-review-only` exists only as an opt-in conservative diagnostic mode. Do not use it for normal quiz work.

Answer file shape:

```json
{
  "answers": [
    {
      "question_index": 1,
      "answer": "4 cm^2",
      "confidence": 1.0,
      "citations": [
        {
          "title": "Moodle Beispielkurs visible question",
          "kind": "moodle_page"
        }
      ]
    }
  ]
}
```

Generated user-facing artifacts are written to `output/`.
