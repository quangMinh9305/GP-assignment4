"""
Entry point for the Custom UNO Online client.

Usage
-----
    python main_client.py
    python main_client.py --assets path/to/Assets

The in-game menu handles host/join and player name.
"""
import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Custom UNO Online")
    parser.add_argument("--assets", default="Assets",
                        help="Path to the assets folder (default: Assets)")
    args = parser.parse_args()

    base_dir    = os.path.dirname(os.path.abspath(__file__))
    assets_path = os.path.join(base_dir, args.assets)

    if not os.path.isdir(assets_path):
        print(f"Error: assets folder not found: {assets_path}", file=sys.stderr)
        sys.exit(1)

    from client.ui import GameClient
    GameClient(assets_path).run()


if __name__ == "__main__":
    main()
