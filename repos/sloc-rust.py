import os

def count_rust_sloc(directory: str) -> int:
    total = 0

    for root, _, files in os.walk(directory):
        for name in files:
            if not name.endswith(".rs"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    block_depth = 0          # supports nested /* ... */
                    in_string = False        # " or '
                    string_quote = None
                    raw_hashes = 0           # number of # in r#" ... "#
                    escape = False

                    for line in f:
                        i = 0
                        code_buf = []
                        in_line_comment = False
                        line_len = len(line)

                        while i < line_len:
                            ch = line[i]
                            nxt = line[i+1] if i + 1 < line_len else ''

                            # Inside a block comment: handle nesting
                            if block_depth > 0:
                                if ch == '/' and nxt == '*':
                                    block_depth += 1
                                    i += 2
                                    continue
                                if ch == '*' and nxt == '/':
                                    block_depth -= 1
                                    i += 2
                                    continue
                                i += 1
                                continue

                            # Not in block comment

                            # Start of line comment (also covers /// and //! doc comments)
                            if ch == '/' and nxt == '/':
                                in_line_comment = True
                                break

                            # Start of block comment (/** and /*! doc comments too)
                            if ch == '/' and nxt == '*':
                                block_depth += 1
                                i += 2
                                continue

                            # Strings -------------------------------------------------
                            if in_string:
                                code_buf.append(ch)

                                if string_quote == '"':
                                    if raw_hashes == 0:
                                        # normal string with escapes
                                        if not escape and ch == '\\':
                                            escape = True
                                        elif not escape and ch == '"':
                                            in_string = False
                                        else:
                                            escape = False
                                    else:
                                        # raw string: end when we see " followed by raw_hashes #'s
                                        if ch == '"':
                                            # look ahead for hashes
                                            j = i + 1
                                            k = 0
                                            while k < raw_hashes and j < line_len and line[j] == '#':
                                                j += 1
                                                k += 1
                                            if k == raw_hashes:
                                                in_string = False
                                                i = j - 1  # consume the hashes
                                else:  # string_quote == "'"
                                    # treat as char literal; simple escape handling
                                    if not escape and ch == '\\':
                                        escape = True
                                    elif not escape and ch == "'":
                                        in_string = False
                                    else:
                                        escape = False

                                i += 1
                                continue

                            # Not in string/comment: maybe starting a string
                            if ch == 'r' and (nxt == '"' or nxt == '#'):
                                # raw string start: r"..." or r#"... "#
                                j = i + 1
                                hashes = 0
                                if j < line_len and line[j] == '#':
                                    while j < line_len and line[j] == '#':
                                        hashes += 1
                                        j += 1
                                if j < line_len and line[j] == '"':
                                    in_string = True
                                    string_quote = '"'
                                    raw_hashes = hashes
                                    escape = False
                                    # append the starting delimiter as code
                                    code_buf.append('r')
                                    code_buf.extend('#' * hashes)
                                    code_buf.append('"')
                                    i = j + 1
                                    continue

                            if ch == '"' or ch == "'":
                                in_string = True
                                string_quote = ch
                                raw_hashes = 0
                                escape = False
                                code_buf.append(ch)
                                i += 1
                                continue

                            # Regular code char
                            code_buf.append(ch)
                            i += 1

                        # end while over line

                        # If we hit a line comment, we ignore the remainder of the line.
                        # Count line if, after stripping whitespace, anything remains.
                        if ''.join(code_buf).strip():
                            total += 1

                    # If file ended while inside a block comment, we just stop (no crash)
            except OSError:
                # unreadable file; skip it
                continue

    return total


if __name__ == "__main__":
    repo_path = input("Enter path to Rust repository: ").strip()
    print(f"\nTotal Rust SLOC in '{repo_path}': {count_rust_sloc(repo_path)}")
