import argparse
import sys
import os
import hashlib
import zlib
import struct
import collections
import time

IndexEntry = collections.namedtuple('IndexEntry', [
    'ctime_s', 'ctime_n', 'mtime_s', 'mtime_n', 'dev', 'ino',
    'mode', 'uid', 'gid', 'size', 'sha1', 'flags', 'path'
])

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
    
def read_index():
    """Reads the binary index file and returns a list of IndexEntry objects."""
    index_path = os.path.join(".git", "index")
    if not os.path.exists(index_path):
        return []
    
    with open(index_path, "rb") as f:
        data = f.read()
    
    # Validation: Header is 12 bytes (DIRC + version + count)
    signature = data[:4]
    if signature != b"DIRC":
        raise Exception("Not a valid Git index")
    
    count = struct.unpack("!I", data[8:12])[0]
    
    entries = []
    offset = 12
    for _ in range(count):
        # Unpack the fixed-length part of the entry (62 bytes)
        fields = list(struct.unpack("!LLLLLLLLLL20sH", data[offset:offset+62]))
        
        fields[10] = fields[10].hex()
        
        path_end = data.find(b"\x00", offset + 62)
        path = data[offset+62:path_end].decode("utf-8")
        
        entry = IndexEntry(*fields, path)
        entries.append(entry)
        
        # Entries are padded to 8-byte boundaries
        entry_len = ((62 + len(path) + 8) // 8) * 8
        offset += entry_len
    
    return entries

def write_index(entries):
    """Writes a list of IndexEntry objects to the binary index file."""
    entries.sort(key=lambda x: x.path)
    
    header = b"DIRC" + struct.pack("!II", 2, len(entries))
    body = b""
    for e in entries:
        path_bytes = e.path.encode("utf-8")
        # Pack the fixed fields + SHA1 + Flags
        entry_data = struct.pack("!LLLLLLLLLL20sH", 
            e.ctime_s, e.ctime_n, e.mtime_s, e.mtime_n, e.dev, e.ino,
            e.mode, e.uid, e.gid, e.size, bytes.fromhex(e.sha1), e.flags)
        
        # Add path and null-padding to 8-byte boundary
        entry_data += path_bytes + b"\x00"
        while len(entry_data) % 8 != 0:
            entry_data += b"\x00"
        body += entry_data

    # Add a SHA-1 checksum of the content at the end
    content = header + body
    sha1_checksum = hashlib.sha1(content).digest()
    
    with open(os.path.join(".git", "index"), "wb") as f:
        f.write(content + sha1_checksum)

def cmd_add(paths):
    """The entry point for the 'add' command."""
    entries = {e.path: e for e in read_index()}
    
    for path in paths:
        with open(path, "rb") as f:
            data = f.read()
            sha1 = hash_object(data, write=True)
            
            st = os.stat(path)
            flags = len(path) & 0xFFF # Basic flags: just the path length
            
            entries[path] = IndexEntry(
                int(st.st_ctime), 0, int(st.st_mtime), 0,
                st.st_dev, st.st_ino, 0o100644, st.st_uid, st.st_gid,
                st.st_size, sha1, flags, path
            )
            
    write_index(list(entries.values()))

def write_tree():
    """Converts the current Index into a Tree object and returns its SHA-1."""
    entries = read_index()
    tree_content = b""
    
    for e in entries:
        # Format: [mode] [path]\x00[20-byte binary SHA-1]
        mode_path = f"{e.mode:o} {e.path}".encode("utf-8")
        tree_content += mode_path + b"\x00" + bytes.fromhex(e.sha1)
        
    return hash_object(tree_content, obj_type="tree")

def cmd_commit(message, author="Jayant Sharma <jayant@example.com>"):
    """Creates a commit object and updates the master branch."""
    tree_sha1 = write_tree()
    
    # Check if there's a parent commit (the current hash in refs/heads/master)
    master_path = os.path.join(".git", "refs", "heads", "master")
    parent = None
    if os.path.exists(master_path):
        with open(master_path, "r") as f:
            parent = f.read().strip()

    # Build the commit object content
    now = int(time.time())
    timezone = "+0530"
    
    content = f"tree {tree_sha1}\n"
    if parent:
        content += f"parent {parent}\n"
    content += f"author {author} {now} {timezone}\n"
    content += f"committer {author} {now} {timezone}\n"
    content += f"\n{message}\n"
    
    # Save the commit object
    commit_sha1 = hash_object(content.encode("utf-8"), obj_type="commit")
    
    # Update the master branch pointer to this new commit
    os.makedirs(os.path.dirname(master_path), exist_ok=True)
    with open(master_path, "w") as f:
        f.write(commit_sha1 + "\n")
        
    print(f"[{commit_sha1[:7]}] {message}")
    return commit_sha1

def read_tree(tree_sha1):
    """Parses a tree object and returns a list of (mode, path, sha1)."""
    obj_type, data = read_object(tree_sha1)
    if obj_type != "tree":
        raise Exception(f"Object {tree_sha1} is not a tree")

    entries = []
    i = 0
    while i < len(data):
        # Format: [mode] [path]\x00[20-byte SHA-1]
        space_pos = data.find(b" ", i)
        null_pos = data.find(b"\x00", space_pos)
        
        mode = data[i:space_pos].decode()
        path = data[space_pos + 1:null_pos].decode()
        sha1 = data[null_pos + 1:null_pos + 21].hex()
        
        entries.append((mode, path, sha1))
        i = null_pos + 21
    return entries

def resolve_sha1(short_sha):
    """Finds a full 40-char SHA-1 from a prefix."""
    if len(short_sha) == 40:
        return short_sha
    
    if len(short_sha) < 4:
        raise Exception("Prefix too short (ambiguous)")

    obj_dir = os.path.join(".git", "objects", short_sha[:2])
    if not os.path.exists(obj_dir):
        return None
    
    prefix = short_sha[2:]
    matches = [f for f in os.listdir(obj_dir) if f.startswith(prefix)]
    
    if not matches:
        return None
    if len(matches) > 1:
        raise Exception(f"Ambiguous prefix {short_sha}: matches {len(matches)} objects")
        
    return short_sha[:2] + matches[0]

def read_object(sha1_prefix):
    """Helper to read and decompress an object from the store."""
    sha1 = resolve_sha1(sha1_prefix)
    if not sha1:
        raise Exception(f"Object {sha1_prefix} not found")
        
    path = os.path.join(".git", "objects", sha1[:2], sha1[2:])
    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())
        
    header, content = raw.split(b"\x00", 1)
    obj_type, size = header.decode().split(" ")
    return obj_type, content

