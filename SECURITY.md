# Security

SongForge is a **local-only** tool. The Node server binds to `127.0.0.1` by default and rejects requests whose `Host` or `Origin` is not local.

Do not expose port `4173` to the internet. The `/api/test` and `/api/generate` endpoints proxy a model request using a key supplied by the caller; making them public would turn the server into an open proxy for arbitrary API keys and URLs.

API keys are entered in the page, kept in that tab's memory only, and sent to the local Node process for the current request. They are not written to disk, localStorage, logs or generated files.

To report a vulnerability, open a private security advisory on the repository rather than a public issue.
