"""Tests for public package exports."""


def test_top_level_observability_logger_export():
    from kvault import ObservabilityLogger
    from kvault.core.observability import ObservabilityLogger as CoreObservabilityLogger

    assert ObservabilityLogger is CoreObservabilityLogger


def test_top_level_search_exports():
    from kvault import SearchDocument, SearchResult, search_nodes
    from kvault.core.search import SearchDocument as CoreSearchDocument
    from kvault.core.search import SearchResult as CoreSearchResult
    from kvault.core.search import search_nodes as core_search_nodes

    assert SearchDocument is CoreSearchDocument
    assert SearchResult is CoreSearchResult
    assert search_nodes is core_search_nodes


def test_top_level_version_is_single_sourced():
    from kvault import __version__
    from kvault._version import __version__ as source_version

    assert __version__ == source_version
    assert __version__.count(".") == 2
