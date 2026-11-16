---
title: "Goose plans. BlitZave does."
description: "How BlitZave's assistant works without its model ever reading a transaction: an Action-Selector planner, a writer that never sees a number, five guarantees with a test behind each, and the leak I found in my own design."
tag: Engineering
date: 2026-11-16 01:00:00 -0600
# Kept off the site until it's ready. Delete this line to publish it.
published: false
---

[Goose](../meet-goose/) is BlitZave's assistant. You ask about your own money in plain words, and it shows, tags, files or adds up. Under it is a language model: Ollama on your laptop by default, or Claude if you opt in.

That sentence should make you nervous. It made me nervous. A finance app plus an AI agent is how your bank statements end up in someone's training data, or how a merchant name like "IGNORE PREVIOUS INSTRUCTIONS" ends up re-filing your rent.

So Goose is built the way most agents aren't. The short version:

> The model writes the query. It never reads the answer.

This post is the long version: the promises that forced the design, the pattern it's built on, the architecture, the guarantees and the tests behind them, and the leak I found in my own work before it shipped.

## Six promises an agent has to keep

BlitZave made promises long before it had an agent. Any agent inside it inherits all of them.

<div class="table-scroll" markdown="1">

| Promise | What it means for an agent |
|---|---|
| **Nothing leaves the machine by default** | With Claude, every token a model reads goes to Anthropic. Keep what a model reads to a minimum, and list it exactly before the first remote call. |
| **No invented numbers** | A model that reads 140 grocery rows and states a total did the sum in its head. A model never produces a figure you see. |
| **Bank text is untrusted** | Merchant names and memos are written by strangers. A model that can change your data never reads them. |
| **Ally, never scorekeeper** | Free model prose can scold, and a bad month is when it's most likely to. Model words are checked before they're shown. |
| **Comprehension over speed** | An agent loop picks its path at run time, so you can't list everything it might do. Use a closed set of actions you can read in one file. |
| **Bring your own model** | If it only works on a frontier model, "bring your own" is lock-in by another name. Ask for one short, schema-bound reply a laptop model can produce. |

</div>

One rule sits under all six: **a prompt asks, code enforces.** "Don't reveal data" in a system prompt is a request to a model. None of Goose's guarantees depend on a model following an instruction. Each one is a type, a schema, a check or a test, and it holds whatever the model does.

## Why the usual agent fails here

The obvious design is the one everyone ships. Give the model some tools ("search transactions", "sum a category", "tag these") and let it loop: call a tool, read the result, decide what's next, answer.

I planned to do exactly that. The first version was going to be an MCP server, so you could ask BlitZave questions from whatever AI app you already use. I shelved it on November 14th, before writing a line of it, because of one fact: **in a tool loop, the model reads the tool results.**

