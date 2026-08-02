import json
from difflib import SequenceMatcher

import pandas as pd
from django.conf import settings

try:
    from anthropic import Anthropic
    _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None
except ImportError:
    _client = None


def _tabular_diff_pct(old_path, new_path):
    old_df = pd.read_csv(old_path)
    new_df = pd.read_csv(new_path)

    if old_df.shape != new_df.shape:
        max_cells = max(old_df.size, new_df.size, 1)
        overlap = min(old_df.shape[0], new_df.shape[0])
        changed_cells = (old_df.iloc[:overlap] != new_df.iloc[:overlap]).sum().sum() if overlap else 0
        added_removed = abs(old_df.shape[0] - new_df.shape[0]) * old_df.shape[1]
        return 100 * (changed_cells + added_removed) / max_cells, old_df, new_df

    changed_cells = (old_df != new_df).sum().sum()
    return 100 * changed_cells / max(old_df.size, 1), old_df, new_df


def _byte_level_diff_pct(old_path, new_path):
    with open(old_path, "rb") as f1, open(new_path, "rb") as f2:
        ratio = SequenceMatcher(None, f1.read(), f2.read()).ratio()
    return 100 * (1 - ratio)


def _summarize_with_ai(old_df, new_df, diff_pct):
    if _client is None:
        return {}
    prompt = f"""Compare these two dataset snapshots and summarize the changes.
Old shape: {old_df.shape}, columns: {list(old_df.columns)}
New shape: {new_df.shape}, columns: {list(new_df.columns)}
Cells changed: {diff_pct:.1f}%

Return ONLY valid JSON, no preamble, in this exact shape:
{{
  "columns_added": [...], "columns_removed": [...],
  "rows_added": <int>, "rows_removed": <int>,
  "notable_value_changes": ["<short description>", ...],
  "overall_summary": "<one sentence>"
}}"""
    try:
        response = _client.messages.create(
            model="claude-sonnet-4-6", max_tokens=500, messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {}


def compute_diff(old_local_path, new_local_path, file_type):
    if file_type in ("csv",):
        pct, old_df, new_df = _tabular_diff_pct(old_local_path, new_local_path)
        return pct, _summarize_with_ai(old_df, new_df, pct)
    pct = _byte_level_diff_pct(old_local_path, new_local_path)
    return pct, {}