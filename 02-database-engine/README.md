# 2. Write a Database from Scratch

Inspired by *Code With Sep* (relational engine series). Modeled after SQLite / PostgreSQL internals — not a SQL wrapper around someone else's storage.

## Architecture

```
SQL text
  -> lexer / recursive-descent parser
  -> query plan (Create/Insert/Select/Delete)
  -> executor (projection, selection, scans)
        |
        v
   B+ tree (rowid primary key)
        |
        v
   slotted 4 KB pages  <-->  LRU buffer pool  <-->  pager + freemap + fsync
```

| Layer | Implementation |
| --- | --- |
| Slotted-page manager | 4 KB pages, 16-byte header, slot array, cells packed from the tail |
| Free-space bitmap | Page 1 tracks allocated pages |
| Buffer pool | LRU (`OrderedDict`), dirty tracking, write-on-evict |
| B+ tree | Internal routers + linked leaves, split, merge of empty leaves, range scan |
| Query engine | Lexer, recursive descent, relational selection/projection |
| Commit | Flush dirty pages + superblock + `fsync` |

## SQL subset

```sql
CREATE TABLE users (id INT, name TEXT, score INT);
INSERT INTO users VALUES (1, 'ada', 10);
SELECT name, score FROM users WHERE score > 5 ORDER BY name LIMIT 10;
DELETE FROM users WHERE id = 1;
DROP TABLE users;
```

## Run

```bash
python3 -m minidb.cli my.db
python3 tests/test_minidb.py
```

## What you learn

Indexes are a disk layout, not a magic keyword: a B+ tree turns equality into a handful of page reads and range queries into a leaf walk. Paging plus a buffer pool keeps the working set in RAM while the file can grow far beyond memory. A commit is not "SQL finished" — it is dirty pages hitting disk and `fsync` returning.
