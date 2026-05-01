from __future__ import annotations

import argparse

from .courses import index_courses
from .documents import download_materials, refresh_document_index
from .moodle import login, snapshot
from .quiz import assist_quiz, fill_quiz
from .storage import ROOT, ensure_dirs
from .study_docs import generate_study_document
from .sync import sync_moodle


def main() -> None:
    parser = argparse.ArgumentParser(prog="uni-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login")

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("url", nargs="?")

    subparsers.add_parser("courses")
    materials_parser = subparsers.add_parser("materials")
    materials_parser.add_argument("--download-limit", type=int, default=0)
    materials_parser.add_argument("--course-limit", type=int, default=0)

    subparsers.add_parser("documents")

    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--course-limit", type=int, default=0)
    sync_parser.add_argument("--download", action="store_true", help="Compatibility flag. Downloads are enabled by default.")
    sync_parser.add_argument("--no-download", action="store_true")
    sync_parser.add_argument("--incremental", action="store_true", help="Keep existing material cache and merge indexes instead of starting clean.")
    sync_parser.add_argument(
        "--download-limit-per-course",
        type=int,
        default=0,
        help="Maximum files to download per course. 0 means unlimited.",
    )
    sync_parser.add_argument("--max-bytes-per-file", type=int, default=100_000_000)

    study_doc_parser = subparsers.add_parser("study-doc")
    study_doc_parser.add_argument("prompt", nargs="+")
    study_doc_parser.add_argument(
        "--format",
        default="markdown+pdf",
        choices=["markdown+pdf", "markdown", "typst", "pdf"],
    )
    study_doc_parser.add_argument("--style", default="academic-study-guide")
    study_doc_parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "exam-study-guide", "summary", "cheat-sheet", "assignment-brief", "generic"],
    )

    quiz_parser = subparsers.add_parser("quiz")
    quiz_parser.add_argument("url")
    quiz_parser.add_argument("--fill-safe", action="store_true")
    quiz_parser.add_argument("--answers")
    quiz_parser.add_argument("--auto-answer", action="store_true")
    quiz_parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Safety cap for quiz pages. Defaults to 100 so the whole quiz is attempted until safe navigation stops.",
    )
    quiz_parser.add_argument("--no-start", action="store_true")
    quiz_parser.add_argument(
        "--force-fill",
        action="store_true",
        help="Deprecated compatibility flag. Fill mode already bypasses review-only classification.",
    )
    quiz_parser.add_argument(
        "--respect-review-only",
        action="store_true",
        help="Use the review-only classifier in fill mode instead of the default force-fill behavior.",
    )

    args = parser.parse_args()
    ensure_dirs()

    if args.command == "login":
        result = login()
        print(f"Logged in or already authenticated. Current URL: {result['url_after']}")
    elif args.command == "snapshot":
        print(snapshot(args.url))
    elif args.command == "courses":
        courses = index_courses()
        print(f"Indexed {len(courses)} courses into state/course_index.json")
    elif args.command == "materials":
        target = download_materials(
            download_limit=args.download_limit,
            course_limit=args.course_limit or None,
        )
        print(f"Wrote material index to {target.relative_to(target.parents[1])}")
    elif args.command == "documents":
        target = refresh_document_index()
        print(f"Wrote {target.relative_to(target.parents[1])}")
    elif args.command == "sync":
        run_dir = sync_moodle(
            course_limit=args.course_limit or None,
            download=not args.no_download,
            download_limit_per_course=args.download_limit_per_course,
            max_bytes_per_file=args.max_bytes_per_file,
            clean=not args.incremental,
        )
        print(f"Wrote Moodle sync report to {run_dir}")
    elif args.command == "study-doc":
        run_dir = generate_study_document(
            " ".join(args.prompt),
            output_format=args.format,
            style=args.style,
            mode=args.mode,
        )
        print(f"Wrote study document to {run_dir}")
    elif args.command == "quiz":
        if args.fill_safe:
            if not args.answers and not args.auto_answer:
                raise SystemExit("--fill-safe requires --answers <path> or --auto-answer")
            run_dir = fill_quiz(
                args.url,
                answers_path=ROOT / args.answers if args.answers else None,
                max_pages=args.max_pages,
                start_attempt=not args.no_start,
                force_fill=not args.respect_review_only,
                auto_answer=args.auto_answer,
            )
            print(f"Wrote quiz fill report to {run_dir}")
        else:
            run_dir = assist_quiz(args.url)
            print(f"Wrote quiz review to {run_dir}")


if __name__ == "__main__":
    main()
