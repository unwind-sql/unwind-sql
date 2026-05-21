-- @group: costs
-- @materialized: view
SELECT *
FROM read_parquet('{{ project_root }}/../data/ref_local_taxes.parquet');
