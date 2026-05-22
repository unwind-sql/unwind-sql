-- Profitabilité agrégée par entrepôt.
-- Une ligne par warehouse_id avec ses totaux de revenu, marge et remboursements.
-- @group: margin
SELECT
    warehouse_id,                              -- identifiant de l'entrepôt
    COUNT(order_id) AS total_orders,           -- nombre de commandes traitées
    SUM(gross_sales) AS total_revenue,         -- chiffre d'affaires brut (EUR)
    SUM(net_margin) AS total_net_margin,       -- marge nette agrégée (EUR)
    SUM(refund_amount) AS total_refunds        -- total des remboursements (EUR)
FROM int_net_margin_per_order
GROUP BY 1;