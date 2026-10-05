"""Local DuckDB catalog and versioned Parquet snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from quantlab.data.schema import DAILY_BAR_COLUMNS


class MarketDataStore:
    def __init__(self, root: Path | str = "data") -> None:
        self.root = Path(root)
        self.raw_ashare = self.root / "raw" / "ashare"
        self.raw_yahoo = self.root / "raw" / "yahoo"
        self.validated = self.root / "validated" / "ashare"
        self.processed = self.root / "processed"
        self.database_dir = self.root / "database"
        self.database_dir.mkdir(parents=True, exist_ok=True)
        for folder in (self.raw_ashare, self.raw_yahoo, self.validated, self.processed):
            folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.database_dir / "quantlab.duckdb"
        self._init_db()

    def _connect(self):
        connection = duckdb.connect(str(self.db_path))
        connection.execute("""
            CREATE TABLE IF NOT EXISTS market_bars (
              market VARCHAR, exchange VARCHAR, symbol VARCHAR, trade_date DATE,
              open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE,
              adjustment VARCHAR, currency VARCHAR, source VARCHAR, downloaded_at TIMESTAMPTZ,
              provider_version VARCHAR, source_interface VARCHAR, ingest_id VARCHAR,
              revision_id VARCHAR, record_version INTEGER, first_seen_at TIMESTAMPTZ,
              recorded_at TIMESTAMPTZ, row_hash VARCHAR,
              PRIMARY KEY (market, exchange, symbol, trade_date, source, adjustment)
            )
        """)
        connection.execute("CREATE TABLE IF NOT EXISTS ingest_runs (ingest_id VARCHAR PRIMARY KEY, source VARCHAR, market VARCHAR, symbol VARCHAR, adjustment VARCHAR, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, status VARCHAR, requested_start DATE, requested_end DATE, fetched_rows INTEGER, accepted_rows INTEGER, rejected_rows INTEGER, updated_rows INTEGER, error VARCHAR)")
        connection.execute("CREATE TABLE IF NOT EXISTS validation_issues (ingest_id VARCHAR, symbol VARCHAR, row_index INTEGER, issue_type VARCHAR, detail VARCHAR)")
        connection.execute("CREATE TABLE IF NOT EXISTS parquet_manifest (partition_key VARCHAR PRIMARY KEY, file_path VARCHAR, row_count BIGINT, updated_at TIMESTAMPTZ)")
        connection.execute("CREATE TABLE IF NOT EXISTS bar_revisions (revision_id VARCHAR, market VARCHAR, exchange VARCHAR, symbol VARCHAR, trade_date DATE, source VARCHAR, adjustment VARCHAR, record_version INTEGER, row_json VARCHAR, recorded_at TIMESTAMPTZ)")
        connection.execute("CREATE TABLE IF NOT EXISTS provider_bar_metadata (provider VARCHAR, symbol VARCHAR, trade_date DATE, provider_version VARCHAR, source_interface VARCHAR, metadata_json VARCHAR, downloaded_at TIMESTAMPTZ, ingest_id VARCHAR, PRIMARY KEY (provider, symbol, trade_date))")
        connection.execute("CREATE TABLE IF NOT EXISTS adjustment_factors (market VARCHAR, exchange VARCHAR, symbol VARCHAR, trade_date DATE, provider VARCHAR, adjustment_factor DOUBLE, downloaded_at TIMESTAMPTZ, first_seen_at TIMESTAMPTZ, recorded_at TIMESTAMPTZ, ingest_id VARCHAR, provider_version VARCHAR, record_version INTEGER, revision_id VARCHAR, PRIMARY KEY (market, exchange, symbol, trade_date, provider))")
        connection.execute("CREATE TABLE IF NOT EXISTS adjustment_factor_revisions (revision_id VARCHAR, market VARCHAR, exchange VARCHAR, symbol VARCHAR, trade_date DATE, provider VARCHAR, record_version INTEGER, row_json VARCHAR, recorded_at TIMESTAMPTZ)")
        connection.execute("CREATE TABLE IF NOT EXISTS backfill_jobs (job_id VARCHAR PRIMARY KEY, job_key VARCHAR UNIQUE, universe_path VARCHAR, requested_start DATE, requested_end DATE, adjustment VARCHAR, provider VARCHAR, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)")
        connection.execute("CREATE TABLE IF NOT EXISTS backfill_items (job_id VARCHAR, symbol VARCHAR, status VARCHAR, provider VARCHAR, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, row_count INTEGER, error VARCHAR, PRIMARY KEY (job_id, symbol))")
        return connection

    def _init_db(self) -> None:
        with self._connect() as con:
            pass

    @staticmethod
    def _hash_row(row: pd.Series) -> str:
        fields = ["market", "exchange", "symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "adjustment", "currency", "source"]
        payload = {key: (None if pd.isna(row[key]) else str(row[key])) for key in fields}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def save_raw(self, raw: pd.DataFrame, *, market: str, symbol: str, adjustment: str, ingest_id: str, source: str, provider_version: str, source_interface: str, downloaded_at: pd.Timestamp) -> Path:
        folder = self.raw_ashare if market == "CN_A" else self.raw_yahoo
        safe_symbol = symbol.replace(".", "_")
        target = folder / f"source={source}" / f"symbol={safe_symbol}" / f"adjustment={adjustment}" / f"ingest_id={ingest_id}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        archived = raw.copy()
        archived["_ingest_id"] = ingest_id
        archived["_symbol"] = symbol
        archived["_adjustment"] = adjustment
        archived["_source"] = source
        archived["_provider_version"] = provider_version
        archived["_source_interface"] = source_interface
        archived["_downloaded_at"] = downloaded_at
        temp = target.with_suffix(".tmp.parquet")
        archived.to_parquet(temp, index=False)
        os.replace(temp, target)
        return target

    def start_ingest(self, ingest_id: str, source: str, market: str, symbol: str, adjustment: str, start: date | None, end: date | None) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO ingest_runs VALUES (?, ?, ?, ?, ?, current_timestamp, NULL, 'running', ?, ?, 0, 0, 0, 0, NULL)", [ingest_id, source, market, symbol, adjustment, start, end])

    def finish_ingest(self, ingest_id: str, *, status: str, fetched: int = 0, accepted: int = 0, rejected: int = 0, updated: int = 0, error: str | None = None) -> None:
        with self._connect() as con:
            con.execute("UPDATE ingest_runs SET completed_at=current_timestamp, status=?, fetched_rows=?, accepted_rows=?, rejected_rows=?, updated_rows=?, error=? WHERE ingest_id=?", [status, fetched, accepted, rejected, updated, error, ingest_id])

    def record_issues(self, ingest_id: str, symbol: str, issues: pd.DataFrame) -> None:
        if issues.empty:
            return
        frame = issues.copy()
        frame["ingest_id"] = ingest_id
        frame["symbol"] = symbol
        with self._connect() as con:
            con.register("issue_rows", frame)
            con.execute("INSERT INTO validation_issues SELECT ingest_id, symbol, row_index, issue_type, detail FROM issue_rows")

    def latest_date(
        self, symbol: str, source: str = "akshare", adjustment: str = "qfq", *,
        start: date | None = None, end: date | None = None,
    ) -> date | None:
        clauses = ["symbol=?", "source=?", "adjustment=?"]
        params: list[object] = [symbol, source, adjustment]
        if start is not None:
            clauses.append("trade_date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("trade_date <= ?")
            params.append(end)
        with self._connect() as con:
            row = con.execute(
                "SELECT max(trade_date) FROM market_bars WHERE " + " AND ".join(clauses), params
            ).fetchone()
        return row[0]

    def merge_validated(self, bars: pd.DataFrame, provider_metadata: pd.DataFrame | None = None) -> int:
        """Idempotent upsert; write new Parquet snapshots before switching the catalog."""
        if bars.empty:
            return 0
        frame = bars.loc[:, DAILY_BAR_COLUMNS].copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        frame["row_hash"] = frame.apply(self._hash_row, axis=1)
        con = self._connect()
        try:
            con.execute("BEGIN TRANSACTION")
            con.register("incoming", frame)
            existing = con.execute("""
              SELECT i.*, b.row_hash old_hash, b.record_version old_version, b.first_seen_at old_first_seen
              FROM incoming i JOIN market_bars b
              ON b.market=i.market AND b.exchange=i.exchange AND b.symbol=i.symbol AND b.trade_date=i.trade_date AND b.source=i.source AND b.adjustment=i.adjustment
            """).df()
            changed = existing[existing["old_hash"] != existing["row_hash"]]
            known = {(symbol, pd.Timestamp(day).date()) for symbol, day in zip(existing.symbol, existing.trade_date)}
            new_rows = frame.loc[[(row.symbol, row.trade_date) not in known for row in frame.itertuples()]].copy()
            revision_ids: dict[tuple[str, object], str] = {}
            if not changed.empty:
                for record in changed.to_dict("records"):
                    prior = con.execute("SELECT * FROM market_bars WHERE market=? AND exchange=? AND symbol=? AND trade_date=? AND source=? AND adjustment=?", [record["market"], record["exchange"], record["symbol"], record["trade_date"], record["source"], record["adjustment"]]).df()
                    rid = str(uuid.uuid4())
                    revision_ids[(record["symbol"], record["trade_date"])] = rid
                    con.execute("INSERT INTO bar_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)", [rid, record["market"], record["exchange"], record["symbol"], record["trade_date"], record["source"], record["adjustment"], int(record["old_version"]), prior.to_json(orient="records", date_format="iso")])
            update_frame = pd.concat([new_rows, changed.loc[:, frame.columns]], ignore_index=True)
            def persist_provider_metadata() -> None:
                if provider_metadata is None or provider_metadata.empty:
                    return
                metadata = provider_metadata.loc[:, [
                    "provider", "symbol", "trade_date", "provider_version", "source_interface",
                    "metadata_json", "downloaded_at", "ingest_id",
                ]].copy()
                metadata["trade_date"] = pd.to_datetime(metadata["trade_date"]).dt.date
                con.register("provider_meta_rows", metadata)
                con.execute("""
                    INSERT INTO provider_bar_metadata
                    SELECT provider, symbol, trade_date, provider_version, source_interface,
                           metadata_json, downloaded_at, ingest_id
                    FROM provider_meta_rows
                    ON CONFLICT (provider, symbol, trade_date) DO UPDATE SET
                        provider_version=excluded.provider_version,
                        source_interface=excluded.source_interface,
                        metadata_json=excluded.metadata_json,
                        downloaded_at=excluded.downloaded_at,
                        ingest_id=excluded.ingest_id
                """)
            if update_frame.empty:
                persist_provider_metadata()
                con.execute("COMMIT")
                return 0
            update_frame["record_version"] = 1
            update_frame["revision_id"] = None
            update_frame["first_seen_at"] = update_frame["downloaded_at"]
            if not changed.empty:
                changed_idx = {(r.symbol, r.trade_date): r for r in changed.itertuples()}
                for ix, row in update_frame.iterrows():
                    old = changed_idx.get((row.symbol, row.trade_date))
                    if old is not None:
                        update_frame.at[ix, "record_version"] = int(old.old_version) + 1
                        update_frame.at[ix, "first_seen_at"] = old.old_first_seen
                        update_frame.at[ix, "revision_id"] = revision_ids[(row.symbol, row.trade_date)]
            # Build full replacement snapshots per coarse partition; filenames are immutable.
            update_frame["_year"] = pd.to_datetime(update_frame["trade_date"]).dt.year
            partitions = update_frame.groupby(["exchange", "adjustment", "_year"], dropna=False)
            for (exchange, adjustment, year), part in partitions:
                key = f"{exchange}/{adjustment}/{year}"
                old_file_row = con.execute("SELECT file_path FROM parquet_manifest WHERE partition_key=?", [key]).fetchone()
                if old_file_row:
                    old_path = Path(old_file_row[0])
                    snapshot = pd.read_parquet(old_path) if old_path.exists() else pd.DataFrame(columns=frame.columns)
                else:
                    snapshot = pd.DataFrame(columns=frame.columns)
                snapshot = pd.concat([snapshot, part.drop(columns="_year")], ignore_index=True)
                if not snapshot.empty:
                    snapshot["trade_date"] = pd.to_datetime(snapshot["trade_date"])
                snapshot = snapshot.drop_duplicates(["market", "exchange", "symbol", "trade_date", "source", "adjustment"], keep="last").sort_values(["symbol", "trade_date"])
                target_dir = self.validated / f"exchange={exchange}" / f"adjustment={adjustment}" / f"year={year}"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"snapshot-{uuid.uuid4().hex}.parquet"
                temp = target.with_suffix(".tmp.parquet")
                snapshot.to_parquet(temp, index=False)
                os.replace(temp, target)
                con.execute("INSERT INTO parquet_manifest VALUES (?, ?, ?, current_timestamp) ON CONFLICT (partition_key) DO UPDATE SET file_path=excluded.file_path, row_count=excluded.row_count, updated_at=excluded.updated_at", [key, str(target.resolve()), len(snapshot)])
            con.register("upserts", update_frame.drop(columns="_year", errors="ignore"))
            con.execute("INSERT INTO market_bars SELECT * FROM upserts ON CONFLICT (market, exchange, symbol, trade_date, source, adjustment) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, volume=excluded.volume, amount=excluded.amount, downloaded_at=excluded.downloaded_at, provider_version=excluded.provider_version, source_interface=excluded.source_interface, ingest_id=excluded.ingest_id, revision_id=excluded.revision_id, record_version=excluded.record_version, first_seen_at=excluded.first_seen_at, recorded_at=excluded.recorded_at, row_hash=excluded.row_hash")
            persist_provider_metadata()
            con.execute("COMMIT")
            return len(update_frame)
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            # New immutable files become harmless orphans if the catalog transaction fails.
            raise
        finally:
            con.close()

    def query(self, *, symbol: str | None = None, market: str | None = None, start: date | None = None, end: date | None = None, adjustment: str | None = None, source: str | None = None) -> pd.DataFrame:
        clauses, params = [], []
        for col, val in (("symbol", symbol), ("market", market), ("trade_date >=", start), ("trade_date <=", end), ("adjustment", adjustment), ("source", source)):
            if val is not None:
                if col.startswith("trade_date"):
                    clauses.append(f"{col} ?")
                else:
                    clauses.append(f"{col}=?")
                params.append(val)
        sql = "SELECT * FROM market_bars" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY symbol, trade_date"
        with self._connect() as con:
            return con.execute(sql, params).df()

    def query_provider_metadata(
        self, *, provider: str, symbol: str, start: date | None = None, end: date | None = None,
    ) -> pd.DataFrame:
        clauses = ["provider=?", "symbol=?"]
        params: list[object] = [provider, symbol]
        for column, value in (("trade_date >=", start), ("trade_date <=", end)):
            if value is not None:
                clauses.append(f"{column} ?")
                params.append(value)
        sql = "SELECT * FROM provider_bar_metadata WHERE " + " AND ".join(clauses) + " ORDER BY trade_date"
        with self._connect() as con:
            return con.execute(sql, params).df()

    def status(
        self, *, start: date | None = None, end: date | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        source: str | None = None, adjustment: str | None = None,
    ) -> pd.DataFrame:
        if symbols is not None and not symbols:
            return pd.DataFrame(columns=["market", "exchange", "symbol", "source", "adjustment",
                                         "start_date", "latest_date", "row_count"])
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (("trade_date >=", start), ("trade_date <=", end),
                              ("source", source), ("adjustment", adjustment)):
            if value is not None:
                if column.startswith("trade_date"):
                    clauses.append(f"{column} ?")
                else:
                    clauses.append(f"{column}=?")
                params.append(value)
        if symbols is not None:
            clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
            params.extend(symbols)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as con:
            return con.execute(
                "SELECT market, exchange, symbol, source, adjustment, min(trade_date) start_date, "
                "max(trade_date) latest_date, count(*) AS row_count FROM market_bars" + where +
                " GROUP BY ALL ORDER BY market, symbol, source, adjustment", params
            ).df()

    def validation_issues(self, symbol: str, ingest_id: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM validation_issues WHERE symbol=?"
        args: list[object] = [symbol]
        if ingest_id:
            sql += " AND ingest_id=?"
            args.append(ingest_id)
        with self._connect() as con:
            return con.execute(sql, args).df()

    def merge_adjustment_factors(self, factors: pd.DataFrame) -> int:
        """Upsert a separately versioned adjustment-factor series, never OHLC rows."""
        required = {
            "market", "exchange", "symbol", "trade_date", "provider", "adjustment_factor",
            "downloaded_at", "first_seen_at", "recorded_at", "ingest_id", "provider_version",
        }
        missing = required.difference(factors.columns)
        if missing:
            raise ValueError(f"Adjustment factors missing fields: {', '.join(sorted(missing))}.")
        if factors.empty:
            return 0
        frame = factors.loc[:, sorted(required)].copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        frame["adjustment_factor"] = pd.to_numeric(frame["adjustment_factor"], errors="coerce")
        if (frame["adjustment_factor"].isna().any()
                or not all(math.isfinite(value) for value in frame["adjustment_factor"].to_numpy(dtype=float))
                or (frame["adjustment_factor"] <= 0).any()):
            raise ValueError("Adjustment factors must be finite and positive.")
        if frame.duplicated(["market", "exchange", "symbol", "trade_date", "provider"]).any():
            raise ValueError("Duplicate adjustment factor keys are not allowed.")
        con = self._connect()
        try:
            con.execute("BEGIN TRANSACTION")
            con.register("incoming_factors", frame)
            existing = con.execute("""
                SELECT i.*, f.adjustment_factor old_factor, f.record_version old_version,
                       f.first_seen_at old_first_seen
                FROM incoming_factors i JOIN adjustment_factors f
                USING (market, exchange, symbol, trade_date, provider)
            """).df()
            changed = existing.loc[existing["adjustment_factor"] != existing["old_factor"]]
            revision_by_key: dict[tuple[str, object, str], tuple[str, int, object]] = {}
            for row in changed.to_dict("records"):
                key = (row["symbol"], pd.Timestamp(row["trade_date"]).date(), row["provider"])
                revision_id = str(uuid.uuid4())
                revision_by_key[key] = (revision_id, int(row["old_version"]) + 1, row["old_first_seen"])
                con.execute("INSERT INTO adjustment_factor_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)", [
                    revision_id, row["market"], row["exchange"], row["symbol"], row["trade_date"],
                    row["provider"], int(row["old_version"]), json.dumps({
                        "adjustment_factor": float(row["old_factor"]),
                        "record_version": int(row["old_version"]),
                    }),
                ])
            changed_keys = set(revision_by_key)
            known_keys = {(row.symbol, pd.Timestamp(row.trade_date).date(), row.provider) for row in existing.itertuples()}
            rows: list[dict[str, object]] = []
            for row in frame.to_dict("records"):
                key = (row["symbol"], row["trade_date"], row["provider"])
                if key in known_keys and key not in changed_keys:
                    continue
                row["record_version"] = 1
                row["revision_id"] = None
                if key in revision_by_key:
                    rid, version_number, first_seen = revision_by_key[key]
                    row["record_version"], row["revision_id"], row["first_seen_at"] = version_number, rid, first_seen
                rows.append(row)
            if rows:
                updates = pd.DataFrame(rows, columns=[*sorted(required), "record_version", "revision_id"])
                con.register("factor_upserts", updates)
                con.execute("""
                    INSERT INTO adjustment_factors
                    SELECT market, exchange, symbol, trade_date, provider, adjustment_factor,
                           downloaded_at, first_seen_at, recorded_at, ingest_id, provider_version,
                           record_version, revision_id
                    FROM factor_upserts
                    ON CONFLICT (market, exchange, symbol, trade_date, provider) DO UPDATE SET
                        adjustment_factor=excluded.adjustment_factor,
                        downloaded_at=excluded.downloaded_at,
                        recorded_at=excluded.recorded_at,
                        ingest_id=excluded.ingest_id,
                        provider_version=excluded.provider_version,
                        record_version=excluded.record_version,
                        revision_id=excluded.revision_id
                """)
            con.execute("COMMIT")
            return len(rows)
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            con.close()

    def query_adjustment_factors(
        self, *, symbol: str, start: date | None = None, end: date | None = None,
        provider: str | None = None,
    ) -> pd.DataFrame:
        clauses, params = ["symbol=?"], [symbol]
        for col, val in (("trade_date >=", start), ("trade_date <=", end), ("provider", provider)):
            if val is not None:
                clauses.append(f"{col} ?" if col.startswith("trade_date") else f"{col}=?")
                params.append(val)
        with self._connect() as con:
            return con.execute("SELECT * FROM adjustment_factors WHERE " + " AND ".join(clauses) +
                               " ORDER BY trade_date, provider", params).df()

    def get_or_create_backfill_job(
        self, *, job_key: str, universe_path: str, start: date, end: date,
        adjustment: str, provider: str, symbols: list[str],
    ) -> str:
        with self._connect() as con:
            found = con.execute("SELECT job_id FROM backfill_jobs WHERE job_key=?", [job_key]).fetchone()
            if found:
                job_id = found[0]
            else:
                job_id = str(uuid.uuid4())
                con.execute("INSERT INTO backfill_jobs VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp)",
                            [job_id, job_key, universe_path, start, end, adjustment, provider])
            for symbol in symbols:
                con.execute("INSERT INTO backfill_items VALUES (?, ?, 'pending', ?, NULL, NULL, 0, NULL) ON CONFLICT (job_id, symbol) DO NOTHING",
                            [job_id, symbol, provider])
            con.execute("UPDATE backfill_jobs SET updated_at=current_timestamp WHERE job_id=?", [job_id])
        return job_id

    def backfill_items(self, job_id: str) -> pd.DataFrame:
        with self._connect() as con:
            return con.execute("SELECT * FROM backfill_items WHERE job_id=? ORDER BY symbol", [job_id]).df()

    def set_backfill_item(
        self, job_id: str, symbol: str, *, status: str, provider: str,
        row_count: int = 0, error: str | None = None, started: bool = False,
    ) -> None:
        if status not in {"pending", "running", "completed", "failed"}:
            raise ValueError("Invalid backfill item status.")
        with self._connect() as con:
            con.execute("""
                UPDATE backfill_items SET status=?, provider=?,
                    started_at=CASE WHEN ? THEN current_timestamp ELSE started_at END,
                    completed_at=CASE WHEN ? IN ('completed','failed') THEN current_timestamp ELSE NULL END,
                    row_count=?, error=?
                WHERE job_id=? AND symbol=?
            """, [status, provider, started, status, row_count, error, job_id, symbol])
            con.execute("UPDATE backfill_jobs SET updated_at=current_timestamp WHERE job_id=?", [job_id])

    def backfill_jobs_status(
        self, *, universe_path: str | None = None, start: date | None = None, end: date | None = None,
        adjustment: str | None = None, provider: str | None = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (("j.universe_path", universe_path), ("j.requested_start", start),
                              ("j.requested_end", end), ("j.adjustment", adjustment), ("j.provider", provider)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as con:
            return con.execute("""
                SELECT j.job_id, j.universe_path, j.requested_start, j.requested_end,
                       j.adjustment, j.provider, j.created_at, j.updated_at,
                       count(i.symbol) item_count,
                       count(*) FILTER (WHERE i.status='completed') completed,
                       count(*) FILTER (WHERE i.status='failed') failed,
                       count(*) FILTER (WHERE i.status IN ('pending','running')) pending
                FROM backfill_jobs j LEFT JOIN backfill_items i USING (job_id)
            """ + where + """
                GROUP BY ALL ORDER BY j.created_at DESC
            """, params).df()

