# Agent Guidelines & Operational Rules

All agents and contributors working on this codebase must strictly adhere to the following rules:

1. **Read Architecture First**:
   Read `ARCHITECTURE.md` before starting any task to understand data flow, single sources of truth, forbidden patterns, and operational standards.

2. **Scope of File Edits**:
   Edit only the files named in the task, plus their corresponding test files. Never rewrite an entire file to fix a small issue; use surgical, targeted edits. Preserve all existing comments and docstrings unrelated to your changes.

3. **No Duplicate Code Paths**:
   If you find two code paths performing the same job (e.g. duplicate simulation loops, exit evaluations, or fee models), **STOP immediately and inform the user** instead of patching one of them or introducing a new duplicate.

4. **Fail Loudly on Missing Data**:
   Never add a fallback value for missing data (e.g. `else 0.0`, `or 1.0`, `except: pass`). Raise a clear, descriptive error instead.

5. **Never Tamper with Validation Standards**:
   Never change thresholds, hurdles, strategy parameters, exit configurations, or statistical verdict rules to make a failing hypothesis pass.

6. **Strict Verification Before Declaring Done**:
   Before declaring any task complete:
   - Run `python -m pytest -q` and verify that all unit tests pass with zero failures.
   - Run the relevant pipeline command end-to-end.
   - Paste the real, unedited command output.
   - Explicitly list any assumptions or items you could not verify.

7. **Two-Failure Rule**:
   If a test or command fails twice, stop immediately and ask the user before trying again.

8. **Linux VM & Long-Running Commands**:
   This project runs on a Linux VM. Use bash and python3. Run any command expected to take more than 2 minutes with nohup or inside tmux, write output to logs/<name>.log, and tell me the command to check its progress.
