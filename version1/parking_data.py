import math
import sqlite3
from contextlib import closing
from pathlib import Path


PARKING_COLUMNS = "id,label,occupied,hourly_rate,map_x,map_y"


def normalize_spaces(rows):
    if not isinstance(rows, list):
        raise ValueError("Invalid parking data")
    spaces = []
    ids, labels, positions = set(), set(), set()
    for row in rows:
        identifier, x, y = row["id"], row["map_x"], row["map_y"]
        label, occupied = row["label"], row["occupied"]
        rate = row["hourly_rate"]
        if any(type(value) is not int for value in (identifier, x, y)):
            raise ValueError("Invalid parking coordinates")
        if identifier < 1 or not 1 <= x <= 6 or not 1 <= y <= 99:
            raise ValueError("Invalid parking coordinates")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 10:
            raise ValueError("Invalid parking label")
        label = label.strip()
        if type(occupied) is not bool or isinstance(rate, bool):
            raise ValueError("Invalid parking state")
        rate = float(rate)
        if not math.isfinite(rate) or not 0 <= rate <= 999999.99:
            raise ValueError("Invalid parking rate")
        if identifier in ids or label in labels or (x, y) in positions:
            raise ValueError("Duplicate parking space")
        ids.add(identifier)
        labels.add(label)
        positions.add((x, y))
        spaces.append({"id": identifier, "label": label, "occupied": occupied,
                       "hourly_rate": rate, "map_x": x, "map_y": y})
    return sorted(spaces, key=lambda space: (space["map_y"], space["map_x"]))


def preview_spaces(database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            exists = connection.execute(
                "select 1 from sqlite_master where type = 'table' and name = 'parking_spaces'"
            ).fetchone()
            connection.execute(
                "create table if not exists parking_spaces ("
                "id integer primary key check (id > 0), "
                "label text not null unique check (length(label) between 1 and 10), "
                "occupied integer not null check (occupied in (0, 1)), "
                "hourly_rate real not null check (hourly_rate between 0 and 999999.99), "
                "map_x integer not null check (map_x between 1 and 6), "
                "map_y integer not null check (map_y between 1 and 99), "
                "unique (map_x, map_y))"
            )
            if not exists:
                rows = []
                for y, letter in enumerate("AB", 1):
                    for x in range(1, 7):
                        label = f"{letter}{x}"
                        rows.append(((y - 1) * 6 + x, label,
                                     int(label in {"A2", "A6", "B2", "B5"}), 5, x, y))
                connection.executemany(
                    "insert or ignore into parking_spaces "
                    "(id, label, occupied, hourly_rate, map_x, map_y) values (?, ?, ?, ?, ?, ?)",
                    rows,
                )
        rows = [dict(row) for row in connection.execute(
            "select " + PARKING_COLUMNS + " from parking_spaces order by map_y, map_x"
        ).fetchall()]
    for row in rows:
        row["occupied"] = bool(row["occupied"])
    return normalize_spaces(rows)
