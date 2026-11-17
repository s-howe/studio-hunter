import yaml

CONFIG_PATH = "config.yaml"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_config_parses():
    config = load_config()
    assert isinstance(config, dict)


def test_criteria_present_and_typed():
    config = load_config()
    criteria = config["criteria"]

    assert criteria["size_m2"]["min"] < criteria["size_m2"]["max"]
    assert isinstance(criteria["price_eur_max"], (int, float))
    assert criteria["districts"]["primary"]
    assert criteria["districts"]["fallback_enabled"] is False
    assert isinstance(criteria["hard_exclude_keywords"], list)


def test_kleinanzeigen_source_present():
    config = load_config()
    assert "kleinanzeigen" in config["sources"]


def test_logging_config_present():
    config = load_config()
    assert "log_file" in config["logging"]
    assert config["logging"]["max_bytes"] > 0
    assert config["logging"]["backup_count"] > 0


def test_every_configured_source_is_registered():
    from main import SOURCE_REGISTRY

    assert set(load_config()["sources"]) <= set(SOURCE_REGISTRY)
