import argparse
import sys
import os
import hashlib
import zlib

def cmd_init(repo_path="."):
    """Creates the basic Git directory structure."""
    git_dir = os.path.join(repo_path, ".git")
    if(os.path.exists(git_dir)):
        print(f"Directory already exists: {git_dir}")
        return
    
    # Create directories
    os.makedirs(os.path.join(git_dir, "objects"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
    
    # Create the HEAD file
    with open(os.path.join(git_dir, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")
     
    print(f"Initialized empty Git repository in {os.path.abspath(git_dir)}")

def hash_object(data, obj_type="blob", write=True):
    """Hashes an object, optionally writing it to the object store."""
    # Header: "type size\x00"
    header = f"{obj_type} {len(data)}".encode() 
    full_data = header + b"\x00" + data
    
    sha1 = hashlib.sha1(full_data).hexdigest()
    
    if write:
        # Path : .git/objects/first2/remaining38
        path = os.path.join(".git", "objects", sha1[:2], sha1[2:])
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(zlib.compress(full_data))
    
    return sha1
    

def main():
    parser = argparse.ArgumentParser(description="A mini-git implementation from scratch.")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new repo")
    init_parser.add_argument("path", default=".", nargs="?", help="Where to create the repository")
    
    # hash-object
    hash_parser = subparsers.add_parser("hash-object")
    hash_parser.add_argument("file", help="The file to hash")
    hash_parser.add_argument("-w", action="store_true", help="Write the object to the database")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.path)
    elif args.command == "hash-object":
        with open(args.file, "rb") as f:
            print(hash_object(f.read(), write=args.w))
    elif args.command is None:
        parser.print_help()

if __name__ == "__main__":
    main()