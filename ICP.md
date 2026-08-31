# Ideal customer profile

Assure is for one person on one machine who cannot paste client notes, unpublished research, or unpublished numbers into a random chat.

Primary ICP: privacy-conscious professionals. Strategy consultants, researchers, and analysts first. Writers who handle source material sit next to that group.

Personas below are archetypes for copy, not customers. There is no public customer list yet. Do not treat these names as testimonials.

## What they share

They already pay for at least one model. The job is not "get an AI." The job is "use AI on sensitive material without looking sloppy or leaking the brief."

They have tried one-box chat. They have also tried running the same question through two products and merging the answers by hand.

They buy when a leak, a fake citation, or a weekend of copy-paste becomes more expensive than $5 a month.

They object when a landing page says the data never leaves, then the product Sends by default. Copy stays on this computer. A Send goes only to the provider they connected. That sentence has to stay in the pitch.

## Persona 1. Lena, independent strategy consultant

Role: solo or two-person shop. Client work is decks, vendor shortlists, and "what should we do" memos. NDAs are normal. She is not a developer.

Pain: ChatGPT will draft the memo. It will also invent a market-share number. She cannot put that in a client slide. Pasting the same brief into Claude and Gemini, then arguing with herself in a third doc, eats Sunday night. She will not upload the client's spreadsheet to a prompt playground she does not control.

Buying trigger: a partner asks "where did this 18% come from" and she cannot point at a file. Or a client forbids cloud tools for the engagement.

Objections: "I already have ChatGPT Plus." "This looks like a wrapper." "I do not want to install Python."

Alternatives she tried:

- ChatGPT or Claude.ai. Fast first draft. One model. Files leave her machine. Invented citations survive if she does not check.
- Perplexity. Fine for public news. Wrong for a private brief.
- A shared Notion AI or firm ChatGPT seat. Convenient. The prompt history is not hers.

Why Assure: Compare & Validate shows where two models agree. Check against my files drops dates and percents that were not in the upload. Copy keeps the compiled prompt here if the NDA says nothing leaves.

Messaging: "Hand the client one checked answer, not four chat tabs."

## Persona 2. Marek, researcher

Role: academic, think-tank, or investigative researcher. He attaches PDFs and notes. He needs the claim, the method, and the limits. A hallucinated journal name is a career problem.

Pain: models write fluent literature reviews. They invent papers. Grounding in a web chatbot still is not "this PDF." He does not want the unpublished draft in someone else's training story, even if the vendor says it is not used that way.

Buying trigger: a reviewer flags a citation he cannot find. Or a funder forbids sending identifiable data off-machine.

Objections: "Claude Projects already has my PDFs." "I can just tell it not to invent citations." "I need live search."

Alternatives he tried:

- ChatGPT / Claude with a PDF attached. The file goes to that vendor. One model. The "do not invent citations" instruction is a wish, not a filter.
- Perplexity or Gemini with search. Live web. Not his corpus. Assure has no live search. That is a limit. Say it.
- Zotero plus a chat window. Good library. Still a manual check.

Why Assure: Research intent asks for claim, method, and limits. Ground strips publication names that were not in the attach. Closed to the internet keeps the question on this computer when that is the rule.

Messaging: "Citations have to appear in what you attached, or they go."

## Persona 3. Priya, analyst

Role: equity, ops, or policy analyst. She compares options and writes the numbers she can defend. She already has two API keys because she does not trust a single model on a close call.

Pain: Gemini says one thing, DeepSeek another. She pastes both into a sheet. There is no overlap view. A round number with no source still slips into the note.

Buying trigger: she ships a comparison that a second model would have fought, and her manager catches it. Or she is tired of paying for two chats and a third place to merge them.

Objections: "PromptLayer already logs my prompts." "I can write a Python script." "Team workspaces?"

Alternatives she tried:

- Two browser chats and a Google doc. Works. It is slow and the merge is her.
- PromptLayer, LangSmith, or similar. Prompt versions, traces, evals. That is ops for people who ship prompts as software. It is not "ask once, see agreement, strip unsourced percents."
- A local notebook that calls LiteLLM. She can build that. She does not want to maintain it.

Why Assure: Compare & Validate is the merge. Analysis intent asks what the numbers do not prove. Team edition in this repo is unlimited Sends on this machine, not a shared workspace.

Messaging: "See the fight between models before you put a number in the note."

## What we do not claim

- That Send stays on the machine.
- That Assure replaces Gemini, Claude, DeepSeek, or Kimi subscriptions. You still connect those keys.
- That Team is shared workspaces. It is not in this repo.
- That there are named customers or usage percentages. Data not available in current context.

## Intent map (engine ids stay)

| UI intent | ICP job |
| --- | --- |
| Research | Papers, notes, claim / method / limits |
| Comparison | Vendor, policy, or cloud A vs B |
| Analysis | What the numbers mean and do not prove |
| Design | A plan or draft to hand over |
| Debug | A problem to find and fix (not "policy drafting") |
