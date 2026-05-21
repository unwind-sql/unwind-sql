-- @group: margin
-- @materialized: view
SELECT
    t.order_id,
    t.warehouse_id,
    t.gross_sales,
    t.base_shipping_cost,
    t.fuel_surcharge_fee,
    t.local_tax_fee,
    COALESCE(r.refund_amount, 0) AS refund_amount,
    -- Formule : Ventes - (Transport + Surcharge + Taxes + Remboursements)
    t.gross_sales - (t.base_shipping_cost + t.fuel_surcharge_fee + t.local_tax_fee + COALESCE(r.refund_amount, 0)) AS net_margin
FROM int_tax_costs t
LEFT JOIN raw_refunds r ON t.order_id = r.order_id;