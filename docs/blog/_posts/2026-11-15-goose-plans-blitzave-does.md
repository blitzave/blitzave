---
title: "Goose plans. BlitZave does."
description: "BlitZave now answers questions about your money in plain words. The model behind it never reads a transaction, never sees a result, and never types a number you'll see. Here's how, and the leak I found in my own design."
tag: Engineering
date: 2026-11-15 23:34:00
# Kept off the site until it's ready. Delete this line to publish it.
published: false
---

BlitZave has a new face. Its name is Goose.

Goose is BlitZave's persona: the calm ally that has your back, never a scorekeeper. In the app, Goose is where you ask about your own money. "Tag May 13 to 27 as PNW." "How's my grocery spending been?" "Show me what I spent at Costco last month."

Under it is a language model. Ollama on your laptop by default, or Claude if you opt in.

That sentence should make you nervous. It made me nervous. A finance app plus an AI agent is how your bank statements end up in someone's training data, or how a merchant name like "IGNORE PREVIOUS INSTRUCTIONS" ends up re-filing your rent.

So Goose is an agent with no eyes. It reasons about your question and decides what to do: which transactions, which tool, whether a summary is the answer. It just never sees what any of that finds. This post is about how that works.

## Why not the usual tool loop

The obvious design is the one everyone ships. Give the model some tools — "search transactions", "sum a category", "tag these" — and let it loop: call a tool, read the result, decide what to do next, answer.

I planned to do exactly that. The first version of this feature was going to be an MCP server, so you could ask BlitZave questions from whatever AI app you already use.

I shelved it on November 14th, before writing a line of it, because of one fact: **in a tool loop, the model reads the tool results.**

That's two problems, not one.

1. **Privacy.** Every row a tool returns goes to the model. With a model on someone else's server, your transactions leave your laptop, one question at a time.
2. **Prompt injection.** Bank descriptions are text written by strangers. Any merchant can put words in your statement. A model that reads them can be told things by them, and a model that can also *write* — tag, re-categorize — can be told to do things.

You can try to filter both. I didn't want to try. I wanted a design where neither can happen.

## The one-way rule

Goose has one rule, and everything else falls out of it:

> The model plans. It never reads your data, and nothing comes back to it.

That still leaves the model doing an agent's real work. It works out what you meant, picks the tools, fills in the filters, puts the steps in order, and decides whether you want a list, a change or a summary. What it gives up is the loop. It plans once, up front, without seeing a single result, and BlitZave carries the plan out.

Concretely, a question goes through three steps.

**1. Plan.** The model gets your question, today's date, and your labels: your category and tag names, and your accounts as aliases like `A1 (checking)`. Never an account's name or number, never your bank's name. It answers with one JSON plan — one to three intents from a short, fixed list:

| Intent | What the engine does |
|---|---|
| `show_transactions` | Lists the rows a filter matches |
| `propose_tag` | Previews putting a tag on them |
| `propose_category` | Previews filing them under a category |
| `spending_summary` | Works out a category's, a tag's, or all spending, month by month |
| `clarify` | Asks which of two labels you meant |
| `decline` | Says no, in fixed words (investment advice, for one) |

The plan's schema has your labels baked in as the only values a category, tag or account can take. Ollama turns that schema into a grammar, so the model *can't* produce anything else. Claude gets it as structured outputs. Then Pydantic checks it again anyway. A plan that doesn't fit gets one repair, which quotes only the model's own reply back to it. Two misses and the card says so, in fixed words.

None of this is my invention. It's the **Action-Selector** pattern, with a little of **Plan-Then-Execute**, from [Design Patterns for Securing LLM Agents against Prompt Injections](https://arxiv.org/abs/2506.08837) (Beurer-Kellner et al., 2025). The paper's point is that an agent can't be talked into anything by text it never reads. That was exactly the property I wanted.

**2. Run.** The engine runs each intent in its own lane, on your laptop, in plain Python and SQL. The model isn't involved. It doesn't learn whether anything matched, how many rows there were, or what they cost.

The lane that writes is the careful one. A tag or a category is only ever a **preview** first: a list with a checkbox per row. Nothing changes until you press Apply. Apply takes back only rows the preview showed, once, in one commit. Undo takes back exactly what Apply wrote. Card payments and transfers are left out, so tagging a trip never catches the credit card bill.

**3. Show.** Every card is in BlitZave's words, not the model's. And each card shows how your question was read — "May 13 – May 27", "Tag: PNW" — because the planner is the one part that can misread you, and you should see when it did.

## Numbers by reference

There's one place a model's words can reach the screen. A summary can have a model-written sentence, if you want it to sound less like a form.

The rule there: **the model never types a number.** It writes slots — `{label}`, `{average}`, `{latest_month}`, `{trend}` — and a function called `fill()` swaps in BlitZave's own figures. If the sentence has a typed digit, a `$`, a month name, a number in words, a scorekeeper word like "overspent", or a slot BlitZave didn't give, `fill()` throws it away and shows BlitZave's own sentence instead.

