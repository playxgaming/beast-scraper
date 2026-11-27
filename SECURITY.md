# Security

Keep `.env`, API keys, proxy credentials, cookies, and SQLite cache out of commits. Rotate any credential accidentally published.

For internet deployments, use `PUBLIC_MODE=true`, `ALLOW_PRIVATE_URLS=false`, and a separate `ADMIN_API_KEY`. Add `API_KEY` if the API is for your own use. Admin routes are disabled when no key is configured. The Render Blueprint applies public mode and generates an admin secret.

Targets, PDF downloads, and redirects are validated before fetches. Browser requests are checked and service workers are disabled. These are not a complete network sandbox; enforce outbound network restrictions for untrusted users and hostile DNS. Optional local adapters have their own fetchers and are disabled in public mode.

The API has process-wide rate/concurrency limits, bounded HTTP/PDF downloads and JSON bodies, and a bounded cache. Browser memory, PDF processing time, and third-party libraries still require host resource controls. Keep dependencies updated. Use one worker on the free deployment.

Report vulnerabilities through GitHub private vulnerability reporting when enabled. Do not place credentials or exploit details in a public issue.
