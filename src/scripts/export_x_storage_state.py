import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "config" / "x_storage_state.json"
DEFAULT_PROFILE_DIR = PROJECT_ROOT / ".browser_profiles" / "x_auth_bootstrap"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Launch an isolated Chromium profile for manual X login and export a "
            "Playwright storage_state file for the scraper."
        )
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Where to write the exported storage_state JSON.",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help=(
            "Isolated browser user-data directory used only for this login/export flow. "
            "This should not be your regular Chrome profile."
        ),
    )
    parser.add_argument(
        "--x-url",
        default="https://x.com/home",
        help="Initial X page to open before you log in.",
    )
    return parser


def export_storage_state(output_path: Path, profile_dir: Path, x_url: str) -> None:
    # Keep everything inside an isolated user-data directory so the exported auth
    # comes from a browser profile dedicated to X instead of the user's main browser.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        # launch_persistent_context gives us a real browser profile directory where
        # the user can complete X login, 2FA, and any security prompts manually.
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1100},
        )

        try:
            page = context.new_page()
            page.goto(x_url, wait_until="domcontentloaded")

            print()
            print("Browser opened for X login.")
            print("1. Log into x.com in the opened browser window.")
            print("2. Wait until you can see your authenticated X timeline or target profile.")
            print("3. Return here and press Enter to export storage_state.")
            input()

            # Capture the auth/session state into a small reusable JSON artifact
            # that the scraper can later inject without touching other site data.
            context.storage_state(path=str(output_path))
        finally:
            context.close()


def main() -> int:
    args = build_parser().parse_args()
    output_path = Path(args.output).resolve()
    profile_dir = Path(args.profile_dir).resolve()

    export_storage_state(output_path=output_path, profile_dir=profile_dir, x_url=args.x_url)

    print()
    print(f"Saved storage_state to: {output_path}")
    print(f"Isolated browser profile dir: {profile_dir}")
    print()
    print("Next step:")
    print("Set config/x_targets.json auth.storage_state_path to this file and clear user_data_dir/cookies_path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
