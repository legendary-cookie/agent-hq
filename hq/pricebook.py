"""Resolve user prices and the bundled LiteLLM snapshot."""


def normalize_model(model):
    return model.casefold().split('/', 1)[-1] if model else model


class PriceBook:
    def __init__(self, user, litellm, aliases):
        self.user = user
        self.litellm = litellm
        self.aliases = aliases
        self._normalized_litellm = {}
        self._normalized_aliases = {}
        for name, value in litellm.items():
            self._normalized_litellm.setdefault(normalize_model(name), value)
        for name, value in aliases.items():
            self._normalized_aliases.setdefault(normalize_model(name), value)

    def _lookup(self, model, prices):
        if model in prices:
            return prices[model]
        normalized = normalize_model(model)
        return next((price for name, price in prices.items() if normalize_model(name) == normalized), None) if normalized else None

    def get(self, model):
        if model and model.startswith('<'):
            return None
        price = self._lookup(model, self.user)
        if price is not None:
            return price
        if model in self.litellm:
            return self.litellm[model]
        target = self.aliases.get(model) or self._normalized_aliases.get(normalize_model(model))
        if target in self.litellm:
            return self.litellm[target]
        return self._normalized_litellm.get(normalize_model(model))

    def source(self, model):
        if model and model.startswith('<'):
            return 'unpriced'
        if self._lookup(model, self.user) is not None:
            return 'yours'
        return 'litellm' if self.get(model) is not None else 'unpriced'
