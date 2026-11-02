def normalize_provider(provider: str | None, model: str | None) -> str | None:
    if provider:
        return provider
    values = ((model or '').lower(),)
    if any('deepseek' in value for value in values):
        return 'deepseek'
    if any('kimi' in value or 'moonshot' in value for value in values):
        return 'moonshot'
    return provider
