"""Model roster for the benchmark report: the configurations the site ranks.

The 2026-11 edition runs the first four (configs/editions.json); the rest are the August 2026
edition's. INTERNAL_MODELS is empty: this repository carries no run the report has to hide.
"""

MODELS = ['haiku55_low', 'haiku55_high', 'gpt6luna_low', 'gpt6luna_high', 'gpt_low', 'gpt_high', 'haiku_low', 'haiku_high', 'qwen_low', 'qwen_high']
LABELS = {
    'gpt6luna_high': 'gpt-6-luna (high)',
    'gpt6luna_low': 'gpt-6-luna (low)',
    'gpt_high': 'gpt-5.4-mini (high)',
    'gpt_low': 'gpt-5.4-mini (low)',
    'haiku55_high': 'haiku-5.5 (high)',
    'haiku55_low': 'haiku-5.5 (low)',
    'haiku_high': 'haiku-4.5 (high)',
    'haiku_low': 'haiku-4.5 (low)',
    'qwen_high': 'qwen3.8-max (high)',
    'qwen_low': 'qwen3.8-max (low)',
}
INTERNAL_MODELS = set()
