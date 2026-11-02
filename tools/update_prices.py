"""Refresh the bundled pricing data from llm-usage-metrics v0.12.1."""
import json
from pathlib import Path
from urllib.request import urlopen

BASE = 'https://raw.githubusercontent.com/ayagmar/llm-usage-metrics/v0.12.1/src/pricing/'


def download(name):
    with urlopen(BASE + name, timeout=30) as response:
        return json.load(response)


def main():
    snapshot = download('litellm-pricing-snapshot.json')
    mapping = download('litellm-model-map.json')
    models = {}
    for name, price in snapshot['pricingByModel'].items():
        # The upstream snapshot is already in USD per million tokens.
        models[name] = {key: price.get(upstream, 0) for key, upstream in (
            ('input', 'inputPer1MUsd'), ('output', 'outputPer1MUsd'),
            ('cache_read', 'cacheReadPer1MUsd'), ('cache_write', 'cacheWritePer1MUsd'))}
    output = {'source': 'llm-usage-metrics@v0.12.1', 'models': models, 'aliases': mapping['aliases']}
    target = Path(__file__).resolve().parent.parent / 'config' / 'prices.litellm.json'
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
