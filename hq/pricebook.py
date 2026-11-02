"""Resolve user prices and the bundled LiteLLM snapshot."""


class PriceBook:
    def __init__(self, user, litellm, aliases):
        self.user = user
        self.litellm = litellm
        self.aliases = aliases

    def get(self, model):
        if model in self.user:
            return self.user[model]
        if model in self.litellm:
            return self.litellm[model]
        return self.litellm.get(self.aliases.get(model))

    def source(self, model):
        if model in self.user:
            return 'yours'
        return 'litellm' if self.get(model) is not None else 'unpriced'
