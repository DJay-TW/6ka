import json
import sqlite3
import sys
from pathlib import Path


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def jsonable(value):
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return value


def row_to_dict(row):
    return {key: jsonable(row[key]) for key in row.keys()}


def main():
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else r"backups\finance-20260602.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = []
    table_rows = cur.execute(
        "select name, sql from sqlite_master where type='table' order by name"
    ).fetchall()
    for table_row in table_rows:
        name = table_row["name"]
        quoted = quote_identifier(name)
        count = cur.execute(f"select count(*) as c from {quoted}").fetchone()["c"]
        columns = [
            {
                "cid": col[0],
                "name": col[1],
                "type": col[2],
                "notnull": col[3],
                "default": col[4],
                "pk": col[5],
            }
            for col in cur.execute(f"pragma table_info({quoted})").fetchall()
        ]
        sample = []
        if count:
            sample = [
                row_to_dict(item)
                for item in cur.execute(f"select * from {quoted} limit 5").fetchall()
            ]
        tables.append(
            {
                "name": name,
                "count": count,
                "columns": columns,
                "sample": sample,
                "sql": table_row["sql"],
            }
        )

    print(json.dumps({"database": str(db_path), "tables": tables}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
