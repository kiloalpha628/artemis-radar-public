# Security

## Deployment boundary

The built-in HTTP service is a read-only cache endpoint intended for loopback, a trusted LAN, or a protected reverse proxy. It has no authentication, TLS, or rate limiting. Publishing this repository does not require making the service publicly reachable. Use a reverse proxy to enforce access controls and TLS if external access is needed.

Only the `public/` subtree of `DATA_DIR` should be served. Do not put credentials, personal files, or symlinks to private files there: Python's static-file handler follows symlinks. The worker writes downloaded sources under `raw/` and staging files under `tmp/`, outside the served tree. The web container mounts the cache read-only; the worker is the only writer.

NOAA/Unidata requests are anonymous. Optional Open-Meteo requests send the radar's coordinates, rounded to 0.01 degrees. Keep local configuration in ignored `.env` files, and retain dependency license notices when redistributing images.

## Reporting

For a vulnerability, use GitHub's **Security → Report a vulnerability** on this repository if private reporting is enabled. If it is unavailable, open an issue asking the maintainer for a private reporting channel, without posting exploit details, secrets, or sensitive logs. Ordinary bugs can be reported through Issues.

This is an early project with no guaranteed response time or maintained release branches. Security fixes target the current development branch.
