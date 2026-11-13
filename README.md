# AI Automation Engineer

Syed Tajdar Nazar Gillani | [syedtajdar812@gmail.com](mailto:syedtajdar812@gmail.com) | [LinkedIn](https://www.linkedin.com/in/syed-tajdar-nazar-a2b431389) | [GitHub](https://github.com/Syed-Tajdar) | [Portfolio](https://syed-tajdar.vercel.app)

## Projects

- [Portfolio site](portfolio-site/): one-page Next.js site with projects, skills, experience and contact.
- [Call QA Agent](call-qa-agent/): transcribes sales and support calls and scores them against a rubric with quoted evidence.
- [n8n Lead Automation](n8n-leads/): captures leads from a web form, classifies them with an LLM, stores them in Supabase and alerts sales.
- [Bilingual RAG + MCP](rag-mcp/): English/Arabic question answering over documents with citations, evals and an MCP server.
- [Mini automations](mini-automations/): 8 small, tested automations (n8n and Python) for leads, invoices, email, reminders, prices, CVs, reports and CRM sync.
- [Calyx AI case study](calyx-case-study/): write-up of a multi-tenant call analytics platform for pay-per-call networks.

## CI

Every project has its own GitHub Actions workflow in [.github/workflows/](.github/workflows/), triggered only by changes in that project's folder. They run with mocks only: no secrets, and the live API tests stay skipped.

All Python is linted with ruff (one configuration, [ruff.toml](ruff.toml)) and the site with ESLint. CI installs the exact dependency versions in each project's `requirements.lock` (hashes checked) or `package-lock.json`, and audits them with pip-audit and `npm audit`. Actions are pinned to commit SHAs, and Dependabot proposes updates once a month in the private working copy, one grouped pull request per ecosystem.

| Project | Status | What runs |
| --- | --- | --- |
| Portfolio site | [![portfolio-site](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/portfolio-site.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/portfolio-site.yml) | typecheck, build and link check, for each region profile |
| Call QA Agent | [![call-qa-agent](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/call-qa-agent.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/call-qa-agent.yml) | pytest on Python 3.11 and 3.12, with SQL tests on a Postgres 17 service container |
| Bilingual RAG + MCP | [![rag-mcp](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/rag-mcp.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/rag-mcp.yml) | pytest on Python 3.11 and 3.12, with a pgvector Postgres service container |
| n8n Lead Automation | [![n8n-leads](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/n8n-leads.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/n8n-leads.yml) | the Docker test bench: n8n with the workflows against mock APIs, and SQL tests on Postgres 17 |
| Mini automations (Python) | [![mini-automations-python](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml) | pytest, Python 3.11, one job per automation |
| Mini automations (n8n) | [![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml) | the Docker test bench: n8n with every workflow against a mock of each API |

One-time Supabase setup (migrations of all three projects, the RAG documents, the Call QA demo calls) runs from the **setup-supabase** workflow, started by hand from the Actions tab: steps in [call-qa-agent/DEPLOY.md](call-qa-agent/DEPLOY.md#setup-from-a-phone-the-setup-supabase-workflow).

Operations run from workflows in the private working copy of this repository, which are not part of a public snapshot: the one-time Supabase and n8n setup, and these scheduled workflows. The scripts they call are in [.github/scripts/](.github/scripts/).

- **uptime**, every 3 hours: loads the portfolio, the Call QA call list, the RAG page and one cached example answer, and checks each shows real content. The demo checks read from the database, so the Supabase free project never pauses for inactivity. On failure it sends a Telegram message naming the failed checks (secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`); without them the run still fails visibly. Script: [.github/scripts/uptime.py](.github/scripts/uptime.py).
- **warm-rag-cache**, after each production deploy of the RAG demo and daily: answers the example questions into the answer cache ([rag-mcp/README.md](rag-mcp/README.md)).
- **n8n-leads-schedule**, every 6 hours and daily: starts the n8n follow-ups and daily summary, only once the `N8N_HOST` variable is set ([n8n-leads/README.md](n8n-leads/README.md)).

Hosting: [docs/hosting-decision.md](docs/hosting-decision.md) (Vercel and Supabase free tiers for the Call QA Agent and the RAG app, and why; Cloud Run kept as the container alternative in [docs/cloud-run/](docs/cloud-run/)).

## License

The code is under the [MIT License](LICENSE). The CV, the photo, the Calyx AI case study (text, PDF and diagram), the screenshots and the sample call audio are not: all rights reserved.
