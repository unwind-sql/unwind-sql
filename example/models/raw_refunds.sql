-- @group: margin
-- @materialized: view
SELECT *
FROM read_parquet('{{ project_root }}/../data/raw_refunds.parquet');
