# Objections

Answers for privacy-conscious consultants, researchers, and analysts. Copy stays on this computer. A Send goes only to the provider you connected.

## Why not just use ChatGPT?

ChatGPT is one model in one box. You paste, it answers, you hope.

Assure is the step after that. You pick Gemini, DeepSeek, Claude, Kimi, or a runner closed to the internet. Compare & Validate sends the same question to more than one model and shows where they agree. Check against my files drops dates, percents, and publication names that were not in the upload.

You can still use ChatGPT. Paste the compiled prompt if you want Copy instead of Send. Assure is not a new model.

## Is this just another AI wrapper?

A wrapper that only forwards your text is a thin tab. Assure compiles the question for the dialect of the model you picked, can run two or more models, then filters unsourced claims against your files. Keys live in `.env` on this machine. There is no second Assure cloud that stores the brief.

If that is still "a wrapper," so is every local app that calls an API. The product is the compile, the overlap view, and the citation pass, not a new chatbot personality.

## How is this different from PromptLayer?

PromptLayer, LangSmith, and similar tools are for teams that version prompts, log traces, and run evals as software. That is a real job. It is a different job.

Assure is a workbench for one person who needs one checked answer from models they already pay for. History and thumbs stay on this machine. `pem eval` exists for a local JSON set. It is not an observability suite and it does not replace PromptLayer.

## Is it really private?

Two modes, said plainly.

Closed to the internet: the model runs on this computer. The question and answer do not leave.

Open to the internet: the model runs on the provider's servers. The question and answer go there. A Send goes only to the API you connected. Assure does not keep a copy of the text.

Copy the prompt never calls a provider.

Keys are in `.env`. If you later sign in for billing, the cloud stores email and subscription tier. Nothing else.

Do not say "your data never leaves your machine" while Send is the default. That claim is false.

## Do I need to be a developer to use this?

You need to install Python, run `assure --web`, and paste an API key on Connect. That is more than opening a website. It is less than writing a pipeline.

The Compose UI is three steps: who answers, what kind of answer, write the question. Example chips fill the box. You do not need to know Jinja, MCP, or the CLI. Those exist for people who want them.

If install is a hard stop, this product is not for that person yet. `pip install prompt-matrix` is not on PyPI yet. Install from the repo.
