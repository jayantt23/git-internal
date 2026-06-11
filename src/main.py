import argparse
import sys
import os
import hashlib
import zlib
import struct
import collections
import time
import subprocess

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

IndexEntry = collections.namedtuple('IndexEntry', [
    'ctime_s', 'ctime_n', 'mtime_s', 'mtime_n', 'dev', 'ino',
    'mode', 'uid', 'gid', 'size', 'sha1', 'flags', 'path'
])
   
def read_index():
    """Reads the binary index file and returns a list of IndexEntry objects."""
    index_path = os.path.join(".git", "index")
    if not os.path.exists(index_path):
        return []
    
    with open(index_path, "rb") as f:
        data = f.read()
    
    digest = hashlib.sha1(data[:-20]).digest()
    if digest != data[-20:]:    
        raise Exception("Invalid index checksum")
    
    # Validation: Header is 12 bytes (DIRC + version + count)
    signature, version, count = struct.unpack('!4sLL', data[:12])
    if signature != b"DIRC":
        raise Exception("Not a valid Git index")
    if version != 2:
        raise Exception("Unknown index version")
    
    entry_data = data[12:-20]
    entries = []
    offset = 0
    while offset + 62 < len(entry_data):
        # Unpack the fixed-length part of the entry (62 bytes)
        fields = list(struct.unpack("!LLLLLLLLLL20sH", entry_data[offset:offset+62]))
        
        fields[10] = fields[10].hex()
        
        path_end = entry_data.find(b"\x00", offset + 62)
        path = entry_data[offset+62:path_end].decode("utf-8")
        
        entry = IndexEntry(*fields, path)
        entries.append(entry)
        
        # Entries are padded to 8-byte boundaries
        entry_len = ((62 + len(path) + 8) // 8) * 8
        offset += entry_len
    
    if len(entries) != count:
        raise Exception("Parsed entry count does not match header declaration")
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
            e.ctime_s & 0xFFFFFFFF, 
            e.ctime_n & 0xFFFFFFFF, 
            e.mtime_s & 0xFFFFFFFF, 
            e.mtime_n & 0xFFFFFFFF, 
            e.dev & 0xFFFFFFFF, 
            e.ino & 0xFFFFFFFF,
            e.mode & 0xFFFFFFFF, 
            e.uid & 0xFFFFFFFF, 
            e.gid & 0xFFFFFFFF, 
            e.size & 0xFFFFFFFF, 
            bytes.fromhex(e.sha1), 
            e.flags & 0xFFFF
        )
        
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
    
    expanded_paths = []
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                if ".git" in dirs:
                    dirs.remove(".git")

                for file in files:
                    clean_path = os.path.normpath(os.path.join(root, file))
                    clean_path = clean_path.replace("\\", "/")
                    expanded_paths.append(clean_path)
        else:
            clean_path = os.path.normpath(path)
            clean_path = clean_path.replace("\\", "/")
            expanded_paths.append(clean_path)
    
    for path in expanded_paths:
        st = os.stat(path)
        
        if path in entries:
            existing = entries[path]
            if existing.mtime_s == int(st.st_mtime) and existing.size == st.st_size:
                continue
        
        with open(path, "rb") as f:
            data = f.read()
            sha1 = hash_object(data, write=True)
            
        flags = len(path) & 0xFFF # Basic flags: just the path length
            
        entries[path] = IndexEntry(
            int(st.st_ctime), 0, int(st.st_mtime), 0,
            st.st_dev, st.st_ino, 0o100644, st.st_uid, st.st_gid,
            st.st_size, sha1, flags, path
        )
            
    write_index(list(entries.values()))

def write_tree():
    """Converts the current Index into a recursive Tree object and returns its SHA-1."""
    entries = read_index()
    
    root_tree = {}
    for entry in entries:
        parts = entry.path.split("/")
        current = root_tree
        
        for folder in parts[:-1]:
            if folder not in current:
                current[folder] = {}
            current = current[folder]
        
        current[parts[-1]] = entry
    
    def build_tree_object(node):
        tree_content = b""
        
        for name, item in sorted(node.items()):
            if isinstance(item, dict):
                mode = "40000" # Git's standard octal mode for a directory
                item_sha1 = build_tree_object(item)
            else:
                mode = f"{item.mode:o}"
                item_sha1 = item.sha1
        
            mode_name = f"{mode} {name}".encode("utf-8")
            tree_content += mode_name + b"\x00" + bytes.fromhex(item_sha1)

        return hash_object(tree_content, obj_type="tree")
    
    return build_tree_object(root_tree)

