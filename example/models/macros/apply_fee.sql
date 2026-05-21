{% macro apply_fee(base_amount_col, pct_rate_col, fixed_fee_col) %}
    ROUND(
        ({{ base_amount_col }} * COALESCE({{ pct_rate_col }}, 0))
        + COALESCE({{ fixed_fee_col }}, 0),
        2
    )
{% endmacro %}