"""CostLens Kubernetes agent: reads cluster state (read-only) and sends a trimmed copy
to CostLens. It never reads Secrets or ConfigMaps, and drops container env, command and
args (they often hold credentials)."""

__version__ = "0.1.0"
