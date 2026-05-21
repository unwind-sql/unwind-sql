-- @group: costs
-- @materialized: view
SELECT o.order_id,
    o.warehouse_id,
    o.gross_sales,
    s.weight_kg,
    s.distance_km,
    c.cost_per_kg,
    c.cost_per_km,
    c.fuel_surcharge_pct,
    t.tax_pct,
    t.fixed_handling_fee
FROM raw_orders o
    JOIN raw_shipments s ON o.order_id = s.order_id
    JOIN ref_carrier_rates c ON s.carrier_id = c.carrier_id
    JOIN ref_local_taxes t ON o.warehouse_id = t.warehouse_id
WHERE o.qty > 0;