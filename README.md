# Git-Internal 

A high-performance, content-addressable version control system built from scratch in Python. This project serves as a deep dive into the engineering principles of Git, implementing its core object model, binary index serialization, and graph-based versioning logic.

## Overview

`git-internal` is a functional VCS subset that is **byte-for-byte compatible** with the official Git binary format. It manages the full lifecycle of a local repository: from initializing the object store and staging files in a complex binary index to committing snapshots and traversing the commit graph (DAG).

## Key Features

* **Content-Addressable Storage:** Implements the Git object store using $SHA-1$ hashing and $zlib$ compression.
* **Binary Index Parsing:** Manages the staging area by parsing the `DIRC` (Directory Cache) binary format using Python's `struct` module.
* **Recursive Merkle Trees:** Snapshots directory states as a Merkle Tree for efficient data integrity verification.
* **Graph-Based History:** Reconstructs historical project states through parent-pointer traversal in the commit Directed Acyclic Graph (DAG).
* **Metadata-Driven Optimization:** Implements $O(1)$ change detection by comparing filesystem `stat` metadata against index entries, bypassing heavy hashing for unchanged files.

## Architecture & Internals

### 1. The Object Model
Every file, directory, and commit is stored as a compressed, hashed object in `.git/objects/`.
* **Blobs:** Store file content with a `type size\x00` header.
* **Trees:** Represent directory listings, mapping filenames and modes to their respective hashes.
* **Commits:** Store tree pointers, parent hashes (linking history), author metadata, and commit messages.

### 2. The Binary Index
The index (`.git/index`) is a sophisticated binary file acting as the "Staging Area." This project implements the **Version 2 Index format**, requiring precise bit-packing of file metadata (mtime, ctime, dev, ino, uid, gid) to maintain compatibility with the official C-based Git implementation.

### 3. Graph Operations
* **Log:** Performs a reverse-walk of the linked-list of commits starting from `HEAD`.
* **Checkout:** Traverses the Merkle Tree from a specific commit hash to recursively reconstruct the working directory on disk.

## Usage

```bash
# Initialize a new repository
python3 main.py init

# Stage files (updates the binary index)
python3 main.py add <file_path>

# Create a snapshot (generates Trees and Commits)
python3 main.py commit -m "Your commit message"

# View the commit graph
python3 main.py log

# Check for changes (Metadata vs Hash comparison)
python3 main.py status

# Time travel (restore files from a hash)
python3 main.py checkout <commit-sha>

```

## Tech Stack

- **Language:** Python 3.10+
- **Core Modules:**
  - `struct`: For C-style binary data packing/unpacking.
  - `zlib`: For object compression (standard Git format).
  - `hashlib`: For $SHA-1$ content addressing.
  - `argparse`: For building the CLI interface.
  - `os` / `sys`: For low-level filesystem manipulation.