<figure class="diagram">
<div class="fig-wrap">
<svg viewBox="0 0 1000 290" role="img" aria-label="Left: in a tool loop, results flow from the tools back into the model, which writes the answer with numbers it typed. Right: in Goose, the plan flows one way from the planner to the engine, the engine sends rows and its own numbers straight to you, and no edge runs from the engine back to the planner.">
<defs>
<marker id="un" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-n"/></marker>
<marker id="uw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-w"/></marker>
<marker id="ua" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-a"/></marker>
<marker id="um" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-m"/></marker>
</defs>
<rect x="10" y="10" width="470" height="270" rx="14" class="d-band"/>
<text x="30" y="40" class="d-t">The usual agent: a tool loop</text>
<text x="30" y="58" class="d-s">ReAct, most frameworks, and MCP</text>
<rect x="30" y="118" width="90" height="44" rx="10" class="d-box"/>
<text x="75" y="145" class="d-t" text-anchor="middle">You</text>
<rect x="190" y="118" width="110" height="44" rx="10" class="d-mbox"/>
<text x="245" y="145" class="d-t" text-anchor="middle">Model</text>
<rect x="360" y="118" width="100" height="44" rx="10" class="d-box"/>
<text x="410" y="145" class="d-t" text-anchor="middle">Tools, data</text>
<line x1="120" y1="130" x2="188" y2="130" class="d-e" marker-end="url(#un)"/>
<text x="154" y="122" class="d-l" text-anchor="middle">question</text>
<text x="330" y="100" class="d-s" text-anchor="middle">repeats until done</text>
<line x1="300" y1="130" x2="358" y2="130" class="d-e" marker-end="url(#un)"/>
<text x="329" y="122" class="d-l" text-anchor="middle">call</text>
<line x1="360" y1="152" x2="302" y2="152" class="d-e w" marker-end="url(#uw)"/>
<text x="331" y="180" class="d-l w" text-anchor="middle">results: rows,</text>
<text x="331" y="194" class="d-l w" text-anchor="middle">bank text</text>
<path d="M245,162 V236 H75 V164" class="d-e w" marker-end="url(#uw)"/>
<text x="160" y="256" class="d-l w" text-anchor="middle">answer, with numbers it typed</text>
<rect x="520" y="10" width="470" height="270" rx="14" class="d-band"/>
<text x="540" y="40" class="d-t">Goose: the same request, one edge deleted</text>
<text x="540" y="58" class="d-s">An Action-Selector planner</text>
<rect x="540" y="118" width="80" height="44" rx="10" class="d-box"/>
<text x="580" y="145" class="d-t" text-anchor="middle">You</text>
<rect x="680" y="82" width="110" height="44" rx="10" class="d-mbox"/>
<text x="735" y="109" class="d-t" text-anchor="middle">Planner</text>
<rect x="850" y="118" width="120" height="44" rx="10" class="d-box"/>
<text x="910" y="145" class="d-t" text-anchor="middle">Engine</text>
<path d="M580,118 V104 H678" class="d-e" marker-end="url(#un)"/>
<text x="629" y="96" class="d-l" text-anchor="middle">question</text>
<path d="M790,104 H910 V116" class="d-e m" marker-end="url(#um)"/>
<text x="850" y="96" class="d-l m" text-anchor="middle">typed plan</text>
<line x1="850" y1="150" x2="772" y2="129" class="d-e x"/>
<line x1="805" y1="133" x2="817" y2="145" class="d-x"/>
<line x1="805" y1="145" x2="817" y2="133" class="d-x"/>
<text x="806" y="174" class="d-l x" text-anchor="middle">nothing returns</text>
<path d="M910,162 V236 H580 V164" class="d-e a" marker-end="url(#ua)"/>
<text x="745" y="256" class="d-l a" text-anchor="middle">rows, preview, the engine's own numbers</text>
</svg>
</div>
<figcaption>Left: every result goes back into the model, and the model writes the answer, numbers included. Right: the same request with one edge deleted. The plan goes one way, and rows and numbers go straight from the engine to your screen.</figcaption>
</figure>

That one results edge carries four problems:

- **Your rows, to whoever runs the model.** With Claude, every transaction a tool returns goes to Anthropic, one question at a time.
- **Totals the model typed.** The number on screen is the model's arithmetic, not the engine's.
- **Merchant text, into a model with write tools.** Private data and untrusted text in one context is two thirds of what Simon Willison calls the "lethal trifecta". A merchant can put words in your statement, and a model that reads them can be told things by them.
- **A path chosen at run time.** There's no fixed list of what it might do, so there's nothing to review.

You can try to filter all of that. I didn't want to try. I wanted a design where none of it can happen.

## The one-way rule

Goose has one rule, and everything else falls out of it:

> The model plans. It never reads your data, and nothing comes back to it.

That still leaves the model doing an agent's real work. It works out what you meant, picks the action, fills in the filters, and decides whether you want a list, a change or a summary. What it gives up is the loop. It plans once, up front, without seeing a single result, and BlitZave carries the plan out.

There's a simpler way to say it. BlitZave already had a rule for code it doesn't fully trust. The UI is vibecoded, and that's safe because the API is a wall: the UI can only show what the engine already decided. **Goose puts the model on the same side of that wall.** The planner is a UI for asking. The writer is a UI for phrasing. Neither computes anything, and neither can see past the API.

