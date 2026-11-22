"""Event store: every reading and every check, with a timestamp, in SQLite."""

import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.last_commit = 0.0
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS readings (t REAL, seq INTEGER, raw TEXT, volts TEXT);
            CREATE TABLE IF NOT EXISTS checks (t REAL, step INTEGER, result TEXT, message TEXT, detail TEXT);
        """)
        # five readings a second would grow the file without limit
        self.db.execute("DELETE FROM readings WHERE t < ?", (time.time() - 7 * 86400,))
        self.db.commit()

    def add_reading(self, seq, raw, volts):
        self.db.execute("INSERT INTO readings VALUES (?, ?, ?, ?)",
                        (time.time(), seq, json.dumps(raw), json.dumps(volts)))
        if time.time() - self.last_commit > 1.0:   # five commits a second would stall the MQTT thread
            self.db.commit()
            self.last_commit = time.time()

    def add_check(self, result):
        self.db.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?)",
                        (time.time(), result["step"], result["result"], result["message"], json.dumps(result)))
        self.db.commit()
