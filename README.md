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

Typst is optional but required for PDF compilation. Without it, study document
generation still writes Markdown and Typst source.

```bash
# Fedora example
sudo dnf install typst

# Or install from https://typst.app/docs/
```

Create a local `.env` from `.env.example`. `.env` is ignored by Git.

## Common Commands

```bash
scripts/moodle_login.sh
scripts/moodle_snapshot.sh https://moodle.technikum-wien.at/my/
scripts/moodle_course_index.sh
scripts/moodle_sync.sh
scripts/moodle_sync.sh --no-download
scripts/moodle_download_materials.sh --course-limit 3 --download-limit 5
scripts/study_buddy.sh "find the next math quiz"
scripts/study_buddy.sh "do the next math quiz"
scripts/study_buddy.sh "do the next math quiz" --auto-answer
scripts/study_buddy.sh "do the next math quiz" --answers output/answers.json
scripts/study_buddy.sh "generate a study guide for Integralrechnung 2"
scripts/study_doc.sh "DYN2" --mode exam-study-guide
scripts/study_doc.sh "summarize MAES2 definite integrals as a PDF" --mode summary
scripts/study_doc.sh "make a formula sheet for DYN2" --mode cheat-sheet
scripts/quiz_assist.sh <quiz-url>
scripts/quiz_assist.sh <quiz-url> --fill-safe --auto-answer
scripts/quiz_assist.sh <quiz-url> --fill-safe --answers output/example-answers.json
```

## Prompt Runner

`scripts/study_buddy.sh` accepts natural-language prompts and maps them to Moodle actions. It can identify likely courses and quizzes from prompts like `next math quiz`, inspect a selected quiz, create an answer template, ask subagents to answer questions from extracted text/screenshots, or fill a quiz from a provided answer JSON.

The prompt runner prefers the synced course cards in
`state/course_agent_cards.json` when routing a prompt to a Moodle course or
known quiz/activity URL. If no sync exists, it falls back to the older course
indexing path and can trigger a fast metadata-only sync when no course index is
available.

`--auto-answer` now creates one isolated packet per visible question under `output/subagent-runs/`. Each packet contains extracted question text, visible controls/options, Moodle page metadata, local source excerpts, and a page screenshot. By default the runner calls `codex exec` as a read-only subagent when available. Set `SUBAGENT_SOLVER_COMMAND` to a custom command, or set it to `off` to only generate packets and leave questions unfilled.

Quiz filling defaults to a `--max-pages` cap of 100, so it attempts the whole quiz until Moodle has no safe next-page navigation left. Lower this value only for testing.

If the prompt is ambiguous, it writes a clarification request under `output/requests/` with the likely course/quiz choices and exact next commands.

## Moodle Sync

`scripts/moodle_sync.sh` is the one-command live Moodle refresh. It logs in,
indexes all visible Moodle courses, opens each course page, extracts compact
activity/resource links, downloads accessible course files, refreshes the local
document index, and writes agent course cards.

Default sync is a clean full refresh: it removes previous sync metadata, clears
the downloaded Moodle material cache, downloads accessible files/resources again,
and rebuilds the document index from the fresh cache. Moodle remains the source
of truth for live state such as current quizzes, deadlines, assignments, and
visible page content. Use `--no-download` for a fast metadata-only refresh, or
`--incremental` to keep the existing material cache.

Other tools consume the sync output as their compact course map:

- `study_buddy.sh` uses course cards for course/quiz routing.
- `study_doc.sh` uses course cards to choose the target course, then uses the
  local document index for source excerpts.
- Quiz subagent packets include the matching course card context when available.

Outputs are written under:

```text
output/sync-runs/<timestamp>_moodle-sync/
state/moodle_sync_summary.json
state/course_agent_cards.json
state/course_index.json
state/material_links.json
state/document_index.json
```

Useful commands:

```bash
scripts/moodle_sync.sh
scripts/moodle_sync.sh --course-limit 3
scripts/moodle_sync.sh --no-download
scripts/moodle_sync.sh --incremental
scripts/moodle_sync.sh --download-limit-per-course 20
python3 -m uni_agent.orchestrator sync
```

## Study Documents and PDFs

Study-guide, summary, Lernzettel, cheat-sheet, assignment-brief, and notes
prompts are routed to the study document generator. It selects local indexed
Moodle material from `state/document_index.json`, generates a citation-backed
document, writes auditable Markdown and Typst source, and compiles a PDF when
`typst` is available.

The generator supports explicit document modes:

```bash
scripts/study_doc.sh "DYN2" --mode exam-study-guide
scripts/study_doc.sh "Integralrechnung" --mode summary
scripts/study_doc.sh "DYN2" --mode cheat-sheet
python3 -m uni_agent.orchestrator study-doc "DYN2" --mode exam-study-guide --format markdown+pdf
```

Exam study guides use a learning-focused schema with topic maps, formula cards,
problem-solving methods, practice plans, self-checks, common mistakes, and a
source appendix. The main body uses compact source IDs instead of repeated full
file paths.

Outputs are written under:

```text
output/study-docs/<timestamp>_<slug>/
```

Expected files include:

```text
request.json
sources.json
study-guide.json
study-guide.md
study-guide.typ
render-result.json
study-guide.pdf          # only when Typst compilation succeeds
```

Direct command:

```bash
scripts/study_doc.sh "generate a study guide for integralrechnung"
python3 -m uni_agent.orchestrator study-doc "summary of Integralrechnung" --format markdown
```

By default the generator uses `codex exec` in a read-only subprocess when
available. Generator failures fail the run so timeouts and schema issues remain
visible while debugging. Set `STUDY_DOC_GENERATOR_COMMAND` to a custom command.
Set `STUDY_DOC_GENERATOR_REQUIRED=false` only when a deterministic fallback is
acceptable.

Useful study document settings:

```bash
STUDY_DOC_LANGUAGE=de
STUDY_DOC_GENERATOR_REQUIRED=true
STUDY_DOC_CODEX_REASONING_EFFORT=medium
# STUDY_DOC_CODEX_MODEL=gpt-5.4
STUDY_DOC_CITATION_STYLE=endnotes
STUDY_DOC_DEFAULT_MODE=auto
STUDY_DOC_TIMEOUT_SECONDS=180
```

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
