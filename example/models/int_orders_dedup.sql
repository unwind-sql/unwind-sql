-- @group: costs
-- @materialized: view
-- @disabled: true
-- No-op deduplication step kept around to demonstrate `@disabled` (Blender-
-- style mute). `order_id` is already unique upstream so `DISTINCT *` would
-- not change anything anyway. While disabled, the runner skips this body and
-- aliases the view to its first parent (int_order_base), so int_transport_costs
-- below sees the same rows as before.
SELECT DISTINCT *
FROM int_order_base;