## The pattern, named

None of this is my invention. [Design Patterns for Securing LLM Agents against Prompt Injections](https://arxiv.org/abs/2506.08837) (Beurer-Kellner et al., 2025) catalogues six patterns for agents that have to stay safe around text they can't trust. Goose is closest to the simplest of them, the **Action-Selector**: the model maps a request to one of a fixed set of actions and never sees what they return.

<div class="table-scroll" markdown="1">

| Pattern | How it works | Does a model read results? |
|---|---|---|
| Tool loop (ReAct) | Call a tool, read the result, pick the next step | Every one |
| Plan-then-execute | Fix the steps before reading anything; results can fill later steps | Yes, after planning |
| Dual LLM (Willison) | A privileged model plans with `$VAR` names; a quarantined model reads the data | The quarantined one does |
| CaMeL (DeepMind) | The model writes a small program; an interpreter tracks where every value came from | The quarantined one does |
| Action-Selector | Map the request to a fixed action; never see what it returns | Never |
| **Goose** | **An Action-Selector with typed arguments, plus a writer that refers to every number by name** | **Never. The writer gets slot names, not values.** |

</div>

**Why not CaMeL?** It solves a harder problem than Goose has. CaMeL is for agents whose next step depends on what they just read ("find the address in Bob's email and send him the file"). That needs control flow over values, so it needs an interpreter. Goose's plan is decided before any row is read. "Tag May 13 to 27" names its rows with a filter, and the engine does the reading. CaMeL also still has a model read your data: its quarantined model parses the untrusted text, and with Claude that model is on Anthropic's servers. And it would be the biggest thing in the engine, to serve a handful of kinds of question.

What Goose takes from each: **from Dual LLM, values travel by name.** The writer types `{average}` and the engine fills it in. **From CaMeL, the plan is data, not instructions.** Nothing a model writes is run as code or SQL. **From Action-Selector, no feedback.** Not a row, not a count, not whether you pressed Apply.

It's also older than it looks. Turning a sentence into a typed frame and letting deterministic code act on it is intent-and-slot parsing, the shape of voice assistants for a decade before LLMs. What's new is the parser. An LLM copes with phrasing a rule-based parser never could ("eating out", "since August", "the PNW thing"), and schema-constrained decoding makes its output parse every time.

## The architecture

At most two kinds of model call per question, both behind one `ChatModel.chat()` interface: the **planner** on every question, and the **writer** for summaries only. Everything else is the engine BlitZave already had, plus a thin orchestrator, `assistant.py`, that does for a question what the sync job does for a bank.

<figure class="diagram">
<div class="fig-wrap">
<svg viewBox="0 0 1000 520" role="img" aria-label="Rows travel from the store to assistant.py and on to the browser without entering the model zone. Only your words, your labels and the date reach the planner, and only slot names reach the writer. There is no edge from the engine back to the planner.">
<defs>
<marker id="an" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-n"/></marker>
<marker id="aa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-a"/></marker>
<marker id="am" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-m"/></marker>
</defs>
<rect x="600" y="104" width="380" height="374" rx="16" class="d-zone"/>
<text x="624" y="128" class="d-z">INSIDE A MODEL CALL</text>
<text x="624" y="145" class="d-s">Ollama on this laptop, or Claude if you opt in</text>
<rect x="20" y="16" width="960" height="60" rx="12" class="d-box-soft"/>
<text x="40" y="42" class="d-t">Browser · the Goose drawer</text>
<text x="40" y="61" class="d-s">Shows what the engine sends; computes nothing.</text>
<rect x="290" y="150" width="220" height="310" rx="12" class="d-box"/>
<text x="306" y="176" class="d-t">assistant.py</text>
<text x="306" y="204" class="d-m">1 labels, date, question</text>
<text x="306" y="224" class="d-m">2 plan: one call</text>
<text x="306" y="244" class="d-m">3 validate, resolve</text>
<text x="306" y="290" class="d-m">4 run the lane</text>
<text x="320" y="308" class="d-m">previews in memory</text>
<text x="306" y="386" class="d-m">5 slot names to writer</text>
<text x="306" y="404" class="d-m">6 fill() checks it,</text>
<text x="320" y="422" class="d-m">then fills the slots</text>
<rect x="20" y="262" width="180" height="72" rx="12" class="d-box"/>
<text x="36" y="290" class="d-t">store/</text>
<text x="36" y="310" class="d-m">list_transactions()</text>
<rect x="20" y="380" width="180" height="72" rx="12" class="d-box"/>
<text x="36" y="408" class="d-t">refine/spending.py</text>
<text x="36" y="428" class="d-s">pure: cents in, facts out</text>
<rect x="640" y="162" width="300" height="84" rx="12" class="d-box"/>
<text x="660" y="190" class="d-t">Planner</text>
<text x="660" y="210" class="d-m">ChatModel.chat(schema=Plan)</text>
<text x="660" y="228" class="d-s">one JSON plan; reads no data</text>
<rect x="640" y="378" width="300" height="84" rx="12" class="d-box"/>
<text x="660" y="406" class="d-t">Writer</text>
<text x="660" y="426" class="d-m">ChatModel.chat()</text>
<text x="660" y="444" class="d-s">no tools; never sees a value</text>
<line x1="350" y1="76" x2="350" y2="148" class="d-e" marker-end="url(#an)"/>
<text x="358" y="118" class="d-l">question</text>
<line x1="456" y1="150" x2="456" y2="79" class="d-e a" marker-end="url(#aa)"/>
<text x="464" y="108" class="d-l a">cards: rows,</text>
<text x="464" y="123" class="d-l a">preview, sentence</text>
<line x1="510" y1="186" x2="638" y2="186" class="d-e m" marker-end="url(#am)"/>
<text x="575" y="162" class="d-l m" text-anchor="middle">your words, labels,</text>
<text x="575" y="176" class="d-l m" text-anchor="middle">today's date</text>
<line x1="640" y1="212" x2="512" y2="212" class="d-e m" marker-end="url(#am)"/>
<text x="575" y="228" class="d-l m" text-anchor="middle">plan (JSON)</text>
<line x1="512" y1="252" x2="638" y2="252" class="d-e x"/>
<line x1="569" y1="246" x2="581" y2="258" class="d-x"/>
<line x1="569" y1="258" x2="581" y2="246" class="d-x"/>
<text x="575" y="276" class="d-l x" text-anchor="middle">results: never</text>
<line x1="290" y1="284" x2="202" y2="284" class="d-e" marker-end="url(#an)"/>
<text x="245" y="276" class="d-l" text-anchor="middle">filter → query</text>
<line x1="200" y1="314" x2="288" y2="314" class="d-e a" marker-end="url(#aa)"/>
<text x="245" y="331" class="d-l a" text-anchor="middle">rows</text>
<line x1="110" y1="334" x2="110" y2="378" class="d-e" marker-end="url(#an)"/>
<text x="118" y="361" class="d-l">monthly totals</text>
<line x1="200" y1="410" x2="288" y2="410" class="d-e" marker-end="url(#an)"/>
<text x="245" y="402" class="d-l" text-anchor="middle">facts</text>
<line x1="510" y1="402" x2="638" y2="402" class="d-e m" marker-end="url(#am)"/>
<text x="575" y="381" class="d-l m" text-anchor="middle">question + slot</text>
<text x="575" y="394" class="d-l m" text-anchor="middle">names, no values</text>
<line x1="640" y1="434" x2="512" y2="434" class="d-e m" marker-end="url(#am)"/>
<text x="575" y="452" class="d-l m" text-anchor="middle">sentence with {slots}</text>
<line x1="20" y1="502" x2="52" y2="502" class="d-e a"/>
<text x="60" y="506" class="d-s">carries your rows</text>
<line x1="190" y1="502" x2="222" y2="502" class="d-e m"/>
<text x="230" y="506" class="d-s">crosses into a model call</text>
<line x1="400" y1="502" x2="432" y2="502" class="d-e x"/>
<text x="440" y="506" class="d-s">the usual agent's results edge, deleted</text>
</svg>
</div>
<figcaption>Blue carries your rows: from the store through <code>assistant.py</code> to the card, never across the dashed line. Two things do cross it: your words and labels, to the planner, and a fixed list of slot names, to the writer. The red line is the edge a normal agent has and this one doesn't.</figcaption>
</figure>

A question goes through three steps.

**1. Plan.** The model gets your question, today's date, and your labels: your category and tag names, and your accounts as aliases like `A1 (checking)`. Never an account's name or number, never your bank's name. It answers with one JSON plan of one to three intents from a short, fixed list:

| Intent | What the engine does |
|---|---|
| `show_transactions` | Lists the rows a filter matches |
| `propose_tag` | Previews putting a tag on them |
| `propose_category` | Previews filing them under a category |
| `spending_summary` | Works out a category's, a tag's, or all spending, month by month |
| `clarify` | Asks which of two labels you meant |
| `decline` | Says no, in fixed words (investment advice, for one) |

The plan's schema has your labels baked in as the only values a category, tag or account can take. Ollama turns that schema into a grammar, so the model *can't* produce anything else. Claude gets it as structured outputs. Then Pydantic checks it again anyway. A plan that doesn't fit gets one repair, which quotes only the model's own reply back to it. Two misses and the card says so, in fixed words.

**2. Run.** The engine runs each intent in its own lane, on your laptop, in plain Python and SQL. The model isn't involved. It doesn't learn whether anything matched, how many rows there were, or what they cost.

**3. Show.** Every card is in BlitZave's words, not the model's. And each card shows how your question was read ("May 13 – May 27", "Tag: PNW"), because the planner is the one part that can misread you, and you should see when it did.

## The lane that writes

Showing and summarizing only read. Tagging and filing change your data, so that lane is the careful one. A change is only ever a **preview** first: a list with a checkbox per row.

<figure class="diagram">
<div class="fig-wrap">
<svg viewBox="0 0 900 486" role="img" aria-label="Sequence for tagging a trip. The model takes part in the first exchange only. The query, the preview, your Apply and the write all happen without it, and none of them is reported back to it.">
<defs>
<marker id="sn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-n"/></marker>
<marker id="sa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-a"/></marker>
<marker id="sm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-m"/></marker>
</defs>
<rect x="0" y="70" width="712" height="206" rx="10" class="d-band"/>
<rect x="0" y="284" width="712" height="140" rx="10" class="d-band"/>
<text x="12" y="88" class="d-z n">PREVIEW · NOTHING WRITTEN</text>
<text x="12" y="302" class="d-z n">APPLY</text>
<rect x="722" y="8" width="170" height="470" rx="14" class="d-zone"/>
<rect x="20" y="18" width="120" height="34" rx="9" class="d-box"/><text x="80" y="40" class="d-t" text-anchor="middle">You</text>
<rect x="280" y="18" width="120" height="34" rx="9" class="d-box"/><text x="340" y="40" class="d-t" text-anchor="middle">Engine</text>
<rect x="520" y="18" width="120" height="34" rx="9" class="d-box"/><text x="580" y="40" class="d-t" text-anchor="middle">store/ · SQLite</text>
<rect x="747" y="18" width="120" height="34" rx="9" class="d-box"/><text x="807" y="40" class="d-t" text-anchor="middle">Planner</text>
<line x1="80" y1="52" x2="80" y2="470" class="d-life"/>
<line x1="340" y1="52" x2="340" y2="470" class="d-life"/>
<line x1="580" y1="52" x2="580" y2="470" class="d-life"/>
<line x1="807" y1="52" x2="807" y2="470" class="d-life"/>
<line x1="80" y1="110" x2="338" y2="110" class="d-e" marker-end="url(#sn)"/>
<text x="210" y="103" class="d-l" text-anchor="middle">"tag May 13–27 as PNW"</text>
<line x1="340" y1="140" x2="805" y2="140" class="d-e m" marker-end="url(#sm)"/>
<text x="575" y="133" class="d-l m" text-anchor="middle">your words, labels, date</text>
<line x1="807" y1="170" x2="342" y2="170" class="d-e m" marker-end="url(#sm)"/>
<text x="575" y="163" class="d-l m" text-anchor="middle">propose_tag(PNW, May 13–27)</text>
<line x1="340" y1="204" x2="578" y2="204" class="d-e" marker-end="url(#sn)"/>
<text x="460" y="197" class="d-l" text-anchor="middle">query by filter, pairs left out</text>
<line x1="580" y1="232" x2="342" y2="232" class="d-e a" marker-end="url(#sa)"/>
<text x="460" y="225" class="d-l a" text-anchor="middle">12 rows</text>
<line x1="340" y1="262" x2="82" y2="262" class="d-e a" marker-end="url(#sa)"/>
<text x="210" y="255" class="d-l a" text-anchor="middle">preview + one-time id</text>
<line x1="80" y1="324" x2="338" y2="324" class="d-e" marker-end="url(#sn)"/>
<text x="210" y="317" class="d-l" text-anchor="middle">Apply: 10 checked rows</text>
<path d="M340,342 h34 v18 h-32" class="d-e" marker-end="url(#sn)"/>
<text x="382" y="355" class="d-l">rows ⊆ preview, else 422</text>
<line x1="340" y1="384" x2="578" y2="384" class="d-e" marker-end="url(#sn)"/>
<text x="460" y="377" class="d-l" text-anchor="middle">tag 10 rows, one commit</text>
<line x1="340" y1="412" x2="82" y2="412" class="d-e a" marker-end="url(#sa)"/>
<text x="210" y="405" class="d-l a" text-anchor="middle">"Tagged 10" + Undo</text>
<line x1="340" y1="452" x2="805" y2="452" class="d-e x"/>
<line x1="569" y1="446" x2="581" y2="458" class="d-x"/>
<line x1="569" y1="458" x2="581" y2="446" class="d-x"/>
<text x="575" y="440" class="d-l x" text-anchor="middle">rows, count, your Apply: never sent</text>
</svg>
</div>
<figcaption>The planner takes part once, at the top. The query, the preview, your Apply and the write all happen without it, and none of them is reported back.</figcaption>
</figure>

Nothing changes until you press Apply. Apply takes only rows the preview showed, once, in one commit. Undo takes back exactly what Apply wrote. Card payments and transfers are left out, so tagging a trip never catches the credit card bill.

## Numbers by reference

There's one place a model's words can reach the screen. A summary can have a model-written sentence, if you want it to sound less like a form.

The rule there: **the model never types a number.** It writes slots (`{label}`, `{average}`, `{latest_month}`, `{trend}`), and a function called `fill()` swaps in BlitZave's own figures. If the sentence has a typed digit, a `$`, a month name, a number in words, a scorekeeper word like "overspent", or a slot BlitZave didn't give, `fill()` throws it away and shows BlitZave's own sentence instead.

So the model writes:

> Over {months} months, {label} averaged {average}, and {latest_month} ran {trend}.

And you read:

> Over 6 months, Groceries averaged $110.00, and October ran higher than usual.

Every number in it came from deterministic code reading your own data. That was a rule in BlitZave long before any model was in it: *no invented numbers.*

## The leak I found in my own design

Here's the part I didn't get right the first time.

The writer was writing `{average}` instead of `$110.00`, but it was being *told* `{average} = $110.00`. Every slot went over with its value attached, so it could word the sentence sensibly. The original design review even drew it that way: an arrow into the writer labelled "aggregates".

Which means that with Claude as your model, every summary sent Anthropic a category's monthly totals, its average, its highest and lowest month, and whether last month was up. Not a transaction. But a result, and my own rule said a model never sees one.

I found it reviewing the feature before it merged. It was even in the disclosure screen ("for a summary, the monthly totals of what you asked about"), which is the honest version of a bug, but still a bug. The point of writing *by reference* is that the writer doesn't need the values. So now it doesn't get them.

The writer now gets the fixed list of slot names and what each one means. That's the same request whatever your numbers are, and there's a test that changes every figure and checks the request didn't move by a byte. All four new privacy tests fail on the old commit and pass on the fix.

That fix has a knock-on. A writer that can't see the numbers can only *guess* whether one is bigger than another. So `fill()` now also refuses any comparison the model typed ("more than", "higher", "as usual"), even a true one. How a month compares comes only from BlitZave's `{trend}`.

## Five guarantees, and the test behind each

A guarantee here is something that stays true even if the model is wrong, confused or manipulated.

<div class="table-scroll" markdown="1">

| Guarantee | Enforced by | Proven by |
|---|---|---|
| **G1. No row or result reaches a model** | The planner's prompt is built from labels only. The writer gets slot names only. | Every description, merchant and account name in the test data carries `CANARY`. No request contains it, an amount, or a month. The writer's request is identical after every figure changes. |
| **G2. Nothing flows back to the planner** | No conversation memory. A lane's result goes only to the API response. | The same question sends a byte-identical request after a show, a preview, an Apply and a summary. |
| **G3. No number or comparison a model typed is shown** | `fill()` | Typed figures, typed comparisons, unknown slots and scorekeeper words all fall back to the engine's sentence. |
| **G4. Nothing changes without your click** | Previews write nothing. Apply checks the rows, in one commit, with Undo. | Apply outside the preview is a 422. A second Apply is a 404. Undo restores. |
| **G5. "Remote" is decided by where the bytes go** | The endpoint's host. A remote model needs your OK after a screen listing what's sent. | An Ollama at `192.168.1.5` needs consent. The API key never reaches the database, its WAL, a backup or an export. |

</div>

## Threat model

<div class="table-scroll" markdown="1">

| Threat | What stops it | What's left |
|---|---|---|
| Injected text in a bank description | No model reads rows (G1) | Nothing, while merchant names stay out of what a model sees |
| Invented numbers | Slots, the digit ban, the engine's own sentence (G3) | None in figures |
| A misread question | Your labels as the only valid values, chips on every card, Apply on every write | You have to glance at the chips |
| An over-broad write | Preview, Apply only on previewed rows, Undo (G4) | Small while writes are tags and categories |
| Data leaving the laptop | Local by default; the host decides; a screen lists exactly what's sent; accounts go as aliases (G5) | Your question and your label names, which you chose to send |
| The API key leaking | Keychain only; never in the database, a backup or an export | None known |
| Runaway cost | No loop: one plan, at most one repair, and one sentence per summary | A fixed ceiling per question |
| A web page posting to the local API | JSON-only bodies (pinned by a test), no CORS, one-time preview ids | A page could start a sync, which is harmless |
| DNS rebinding | Any `Host` but `127.0.0.1` or `localhost` gets a 400 | None known |

</div>

## What it gives up

This design is deliberately less capable than an agent that reads its own results. Goose's ceiling is the engine's ceiling: it can only say what reviewed code can compute.

- **No open-ended "why".** Goose can show you, tag, file and add up. Ask "why did July run high?" and you get July's rows, largest first, not a theory, because nothing that thinks ever reads them.
- **No memory.** Each question stands alone. "Now tag those" doesn't know what "those" were.
- **A short list of things it can do.** Six intents. A new ability arrives as a reviewed engine function with an intent in front of it, never as more context for the model.

Those are real limits. They're also the reason I'm comfortable letting a model anywhere near your money.

## If you're building one

- **Ask what the model reads, not what it can do.** In a tool loop, it reads everything the tools return.
- **Let the model choose, and let your code act.** A plan from a fixed list is easy to check. A free-form tool loop isn't.
- **Pass numbers by reference.** If the model never sees the value, it can't leak it or get it wrong.
- **Print the plan back to the user.** The model will misread people. Make it obvious when it does.

How well the planner actually plans, on a laptop model versus Claude, is its own question. That one gets its own post, about the eval.
