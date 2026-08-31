# Assure

Ask one question. Get one verified answer. The question is rewritten for the model you pick, then checked.

The local UI is `http://127.0.0.1:8765`. The compiler is PEM (`prompt_matrix`).

```bash
cd prompt_matrix
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ..
assure --web
```

`pip install prompt-matrix` is not on PyPI yet. Pro is $5 per month on the Pricing page in this tree. Copy stays on this computer. A Send goes only to the provider you chose.

Install, CLI, and MCP details: [prompt_matrix/README.md](prompt_matrix/README.md). Product overview: [prompt_matrix/PEM.md](prompt_matrix/PEM.md). Public landing: [landing/index.html](landing/index.html).

## Cloudflare Pages

GitHub [`orhgor/assure`](https://github.com/orhgor/assure) holds the **webpage only** (branch `webpage`, site at repo root). The workbench and PEM engine stay on this machine. Do not push `main` to that remote.

In Cloudflare Pages, connect the repo, production branch `webpage`, root directory `/`, no build command. Then attach `assure.ai`.
