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

-- Execute it, then query the result
SELECT schema_privileges_pivot();
SELECT * FROM schema_privilege_pivot;
