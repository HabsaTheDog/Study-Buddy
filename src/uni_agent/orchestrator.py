from __future__ import annotations

import argparse

from .courses import index_courses
from .documents import download_materials, refresh_document_index
from .moodle import login, snapshot
from .quiz import assist_quiz, fill_quiz
from .storage import ROOT, ensure_dirs


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
