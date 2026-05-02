"""Tests for public package exports."""


def test_top_level_observability_logger_export():
    from kvault import ObservabilityLogger
    from kvault.core.observability import ObservabilityLogger as CoreObservabilityLogger

    assert ObservabilityLogger is CoreObservabilityLogger
