-- @group: costs
-- @materialized: view
SELECT
    *,
    (weight_kg * cost_per_kg) + (distance_km * cost_per_km) AS base_shipping_cost,
    -- Appel 1 de la macro : Surcharge carburant (Pas de frais fixes ici, on passe 0)
    {{ apply_fee('(weight_kg * cost_per_kg) + (distance_km * cost_per_km)', 'fuel_surcharge_pct', '0') }} AS fuel_surcharge_fee
FROM int_order_base;