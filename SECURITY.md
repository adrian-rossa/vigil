# Security policy

vigil handles cluster and cloud credentials (a Hetzner Cloud API token, GitHub App private keys or a GitHub token, kubeconfig and cluster credentials) alongside LLM provider credentials (Anthropic or Ollama API keys). These are read from environment variables; the GitHub App private keys are read from files whose paths come from environment variables. The orchestrator webhook secret is generated per cluster at provisioning time. No plaintext secret is committed.

## Reporting a vulnerability

Report a suspected vulnerability privately through the "Report a vulnerability" form under the repository's Security tab, rather than opening a public issue. A maintainer acknowledges the report within 14 days and keeps the discussion in the private advisory. Once a fix is released, the advisory is published with the affected and fixed versions.

## Supported versions

| Version  | Supported                                                                |
| -------- | ------------------------------------------------------------------------ |
| `main`   | Receives fixes                                                           |
| `v2.x`   | Latest release; fixes ship in a patch release                            |
| `v1.1.0` | Superseded by `v2.0.0`                                                   |
| `v1.0.0` | Archived evaluation release, kept unchanged for reproducibility          |
