-- @group: margin
SELECT
    warehouse_id,
    COUNT(order_id) AS total_orders,
    SUM(gross_sales) AS total_revenue,
    SUM(net_margin) AS total_net_margin,
    SUM(refund_amount) AS total_refunds
FROM int_net_margin_per_order
GROUP BY 1;