So the model writes:

> Over {months} months, {label} averaged {average}, and {latest_month} ran {trend}.

And you read:

> Over 6 months, Groceries averaged $110.00, and October ran higher than usual.

Every number in it came from deterministic code reading your own data. That was a rule in BlitZave long before any model was in it: *no invented numbers.*

## The leak I found in my own design

Here's the part I didn't get right the first time.

The writer was writing `{average}` instead of `$110.00` — but it was being *told* `{average} = $110.00`. Every slot went over with its value attached, so it could word the sentence sensibly.

Which means that with Claude picked as your model, every summary sent Anthropic a category's monthly totals, its average, its highest and lowest month, and whether last month was up. Not a transaction. But a result, and my own rule said a model never sees one.

I found it reviewing the feature before it merged. It was even in the disclosure screen — "for a summary, the monthly totals of what you asked about" — which is the honest version of a bug, but still a bug. The point of writing *by reference* is that the writer doesn't need the values. So now it doesn't get them.

The writer now gets the fixed list of slot names and what each one means. That's the same request whatever your numbers are, and there's a test that changes every figure and checks the request didn't move by a byte.

That fix has a knock-on. A writer that can't see the numbers can only *guess* whether one is bigger than another. So `fill()` now also refuses any comparison the model typed — "more than", "higher", "as usual" — even a true one. How a month compares comes only from BlitZave's `{trend}`.

## How I know it holds

Five guarantees, each with a test:

1. **No row or result reaches a model.** In the test data every description, merchant and account name contains the word `CANARY`. No request a model gets ever contains it, or a dollar amount, or a month a summary picked out.
2. **Nothing flows back to the planner.** Ask the same question after a show, a preview, an Apply and a summary, and the planner gets a byte-identical request.
3. **No number or comparison a model typed is shown.** `fill()`, tested sentence by sentence.
4. **Nothing changes without your click.** Previews write nothing. Apply outside the preview is refused. A second Apply is refused. Undo restores.
5. **"Remote" is decided by where the bytes go.** An Ollama on another machine on your network counts as remote, and needs the same OK as Claude. A Claude API key lives in your keychain, never in the database, a backup or an export.

## What it's like on a real model

The tests use a scripted model. So before merging, I put the same four questions to three real ones, all on made-up data: two on my laptop through Ollama, and Claude Haiku 5.5.

| Question | qwen3:1.7b | qwen3:8b | Claude Haiku 5.5 |
|---|---|---|---|
| How's my grocery spending been? | One month only | Timed out | Right: three months, with its own sentence |
| Show what I spent May 13–27 | Added filters nobody asked for, so nothing matched | Right | Right |
| Tag May 13–27 as PNW trip | Same stray filters, so nothing to tag | Timed out | Right: a preview of 2 to tag |
| Should I put my savings in index funds? | Became a summary of Dining | Declined | Declined |

The privacy held on all three, including the one that left my laptop. No description, no amount, no account number in any request.

Model size matters, a lot. The 1.7-billion-parameter model is small enough to run anywhere, and it can't plan. `qwen3:8b` planned everything it finished, but it's slow enough that two questions ran past the two-minute timeout. Haiku got all four right in about thirteen seconds. Its summary sentence passed `fill()` on the first try:

> Your {label} so far this month is {so_far}. In {latest_month}, the latest whole month, it came to {latest}, which is {trend}.

Four questions isn't an eval, and the next job is a real one. But the shape is clear. The best planner here is the one that isn't on your laptop. That's why it's opt-in, behind a screen that lists exactly what it gets. And because of the one-way rule, that list is short.

As for the small model, I'll take that trade. A small model making a bad plan gets you a wrong list with its wrong reading printed on top. It can't leak, and it can't change anything without your click. Making the planner *smarter* is a prompt-and-eval problem I can work on. Making an agent *safe* after the fact is not.

## What it gives up

This design is deliberately less capable than an agent that reads its own results:

- **No open-ended "why".** Goose can show you, tag, file and add up. It can't go digging through your rows and come back with a theory, because nothing that thinks ever reads them.
- **No memory.** Each question stands alone. "Now tag those" doesn't know what "those" were.
- **A short list of things it can do.** Six intents. Anything else gets a polite "not yet".

Those are real limits. They're also the reason I'm comfortable letting a model anywhere near your money.

## If you're building one

- **Ask what the model reads, not what it can do.** In a tool loop, it reads everything the tools return.
- **Let the model choose, and let your code act.** A plan from a fixed list is easy to check. A free-form tool loop isn't.
- **Pass numbers by reference.** If the model never sees the value, it can't leak it or get it wrong.
- **Print the plan back to the user.** The model will misread people. Make it obvious when it does.

**Try it.** Goose is in BlitZave now. Install from the [guide](../../guide/), then open Home and press "Ask Goose about your money". It starts with a model on your laptop; Claude is opt-in, after a screen that lists exactly what it would get. Tell me what it misread on [Discord](https://discord.gg/akSZ794XxG).
{: .note}
