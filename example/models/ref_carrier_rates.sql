-- @group: costs
-- @materialized: view
SELECT *
FROM read_parquet('{{ project_root }}/../data/ref_carrier_rates.parquet');
