-- @group: costs
-- @materialized: view
SELECT *
FROM read_parquet('{{ project_root }}/../data/raw_shipments.parquet');
