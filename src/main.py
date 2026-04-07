import argparse
import sys
import os

def cmd_init(args):
    # init Logic
    print("Initializing a new git-internal repository...")

def main():
    parser = argparse.ArgumentParser(description="A mini-git implementation from scratch.")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new repo")
    init_parser.add_argument("path", default=".", nargs="?", help="Where to create the repository")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command is None:
        parser.print_help()

if __name__ == "__main__":
    main()