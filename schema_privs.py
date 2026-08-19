#!/usr/bin/env python3
"""
Report schema-level privileges for all roles, pivoted so rows are schemas
and columns are grantees.
"""

import argparse
import configparser
import csv
import io
import sys

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

# ---------------------------------------------------------------------------
# Embedded SQL
# ---------------------------------------------------------------------------

SQL_CREATE_FUNCTION = """\
CREATE OR REPLACE FUNCTION schema_privileges_pivot()
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    cols text;
BEGIN
    SELECT string_agg(
        'MAX(CASE WHEN grantee = ' || quote_literal(rolname) ||
        ' THEN privileges END) AS ' || quote_ident(rolname),
        ', '
        ORDER BY rolname
    )
    INTO cols
    FROM (
        SELECT DISTINCT r.rolname
        FROM pg_namespace n
        JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a ON true
        JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
    ) r;

    EXECUTE 'DROP TABLE IF EXISTS schema_privilege_pivot';

    EXECUTE '
        CREATE TEMP TABLE schema_privilege_pivot AS
        SELECT schema_name, ' || cols || '
        FROM (
            SELECT
                n.nspname AS schema_name,
                r.rolname AS grantee,
                string_agg(a.privilege_type, '', '' ORDER BY a.privilege_type) AS privileges
            FROM pg_namespace n
            JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault(''n'', n.nspowner))) a ON true
            JOIN pg_roles r ON r.oid = a.grantee
            WHERE n.nspname NOT IN (''pg_catalog'', ''information_schema'')
            GROUP BY n.nspname, r.rolname
        ) sub
        GROUP BY schema_name
        ORDER BY schema_name';
END;
$$;
"""

SQL_CALL = "SELECT schema_privileges_pivot();"
SQL_QUERY = "SELECT * FROM schema_privilege_pivot;"

# ---------------------------------------------------------------------------
# Config / argument parsing
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = "config.conf"
CONFIG_SECTION = "database"


def load_config(path: str) -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    if CONFIG_SECTION in cfg:
        return dict(cfg[CONFIG_SECTION])
    return {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report schema privileges as a pivot table."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"Config file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--server", help="PostgreSQL host")
    parser.add_argument("--port", type=int, help="PostgreSQL port (default: 5432)")
    parser.add_argument("--database", help="Database name")
    parser.add_argument("--username", help="Database user")
    parser.add_argument("--password", help="Database password")
    parser.add_argument("--output", metavar="FILE",
                        help="Write output to FILE instead of stdout")
    parser.add_argument("--csv", action="store_true",
                        help="Output as CSV instead of a formatted table")
    return parser.parse_args()


def resolve(args: argparse.Namespace) -> dict:
    """Merge config file and CLI args; CLI takes precedence."""
    cfg = load_config(args.config)
    def pick(cli_val, cfg_key, default=None):
        return cli_val if cli_val is not None else cfg.get(cfg_key, default)

    params = {
        "host":     pick(args.server,   "server",   "localhost"),
        "port":     pick(args.port,     "port",     5432),
        "dbname":   pick(args.database, "database", "postgres"),
        "user":     pick(args.username, "username"),
        "password": pick(args.password, "password"),
    }
    # Remove None values so psycopg2 uses its own defaults
    return {k: v for k, v in params.items() if v is not None}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _format_table(columns: list, rows: list) -> str:
    widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)) if val is not None else 4)

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"

    lines = [
        sep,
        fmt.format(*columns),
        sep,
        *[fmt.format(*[str(v) if v is not None else "NULL" for v in row]) for row in rows],
        sep,
        f"{len(rows)} row(s)",
    ]
    return "\n".join(lines) + "\n"


def _format_csv(columns: list, rows: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(
        [str(v) if v is not None else "" for v in row]
        for row in rows
    )
    return buf.getvalue()


def write_output(columns: list, rows: list, output: str = None, as_csv: bool = False) -> None:
    content = _format_csv(columns, rows) if as_csv else _format_table(columns, rows)
    if output:
        try:
            with open(output, "w", newline="") as f:
                f.write(content)
        except OSError as e:
            sys.exit(f"Could not write to {output}: {e}")
    else:
        print(content, end="")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    conn_params = resolve(args)

    try:
        conn = psycopg2.connect(**conn_params)
    except psycopg2.OperationalError as e:
        sys.exit(f"Connection failed: {e}")

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SQL_CREATE_FUNCTION)
                cur.execute(SQL_CALL)
                cur.execute(SQL_QUERY)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
    except psycopg2.Error as e:
        sys.exit(f"Query failed: {e}")
    finally:
        conn.close()

    write_output(columns, rows, args.output, as_csv=args.csv)


if __name__ == "__main__":
    main()
