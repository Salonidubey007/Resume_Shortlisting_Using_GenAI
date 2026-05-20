import argparse
from pathlib import Path
from dotenv import load_dotenv

from engines.resume_engine import parse_resumes
from engines.jd_engine import parse_jd
from engines.scoring_engine import score_all_resumes

# Ensure .env is loaded for Cloudflare creds in CLI mode
load_dotenv(override=True)

DATA_RESUMES_DIR = Path("data/resumes")
DATA_JDS_DIR = Path("data/jds")


def build_cli():
    parser = argparse.ArgumentParser(
        description="Document Similarity Pipeline CLI (resumes → JD → scoring)"
    )

    parser.add_argument(
        "--resumes",
        action="store_true",
        help="Parse all resumes inside data/resumes",
    )

    parser.add_argument(
        "--jd",
        type=str,
        help="Parse selected JD file from data/jds folder (pass only the filename, e.g., 'MyJD.pdf')",
    )

    parser.add_argument(
        "--score",
        action="store_true",
        help="Run scoring using parsed JD (jd_latest pointer) and resumes (resumes_master.json)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reparse (re-extract resume or JD JSON even if cached)",
    )

    parser.add_argument(
        "--topn",
        type=int,
        default=100,
        help="Top-N to include in the Excel summary (full scoring still runs for all). Default: 100",
    )

    # Optional: control parallelism for large batches (200–1000 resumes)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=6,
        help="Concurrency for scoring (and parsing where applicable). Default: 6",
    )

    return parser


def main():
    parser = build_cli()
    args = parser.parse_args()

    # Ensure input folders exist
    DATA_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_JDS_DIR.mkdir(parents=True, exist_ok=True)

    did_anything = False

    if args.resumes:
        did_anything = True
        print("\n=== STEP 1: Parsing resumes ===")
        parse_resumes(
            DATA_RESUMES_DIR,
            force_reparse=args.force,
            verbose=True,
            max_workers=args.max_workers,
        )

    if args.jd:
        did_anything = True
        print("\n=== STEP 2: Parsing JD ===")
        jd_path = DATA_JDS_DIR / args.jd
        if not jd_path.exists():
            print(f"[ERROR] JD not found: {jd_path}")
            return
        parse_jd(
            jd_path,
            force_reparse=args.force,
            verbose=True,
        )

    if args.score:
        did_anything = True
        print("\n=== STEP 3: Scoring all resumes ===")
        # Note: score_all_resumes already loads jd_latest pointer and resumes_master.json
        score_all_resumes(
            top_n=args.topn,
            verbose=True,
            max_workers=args.max_workers,
        )

    if not did_anything:
        print("No step selected. Use --resumes, --jd <file>, or --score")


if __name__ == "__main__":
    main()