"""Model roster for the benchmark report.

Kept in its own module so this public branch can ship a roster *without* the internal models while
report.py stays byte-identical to the internal branch. INTERNAL_MODELS is empty here: this repo carries
no internal models or their result dirs, so nothing is hidden — report.py simply renders what ships.
"""

MODELS = ["gpt_low", "gpt_high",
           "haiku_low",  "haiku_high" ]
LABELS = {                                            # human-readable display names for table headers/rows
    "gpt_none": "gpt-5.4-mini (no think)", "gpt_low": "gpt-5.4-mini (low)",
    "gpt": "gpt-5.4-mini (med)", "gpt_high": "gpt-5.4-mini (high)", "gpt55": "gpt-5.5 (med)",
    "haiku": "haiku-4.5 (no think)", "haiku_low": "haiku-4.5 (low)",
    "haiku_med": "haiku-4.5 (med)", "haiku_high": "haiku-4.5 (high)", "opus": "opus-4.8 (med)",
}
INTERNAL_MODELS = set()                               # public branch: nothing to hide