def cmd_checkout(commit_hash):
    """Restores the working directory to the state of a specific commit."""
    # Get the Tree hash from the Commit
    obj_type, data = read_object(commit_hash)
    if obj_type != "commit":
        raise Exception("Can only checkout commits")
    
    # Simple parsing to find the 'tree' line
    lines = data.decode().splitlines()
    tree_sha1 = lines[0].split(" ")[1]

    # Recursively write files from the tree
    def unpack_tree(sha1, base_path="."):
        entries = read_tree(sha1)
        for mode, path, entry_sha1 in entries:
            full_path = os.path.join(base_path, path)
            
            obj_type, content = read_object(entry_sha1)
            if obj_type == "blob":
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(content)

    unpack_tree(tree_sha1)
    
    # Update the Index to match this commit
    print(f"Switched to commit {commit_hash[:7]}")

def cmd_log():
    """Traverses the commit graph and prints the history."""
    # Find the starting point (the latest commit hash)
    master_path = os.path.join(".git", "refs", "heads", "master")
    if not os.path.exists(master_path):
        print("No commits yet.")
        return

    with open(master_path, "r") as f:
        current_hash = f.read().strip()

    # Walk backwards through the parents
    while current_hash:
        obj_type, data = read_object(current_hash)
        content = data.decode()
        
        # Simple parsing to find parent and message
        lines = content.splitlines()
        parent = None
        message = ""
        
        msg_start = content.find("\n\n")
        message = content[msg_start:].strip()

        for line in lines:
            if line.startswith("parent "):
                parent = line.split(" ")[1]
            elif line.startswith("author "):
                author_info = line[7:]

        print(f"\033[33mcommit {current_hash}\033[0m") # Yellow text for hash
        print(f"Author: {author_info}")
        print(f"\n    {message}\n")
        
        current_hash = parent

def cmd_status():
    """Compares the Workspace, Index, and HEAD to show differences."""
    index_entries = {e.path: e for e in read_index()}
    
    # Get all files in the current directory (skipping .git)
    workspace_files = []
    for root, _, files in os.walk("."):
        if ".git" in root: continue
        for f in files:
            workspace_files.append(os.path.relpath(os.path.join(root, f), "."))

    print("On branch master\n")

    # Check for untracked or modified files
    untracked = []
    modified = []
    for f_path in workspace_files:
        if f_path not in index_entries:
            untracked.append(f_path)
        else:
            entry = index_entries[f_path]
            stat = os.stat(f_path)
            
            # O(1) Check: Compare metadata before doing heavy hashing
            if int(stat.st_mtime) == entry.mtime_s and int(stat.st_size) == entry.size:
                continue
                
            # If metadata differs, then we do the O(N) hash check
            with open(f_path, "rb") as f:
                current_hash = hash_object(f.read(), write=False)
            if current_hash != entry.sha1:
                modified.append(f_path)

    if modified:
        print("Changes not staged for commit:")
        for f in modified: print(f"  modified: {f}")
        print()

    if untracked:
        print("Untracked files:")
        for f in untracked: print(f"  {f}")

def main():
    parser = argparse.ArgumentParser(description="A mini-git implementation from scratch.")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new repo")
    init_parser.add_argument("path", default=".", nargs="?", help="Where to create the repository")
    
    # hash-object
    hash_parser = subparsers.add_parser("hash-object", help="Hash object and optionally write to database")
    hash_parser.add_argument("file", help="The file to hash")
    hash_parser.add_argument("-w", action="store_true", help="Write the object to the database")

    # add
    add_parser = subparsers.add_parser("add", help="Add file contents to the index")
    add_parser.add_argument("paths", nargs="+", help="Files to add to the index")

    # commit
    commit_parser = subparsers.add_parser("commit", help="Record changes to the repository")
    commit_parser.add_argument("-m", "--message", required=True, help="The commit message")

    # checkout
    checkout_parser = subparsers.add_parser("checkout", help="Restore working tree files")
    checkout_parser.add_argument("commit_hash", help="The SHA-1 hash of the commit to checkout")

    # log
    subparsers.add_parser("log", help="Display commit history")

    # status
    subparsers.add_parser("status", help="Show the working tree status")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.path)
    elif args.command == "hash-object":
        with open(args.file, "rb") as f:
            print(hash_object(f.read(), write=args.w))
    elif args.command == "add":
        cmd_add(args.paths)
    elif args.command == "commit":
        cmd_commit(args.message)
    elif args.command == "checkout":
        cmd_checkout(args.commit_hash)
    elif args.command == "log":
        cmd_log()
    elif args.command == "status":
        cmd_status()
    elif args.command is None:
        parser.print_help()

if __name__ == "__main__":
    main()