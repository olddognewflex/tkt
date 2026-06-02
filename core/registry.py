"""Maps config.provider -> adapter class. Lazy import so a missing optional
dependency in one adapter never breaks the others."""
from .config import Config
from .errors import ConfigError

# provider name -> (module, class)
_PROVIDERS = {
    "jira": ("adapters.jira", "JiraAdapter"),
    "markdown": ("adapters.markdown", "MarkdownAdapter"),
    "github": ("adapters.github", "GithubAdapter"),
    # Phase 3 (remaining):
    # "linear": ("adapters.linear", "LinearAdapter"),
    # "qi": ("adapters.qi", "QiAdapter"),
}


def get_adapter(config: Config):
    spec = _PROVIDERS.get(config.provider)
    if not spec:
        raise ConfigError(
            f"unknown ticketing provider '{config.provider}'. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )
    module_name, class_name = spec
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)(config)
