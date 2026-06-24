"""Shared utilities used by both microservices.

This package is intentionally dependency-light so it can be copied into each
service's container image. It contains cross-cutting concerns only (tracing,
structured logging, metrics) -- never any business logic or shared state.
"""