def cmd_commit(message, author="Jayant Sharma <jayant@example.com>"):
    """Creates a commit object and updates the current branch."""
    tree_sha1 = write_tree()
    
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()
    
    is_detached = False
    parent = None
    branch_path = None
    
    if head_content.startswith("ref: "):
        # Normal state: HEAD points to a branch
        ref_path = head_content[5:]
        branch_path = os.path.join(".git", *ref_path.split("/"))
        
        # Check if there's a parent commit on this branch
        if os.path.exists(branch_path):
            with open(branch_path, "r") as f:
                parent = f.read().strip()
    else:
        # Detached HEAD state: HEAD directly contains the parent commit hash
        is_detached = True
        parent = head_content

    if parent:
        obj_type, parent_data = read_object(parent)
        if obj_type == "commit":
            lines = parent_data.decode().splitlines()
            parent_tree_sha1 = lines[0].split(" ")[1]
            
            # If the current staging area perfectly matches the parent commit, abort!
            if tree_sha1 == parent_tree_sha1:
                print("nothing to commit, working tree clean")
                return None

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
    
    if is_detached:
        # In detached HEAD, we update the HEAD file directly
        with open(head_path, "w") as f:
            f.write(commit_sha1 + "\n")
    else:
        # Otherwise, update the dynamic branch pointer
        os.makedirs(os.path.dirname(branch_path), exist_ok=True)
        with open(branch_path, "w") as f:
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
    
    if not os.path.exists(path):
        try:
            obj_type = subprocess.check_output(["git", "cat-file", "-t", sha1]).decode().strip()
            content = subprocess.check_output(["git", "cat-file", obj_type, sha1])
            return obj_type, content
        except subprocess.CalledProcessError:
            raise Exception(f"Object {sha1} is missing from the repository.")
    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())
        
    header, content = raw.split(b"\x00", 1)
    obj_type, size = header.decode().split(" ")
    return obj_type, content

def cmd_checkout(target):
    """Restores the working directory to the state of a specific commit or branch."""
    
    branch_path = os.path.join(".git", "refs", "heads", target)
    is_branch = os.path.exists(branch_path)
    
    if is_branch:
        # If it's a branch, read the commit hash from the branch file
        with open(branch_path, "r") as f:
            commit_hash = f.read().strip()
    else:
        # Otherwise, assume the user passed a commit hash
        commit_hash = resolve_sha1(target)
        if not commit_hash:
            raise Exception(f"Commit {target} not found")
    
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
            elif obj_type == "tree":
                unpack_tree(entry_sha1, full_path)

    unpack_tree(tree_sha1)
    
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "w") as f:
        if is_branch:
            # Reattach HEAD to the branch
            f.write(f"ref: refs/heads/{target}\n")
            print(f"Switched to branch '{target}'")
        else:
            # Detach HEAD to the specific commit
            f.write(commit_hash + "\n")
            print(f"Switched to detached HEAD at {commit_hash[:7]}")
    
    # Update the Index to match this commit
    print(f"Switched to commit {commit_hash[:7]}")

def cmd_log():
    """Traverses the commit graph and prints the history."""
    # Find the starting point (the latest commit hash)
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()
    
    if head_content.startswith("ref: "):
        # Normal state: HEAD points to a branch
        ref_path = head_content[5:]
        branch_path = os.path.join(".git", *ref_path.split("/"))

        if not os.path.exists(branch_path):
            print("No commits yet.")
            return

        with open(branch_path, "r") as f:
            current_hash = f.read().strip()
    else:
        # Detached HEAD state: HEAD directly contains the commit hash
        current_hash = head_content

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
    
    head_path = os.path.join(".git", "HEAD")
    if os.path.exists(head_path):
        with open(head_path, "r") as f:
            head_content = f.read().strip()
            
        if head_content.startswith("ref: "):
            # Extract just the branch name from refs/heads/branch_name
            branch_name = head_content.split("/")[-1]
            print(f"On branch {branch_name}\n")
        else:
            # We are in a detached HEAD state
            print(f"HEAD detached at {head_content[:7]}\n")
    else:
        print("Fatal: Not a git repository")
        return
    
    # Get all files in the current directory (skipping .git)
    workspace_files = []
    for root, _, files in os.walk("."):
        if ".git" in root: continue
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), ".")
            normalized_path = rel_path.replace("\\", "/")
            workspace_files.append(normalized_path)

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
    
    if not modified and not untracked:
        print("nothing to commit, working tree clean")

def cmd_branch(branch_name=None):
    """Creates a new branch or lists existing branches."""
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()

    # Figure out where we are currently
    if head_content.startswith("ref: "):
        current_branch = head_content.split("/")[-1]
        branch_path = os.path.join(".git", *head_content[5:].split("/"))
        with open(branch_path, "r") as f:
            current_hash = f.read().strip()
    else:
        current_branch = "HEAD (detached)"
        current_hash = head_content

    if branch_name:
        # Create a new branch
        new_branch_path = os.path.join(".git", "refs", "heads", branch_name)
        if os.path.exists(new_branch_path):
            print(f"fatal: A branch named '{branch_name}' already exists.")
            return
        
        os.makedirs(os.path.dirname(new_branch_path), exist_ok=True)
        with open(new_branch_path, "w") as f:
            f.write(current_hash + "\n")
        print(f"Created branch '{branch_name}' at {current_hash[:7]}")
    else:
        # List existing branches
        heads_dir = os.path.join(".git", "refs", "heads")
        if not os.path.exists(heads_dir):
            return
        
        branches = os.listdir(heads_dir)
        for b in sorted(branches):
            if b == current_branch:
                print(f"* \033[32m{b}\033[0m") # Green text for active branch
            else:
                print(f"  {b}")
        
        if current_branch == "HEAD (detached)":
            print(f"* \033[32m(HEAD detached at {current_hash[:7]})\033[0m")

