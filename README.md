# BeastScraper

[![CI](https://github.com/playxgaming/beast-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/playxgaming/beast-scraper/actions/workflows/ci.yml)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/playxgaming/beast-scraper)

A free, self-hostable Python API for webpages, crawling, web search, research PDFs, and YouTube metadata/transcripts. Includes a playground, Swagger docs, SQLite caching, and a Python client. No paid scraping API, LLM subscription, or paid proxy is required.

## Public API hosting

Click **Deploy to Render**, sign in, and deploy the included `render.yaml` Blueprint. It explicitly selects the **Free** plan. Render assigns an HTTPS URL; use that URL for `/docs`, `/health`, and the endpoints below. A live URL exists only after deployment finishes.

The free deployment serves HTTP scraping, crawling, search, YouTube, and basic PDF extraction. It allows one concurrent operation, 20 requests/minute across the instance, up to five crawl pages, and 10 MB PDF downloads. Browser rendering is disabled to fit the small free instance; use the full local installation for JavaScript pages and screenshots.

Render's free services sleep after 15 idle minutes, have cold starts and usage limits, and lose local SQLite cache on restart or redeploy. Heavy outbound scraping can trigger suspension. See [Render's current free service limits](https://render.com/docs/free). This is a hobby deployment with no uptime guarantee. No paid resources are declared by this project.

`API_KEY` is empty by default, making scraping accessible without a key. Render generates a separate `ADMIN_API_KEY`. To restrict your instance, add an `API_KEY` secret in Render's Environment settings and send it in the `X-API-Key` header. Never commit secrets.

GitHub hosts the source and CI. The Python API runs on Render or your computer; GitHub Pages cannot run this server.

## Run locally

Requires Python 3.11+.

Windows PowerShell:

```powershell
git clone https://github.com/playxgaming/beast-scraper.git
cd beast-scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
Copy-Item .env.example .env
beast-scraper
```

macOS / Linux:

```bash
git clone https://github.com/playxgaming/beast-scraper.git
cd beast-scraper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install --with-deps chromium
cp .env.example .env
beast-scraper
```

Open the [playground](http://127.0.0.1:8000/), [Swagger docs](http://127.0.0.1:8000/docs), or [OpenAPI schema](http://127.0.0.1:8000/openapi.json). Chromium is needed only for browser rendering.

For richer PDF layout extraction, install `python -m pip install -e '.[pdf]'` and set `PDF_LAYOUT_ENABLED=true`. The default parser uses PyMuPDF text extraction with page headings or page chunks. The optional parser has larger dependencies and higher memory use. Optional Crawl4AI and Scrapling adapters use `.[pro]` and are intended for trusted local use.

## Docker

```bash
docker build -t beast-scraper .
docker run --rm -p 127.0.0.1:8000:8000 beast-scraper
```

For local SearXNG search, copy `.env.example` to `.env` and run `docker compose up --build`. Compose binds both services to localhost and stores cache in `./data`. Docker honors `HOST` and `PORT`, including the port supplied by a hosting platform.

## API examples

Replace `http://127.0.0.1:8000` with your deployed base URL. In Windows PowerShell use `curl.exe`.

```bash
# Webpage to Markdown
curl 'http://127.0.0.1:8000/scrape?url=https://example.com&engine=http&format=markdown'

# JavaScript rendering (local/full instance)
curl 'http://127.0.0.1:8000/scrape?url=https://example.com&dynamic=true&screenshot=true'

# Crawl up to five pages
curl -X POST 'http://127.0.0.1:8000/crawl' \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://docs.python.org/3/","max_pages":5,"max_depth":1}'

# /google is an alias, not an official Google API
curl 'http://127.0.0.1:8000/search?query=python+fastapi&results=5'
curl 'http://127.0.0.1:8000/papers/search?query=attention&limit=5'
curl 'http://127.0.0.1:8000/papers/parse?identifier=1706.03762&max_pages=3'
curl 'http://127.0.0.1:8000/youtube/info?url=dQw4w9WgXcQ'
curl 'http://127.0.0.1:8000/youtube/transcript?url=dQw4w9WgXcQ&format=markdown'

# If API_KEY is configured
curl -H 'X-API-Key: YOUR_KEY' 'http://127.0.0.1:8000/search?query=python'
```

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Status and enabled capabilities |
| `/scrape` | GET, POST | HTML, Markdown, text, JSON, links, images |
| `/crawl` | POST | Bounded breadth-first crawl |
| `/search`, `/google` | GET | Web/news search via SearXNG or DDGS |
| `/papers/search` | GET | arXiv, OpenAlex, Semantic Scholar search |
| `/papers/parse` | GET, POST | PDF extraction, page chunks, BibTeX |
| `/youtube/info` | GET | Video/playlist metadata (up to 50 playlist entries) |
| `/youtube/transcript` | GET | JSON, text, Markdown, SRT captions |
| `/youtube/search` | GET | Video search |
| `/admin/proxies` | GET, POST | Proxy pool; admin key required |
| `/admin/cache` | DELETE | Clear cache; admin key required |

## Python client

```python
from beast_scraper.client import BeastScraperClient

client = BeastScraperClient(base_url="http://127.0.0.1:8000")
page = client.scrape("https://example.com", engine="http")
print(page["content"])
```

The client is synchronous. For async callers, use `httpx.AsyncClient` against the endpoints.

## Configuration

Settings are environment variables; see `.env.example`.

- `API_KEY`: optional key for scraping/search routes. Prefer `X-API-Key`; query keys are supported for compatibility but can appear in access logs.
- `ADMIN_API_KEY`: header-only admin key, falling back to `API_KEY`. With neither configured, admin returns 403.
- `PUBLIC_MODE=true`: disables client-supplied proxies and optional adapters. Requires `ALLOW_PRIVATE_URLS=false`.
- `BROWSER_ENABLED`: controls Playwright and browser fallback.
- `API_REQUESTS_PER_MINUTE`, `MAX_CONCURRENT_REQUESTS`: process-wide limits; busy instances return 429 with `Retry-After`.
- `WEB_MAX_MB`, `PDF_MAX_MB`, `MAX_CRAWL_PAGES`: resource limits. JSON bodies are limited to 64 KiB. Cache keeps at most 256 entries and omits payloads above 1 MiB.
- `OPENALEX_API_KEY`: optional [free OpenAlex key](https://developers.openalex.org/). arXiv and Semantic Scholar remain available without it. Providers enforce their own quotas.
- `SEARXNG_URL`: optional SearXNG JSON API; DDGS is the fallback.
- `PROXIES`, `PROXY_FILE`, `PROXY_TEMPLATE`: optional proxies. Country routing needs a proxy supporting the requested location.

Web/PDF targets and redirects are checked for private/local addresses. Playwright checks network requests and blocks service workers. These are application protections, not a network sandbox: hostile DNS and browser resources still require network-level egress restrictions and authentication for an untrusted multi-user service. Optional local adapters use their own fetchers. Use one worker to keep the limits process-wide.

YouTube may block datacenter IPs or omit captions; search providers/websites can throttle or reject requests. Follow each site's terms, permissions, and crawl policies; the crawler does not automatically enforce `robots.txt`.

## Development

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest -q
python -m build
```

For Windows systems with a locked system temp directory, use `pytest -q --basetemp=.pytest-tmp` (pytest owns this disposable directory). CI checks Python 3.11–3.13, wheel assets, and Docker health. Tests mock external providers; they do not promise live upstream availability.

## License

[GNU AGPLv3](LICENSE). Dependencies retain their respective licenses, including PyMuPDF and optional PyMuPDF4LLM.
