-- @group: costs
-- @materialized: view
SELECT
    *,
    -- Appel 2 de la macro : Taxes locales et frais fixes de manutention
    {{ apply_fee('gross_sales', 'tax_pct', 'fixed_handling_fee') }} AS local_tax_fee
FROM int_transport_costs;