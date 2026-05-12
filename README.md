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
scripts/study_buddy.sh "do the next math quiz" --answers answers.json
scripts/study_buddy.sh "generate a study guide for Integralrechnung 2"
scripts/study_build.sh "DYN2 exam study guide" --format markdown+pdf
scripts/study_build.sh "summarize MAES2 definite integrals as a PDF" --format markdown+pdf
scripts/study_build.sh "make a formula sheet for DYN2" --format markdown+pdf
scripts/quiz_assist.sh <quiz-url>
scripts/quiz_assist.sh <quiz-url> --fill-safe --auto-answer
scripts/quiz_assist.sh <quiz-url> --fill-safe --answers answers.json
```

## Prompt Runner

`scripts/study_buddy.sh` accepts natural-language prompts and maps them to Moodle actions. It can identify likely courses and quizzes from prompts like `next math quiz`, inspect a selected quiz, create an answer template, ask subagents to answer questions from extracted text/screenshots, or fill a quiz from a provided answer JSON.

The prompt runner prefers the synced course cards in
`state/course_agent_cards.json` when routing a prompt to a Moodle course or
known quiz/activity URL. If no sync exists, it falls back to the older course
indexing path and can trigger a fast metadata-only sync when no course index is
available.

`--auto-answer` creates one isolated packet per visible question inside the current quiz run folder. Each packet contains extracted question text, visible controls/options, Moodle page metadata, local source excerpts, and a page screenshot. By default the runner calls `codex exec` as a read-only subagent when available. Set `SUBAGENT_SOLVER_COMMAND` to a custom command, or set it to `off` to only generate packets and leave questions unfilled.

Quiz filling defaults to a `--max-pages` cap of 100, so it attempts the whole quiz until Moodle has no safe next-page navigation left. Lower this value only for testing.

If the prompt is ambiguous, it writes one clarification folder directly under `output/` with the likely course/quiz choices and exact next commands.

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
- `study_build.sh` uses course cards and the local document index to plan resources, build a source bundle, render Markdown/Typst/PDF, and review the result.
- Quiz subagent packets include the matching course card context when available.

Outputs are written under:

```text
output/<timestamp>_moodle-sync/
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

## Study Build Documents and PDFs

Study-guide, summary, Lernzettel, formula-sheet, assignment-brief, and notes
prompts are routed to the `study-build` pipeline. The orchestrator selects a
course, plans relevant resources, builds a source bundle, sends that bundle to a
specialized builder, renders Markdown/Typst/PDF, and runs a final reviewer. If a
request asks for Moodle-like quiz questions, the pipeline stops and asks for
explicit permission before opening any quiz/test page.

Direct commands:

```bash
scripts/study_build.sh "DYN2 exam study guide" --format markdown+pdf
scripts/study_build.sh "Integralrechnung Zusammenfassung" --format markdown+pdf
scripts/study_build.sh "DYN2 Formelsammlung" --format markdown+pdf
python3 -m uni_agent.orchestrator study-build "DYN2 exam study guide" --format markdown+pdf
```

Quiz access policy for document generation:

- `--quiz-access ask` is the default. The run stops with
  `needs-quiz-authorization` before opening any quiz/test page.
- `--quiz-access none` ignores quiz pages and builds self-check questions only
  from theory sources.
- `--quiz-access authorized` is reserved for a current user instruction naming
  exactly which quiz views may be opened. Final submission remains forbidden.

Outputs are written under:

```text
output/<timestamp>_study-build_<slug>/
```

Expected files include:

```text
study-build.md
study-build.pdf          # only when Typst compilation succeeds
REVIEW.md
SOURCES.md               # human-readable source list
artifacts/               # build inputs, model responses, metadata, copied sources
```

Builder and reviewer model hooks are optional. Without them, `study-build` uses
the local deterministic fallback so the pipeline remains auditable and testable.

Useful study-build settings:

```bash
STUDY_BUILD_BUILDER_COMMAND='... {packet} ... {output} ...'
STUDY_BUILD_REVIEWER_COMMAND='... {packet} ... {output} ...'
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
