import os
from types import SimpleNamespace

from app.tracing import configure_tracing


def test_tracing_disabled_when_langsmith_tracing_false():
    env_before = dict(os.environ)
    try:
        for key in (
            "LANGSMITH_TRACING",
            "LANGSMITH_API_KEY",
            "LANGSMITH_PROJECT",
            "LANGSMITH_ENDPOINT",
        ):
            os.environ.pop(key, None)

        settings = SimpleNamespace(
            langsmith_tracing=False,
            langsmith_api_key=None,
            langsmith_project="test-project",
            otel_enabled=False,
            otel_exporter_otlp_endpoint=None,
            otel_service_name="test-service",
        )
        configure_tracing(settings)

        assert os.environ.get("LANGSMITH_TRACING") != "true"
    finally:
        os.environ.clear()
        os.environ.update(env_before)


def test_tracing_configures_langsmith_env_vars():
    env_before = dict(os.environ)
    try:
        for key in (
            "LANGSMITH_TRACING",
            "LANGSMITH_API_KEY",
            "LANGSMITH_PROJECT",
            "LANGSMITH_ENDPOINT",
        ):
            os.environ.pop(key, None)

        settings = SimpleNamespace(
            langsmith_tracing=True,
            langsmith_api_key=SimpleNamespace(get_secret_value=lambda: "ls__test"),
            langsmith_project="dental-test",
            otel_enabled=False,
            otel_exporter_otlp_endpoint=None,
            otel_service_name="test-service",
        )
        configure_tracing(settings)

        assert os.environ.get("LANGSMITH_TRACING") == "true"
        assert os.environ.get("LANGSMITH_API_KEY") == "ls__test"
        assert os.environ.get("LANGSMITH_PROJECT") == "dental-test"
    finally:
        os.environ.clear()
        os.environ.update(env_before)


def test_tracing_configures_otel_env_vars():
    env_before = dict(os.environ)
    try:
        for key in (
            "OTEL_SERVICE_NAME",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_TRACES_EXPORTER",
        ):
            os.environ.pop(key, None)

        settings = SimpleNamespace(
            langsmith_tracing=False,
            langsmith_api_key=None,
            langsmith_project="test-project",
            otel_enabled=True,
            otel_exporter_otlp_endpoint="http://localhost:4317",
            otel_service_name="dental-test",
        )
        configure_tracing(settings)

        assert os.environ.get("OTEL_SERVICE_NAME") == "dental-test"
        assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == (
            "http://localhost:4317"
        )
        assert os.environ.get("OTEL_TRACES_EXPORTER") == "otlp"
    finally:
        os.environ.clear()
        os.environ.update(env_before)
