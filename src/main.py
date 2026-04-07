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

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.path)
    elif args.command == "hash-object":
        with open(args.file, "rb") as f:
            print(hash_object(f.read(), write=args.w))
    elif args.command == "add":
        cmd_add(args.paths)
    elif args.command == "commit":
        cmd_commit(args.message) # Calls your function with the -m text
    elif args.command is None:
        parser.print_help()

if __name__ == "__main__":
    main()