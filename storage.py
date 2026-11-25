import json
import sqlite3
from contextlib import closing
from pathlib import Path


CREATE_TABLE = '''
CREATE TABLE IF NOT EXISTS career_analysis (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    target TEXT NOT NULL,
    mode TEXT NOT NULL,
    jd_count INTEGER NOT NULL CHECK (jd_count BETWEEN 1 AND 10),
    match_percentage REAL NOT NULL CHECK (match_percentage BETWEEN 0 AND 100),
    model_attempts INTEGER NOT NULL CHECK (model_attempts BETWEEN 1 AND 3),
    report_json TEXT NOT NULL
)
'''


def save_analysis(database: Path, report: dict) -> int:
    """Append a validated analysis and return its SQLite record ID."""
    database.parent.mkdir(parents=True, exist_ok=True)
    values = (
        report['generated_at'], report['target'], report['mode'], report['jd_count'],
        report['match_percentage'], report['model_attempts'],
        json.dumps(report, ensure_ascii=False, allow_nan=False),
    )
    try:
        with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
            connection.execute(CREATE_TABLE)
            cursor = connection.execute(
                '''INSERT INTO career_analysis (
                    created_at, target, mode, jd_count, match_percentage, model_attempts, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                values,
            )
            return cursor.lastrowid
    except sqlite3.Error as error:
        raise RuntimeError(f'无法保存 SQLite 数据库 {database}：{error}') from error