def cmd_stats():
    """Analyzes the current commit and prints repository statistics."""

    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()
        
    if head_content.startswith("ref: "):
        branch_path = os.path.join(".git", *head_content[5:].split("/"))
        if not os.path.exists(branch_path):
            print("No commits yet to analyze.")
            return
        with open(branch_path, "r") as f:
            current_hash = f.read().strip()
    else:
        current_hash = head_content

    # Get the Tree hash from the Commit
    obj_type, data = read_object(current_hash)
    lines = data.decode().splitlines()
    tree_sha1 = lines[0].split(" ")[1]

    # Analytics variables
    total_files = 0
    total_size = 0
    extensions = collections.defaultdict(int)

    # Recursively traverse the tree to gather stats
    def analyze_tree(sha1):
        nonlocal total_files, total_size, extensions
        entries = read_tree(sha1)
        
        for mode, path, entry_sha1 in entries:
            obj_type, content = read_object(entry_sha1)
            
            if obj_type == "blob":
                total_files += 1
                total_size += len(content)
                
                # Extract file extension
                ext = os.path.splitext(path)[1]
                if ext:
                    extensions[ext] += 1
                else:
                    extensions["(no extension)"] += 1
                    
            elif obj_type == "tree":
                analyze_tree(entry_sha1)

    analyze_tree(tree_sha1)
    
    # Print the results
    print(f"\nRepository Stats (Commit {current_hash[:7]})")
    print("-" * 40)
    print(f"Total Files: {total_files}")
    print(f"Total Size:  {total_size / 1024:.2f} KB")
    print("\nFile Breakdown:")
    for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
        print(f"  {ext}: {count} files")
    print("-" * 40 + "\n")

def cmd_rewind(steps=1):
    """Safely undoes the last N commits without touching the workspace."""
    steps = int(steps)
    
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()
        
    is_detached = not head_content.startswith("ref: ")
    if is_detached:
        current_hash = head_content
        ref_to_update = head_path
    else:
        branch_path = os.path.join(".git", *head_content[5:].split("/"))
        with open(branch_path, "r") as f:
            current_hash = f.read().strip()
        ref_to_update = branch_path

    # Walk backward 'N' times to find the target ancestor
    target_hash = current_hash
    for i in range(steps):
        obj_type, data = read_object(target_hash)
        content = data.decode()
        
        parent = None
        for line in content.splitlines():
            if line.startswith("parent "):
                parent = line.split(" ")[1]
                break
                
        if not parent:
            print(f"Reached the beginning of history. Can only rewind {i} steps.")
            break
        target_hash = parent

    # Update the pointer to officially "rewind" time
    if target_hash != current_hash:
        with open(ref_to_update, "w") as f:
            f.write(target_hash + "\n")
        print(f"Rewound {steps} commit(s).")
        print(f"HEAD is now at {target_hash[:7]}.")
        print("Your files were not changed. Run 'python3 main.py status' to see your uncommitted work.")

def cmd_graph():
    """Prints a beautiful ASCII visual graph of the commit history."""
    head_path = os.path.join(".git", "HEAD")
    with open(head_path, "r") as f:
        head_content = f.read().strip()
        
    if head_content.startswith("ref: "):
        branch_path = os.path.join(".git", *head_content[5:].split("/"))
        if not os.path.exists(branch_path):
            print("No commits yet.")
            return
        with open(branch_path, "r") as f:
            current_hash = f.read().strip()
    else:
        current_hash = head_content

    print("\nCommit History Graph:")
    
    while current_hash:
        obj_type, data = read_object(current_hash)
        content = data.decode()
        
        lines = content.splitlines()
        parent = None
        message = content[content.find("\n\n"):].strip().split('\n')[0] # Get just the first line of the message

        for line in lines:
            if line.startswith("parent "):
                parent = line.split(" ")[1]

        # Draw the node
        print(f" * \033[33m{current_hash[:7]}\033[0m {message}")
        
        if parent:
            print(" |")
            
        current_hash = parent
    print("\n")

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
    
    # branch
    branch_parser = subparsers.add_parser("branch", help="List or create branches")
    branch_parser.add_argument("name", nargs="?", help="Name of the new branch")
    
    # stats
    subparsers.add_parser("stats", help="Show repository statistics")
    
    # rewind
    rewind_parser = subparsers.add_parser("rewind", help="Undo the last N commits safely")
    rewind_parser.add_argument("steps", nargs="?", default=1, type=int, help="Number of commits to undo")
    
    # graph
    subparsers.add_parser("graph", help="Show visual commit graph")

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
    elif args.command == "branch":
        cmd_branch(args.name)
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "rewind":
        cmd_rewind(args.steps)
    elif args.command == "graph":
        cmd_graph()
    elif args.command is None:
        parser.print_help()

if __name__ == "__main__":
    main()