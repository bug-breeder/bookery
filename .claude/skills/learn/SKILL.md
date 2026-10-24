---
name: learn
description: Turn a bookery chapter (PDF or EPUB) into an interactive, figure-rich lesson published as a Claude Artifact, which teaches the chapter well enough that the reader doesn't need the book; for studying alone or presenting. Use when the user wants to learn, study, teach, present or "make an interactive lesson/slides/artifact" from a chapter of a book processed by bookery, e.g. "/learn behavioral-game-theory 2", "make chapter 5 into an interactive lesson".
---

# Learn: a chapter into a lesson that replaces it

The reader will not read the book. The lesson is all they get, so it has to
**teach**. Every idea it uses is explained in plain words, with a concrete
example, before the reader is asked anything about it. A lesson is
*condensed*: it drops repetition, detours and citations. It is never
*compressed*: no term, symbol, game or theory is used before it is taught.
It still has to be quicker than the chapter, show the book's own figures
and figures drawn for the chapter, and make the reader *use* what it
taught. One lesson works two ways: **Study** (scroll at your own pace, the
book's passages one click away) and **Present** (a deck with builds and
speaker notes).

Write it the way a great teacher plans a class. Know where the student
starts (from nothing). Lay down each idea before building on it. Show one
case before the rule. Ask only what the student can now work out. Read the
lesson back as the student would.

You write data, not a page: `lessons/chNN/lesson.json` in the book's root
(`$BOOKERY_ROOT`). A shared runtime renders it, and gates check it:
- every number must be in the chapter;
- every quotation must be verbatim;
- every slide cites its blocks;
- every section is taught or skipped with a reason;
- no slide is a wall of text;
- no slide uses a term before the slide that teaches it.

A cold reader then checks what the gates can't.

## 0. Setup

- **Book root:** `export BOOKERY_ROOT=<path to the book's folder>` (it has
  `work/reconcile/chNN.json`). For an EPUB that hasn't been ingested yet,
  run `bookery ingest --epub <file>` first.
- **Language:** lesson content is **English** unless the user asks for
  another language. Set `"language"` in lesson.json (BCP-47, e.g. `"en"`,
  `"vi"`). Quotes always stay in the book's language, verbatim.

## 1. Read the chapter as its teacher

```bash
uv run bookery learn pack --chapter N
```

This writes `work/learn/chNN/pack.md`: the chapter as one Markdown file,
with every block tagged `[b0034 p48]` (block id, printed page) and every
exhibit listed with its absolute image path.

Read the whole pack. **Look at the figures you might use** (Read the image
paths): you can only pin notes on a figure you have seen, and a figure is
often the fastest way to teach its section.

As you read, list:
- every term, symbol, game, model, measure and named theory the chapter
  relies on, including the ones it assumes you already know;
- what the chapter argues *against* (the standard prediction, the usual
  mistake).

## 2. Plan the teaching before writing a slide

Decide, in this order:

1. **The 3-5 objectives.** What can the reader *do* after this lesson that
   they couldn't before? Write "Predict how responders treat a 20 percent
   offer", not "Learn about ultimatum games".
2. **The teaching path.** Take the list from step 1. Each item the reader
   needs becomes a glossary entry: a plain definition (no undefined words),
   one concrete example, and the slide that teaches it (`teaches`). Order
   them so each comes after what it depends on. For example: proposer and
   responder → ultimatum game → what self-interest predicts → dictator game
   (the ultimatum game minus the veto) → trust game (a dictator game with an
   investment first).

   Terms the book assumes count too: if a reader without the field would
   stumble on "utility" or "subgame perfect", teach it.
3. **The baseline.** For each finding, what does standard theory or common
   sense predict? Teach that first, so the data surprises the reader rather
   than just informing them. A puzzle needs the expectation it breaks.
4. **What to keep.** Keep every idea the book wants a reader to leave with:
   the findings, the mechanisms that explain them, and the one or two
   examples that make them stick.

   Cut repetition, extra studies that make the same point, robustness
   detail and literature tours. Don't cut ideas.

   Every top-level section is either taught or listed in `skipped` with a
   reason the reader sees ("Technical robustness checks; the takeaway is on
   slide X").
5. **The arc for each idea.** It usually runs 3-6 slides, in this order:
   - **Set up:** the question, and why it matters.
   - **Teach:** the concept, with its definition card and a worked case,
     usually drawn.
   - **Try:** the reader predicts or applies it, using only what has been
     taught.
   - **Show:** the book's evidence, with a line on how to read it.
   - **Explain:** why it happens, and what it answers.
   - **Check:** later, a question that mixes ideas.

   Drop a beat only when it adds nothing. Never drop **Teach**.
6. **The best picture for each idea.** Ask what figure would teach it
   fastest, then **build it**: a `custom` widget (SVG/HTML written for this
   chapter, in `lessons/chNN/widgets/`), not the nearest built-in type.
   - Use `chart` or `table` only when it really is the best fit.
   - Use the book's own figure when it already says it well, with pinned
     notes.
   - Several screenshots of one evolving table become one figure that
     moves (columns filling in, rows re-ranking).
   - Aim for at least 4 of the book's exhibits. Numbers you read off an
     image go in `transcribed`.

   A game, a model or a rule is usually best taught as a picture of how it
   works: who moves, what each choice pays.

## 3. Write lesson.json

Read `authoring.md` (next to this file) for the full vocabulary: slide
parts, the glossary and `teaches`, the ten widget types, figure notes,
`beyond`, custom widgets, and examples. The rules that matter most:

- **Teach before you ask.** A slide may use a term only if that slide or
  an earlier one teaches it (`"teaches": ["Dictator game"]`). That slide
  shows the term's definition and example as a card; later slides
  underline it with a hover reminder. The `teaching` gate fails any use
  that comes earlier, in text, a question, an option, a caption or a
  figure note. Declare every term the reader might not know.

  Undeclared jargon slips past the gate, and the cold read (step 5) is
  there to catch it.
- **Plain words.** Write for a smart 16-year-old who has never studied the
  field. A sentence that only makes sense if you've read the book is not
  finished.
- **Concrete first.**
  - Work one case with numbers ($10, offer $3) before the general rule.
  - Work it before a formula.
  - Define every symbol where it first appears: "(8,2): the proposer gets
    8, the responder 2"; "\(\alpha\): how much she minds having less".
  - Every number says what it counts: "26 percent *of the pie*".
- **Each slide builds on the last.** When the link isn't obvious, open
  with it: "So fear of rejection can't be the whole story. Take the veto
  away:".
- **Read figures for the reader.**
  - The title states what the figure shows.
  - One line says how to read it: what one bar is, what the axes count.
  - Pinned notes point at the part that matters.
- **Names earn their place.** Name a person or study only when the reader
  needs the name, and say who they are. A kicker names the topic
  ("Raising the stakes"), never a citation ("List and Cherry").
- **The slide carries the lesson.** `deeper` adds detail for the curious.
  `notes` are for a presenter, and a study reader never sees them, so an
  explanation that lives only there is missing.
- **One idea per slide, one main part** (a figure, a widget, stats, a
  quote or columns), plus a short `text`, definition cards, or a few
  `points`.
- **Short.**
  - Titles ≤ 12 words; text ≤ 50; ≤ 5 points of ≤ 18 words.
  - ≤ 90 visible words per slide.
  - A definition ≤ 40 words; its example ≤ 30.

  Teaching a hard idea takes more slides, not longer ones.
- **Grounded.**
  - Every content slide lists `sources`: the block ids it condenses.
  - Every number on a slide must appear in the chapter. A proportion may
    appear as its percent: 0.45 ↔ 45.
  - Quotes are verbatim, with `...` allowed between verbatim pieces.
- **Your own knowledge.** Definitions of background terms may be in your
  own plain words. Outside *facts* (findings, history, numbers) go only in
  `beyond`, which renders as "Beyond the book".
- **Interactions earn their place.** Keep one only if the reader has to
  use what the lesson just taught and can't guess the answer from the
  wording.

  Cut:
  - a question about anything not yet taught;
  - guessing the author's opinion or an arbitrary threshold;
  - a quiz whose answer was on the previous slide;
  - matching by keyword;
  - steps that only add clicks;
  - sliders that only add numbers up.

  Prefer:
  - predictions the reader can work out from what was taught;
  - explorables on the chapter's own formula;
  - tools they'll keep using.

  There is no quota (the gate wants one recall check). Every quiz option
  gets a `why`.
- **Visual.** At least 40% of content slides show a figure (yours or the
  book's), chart, stats, table, explorable or prediction. Never put 3
  text-only slides in a row.

## 4. Verify, fix, repeat

```bash
uv run bookery learn verify --chapter N
```

This runs every gate, builds `work/learn/chNN/index.html` and
`transcript.md`, and self-tests the page in headless Chrome. The self-test
catches:
- script errors;
- slides overflowing the 1600×900 stage;
- broken images;
- recall checks that give no feedback;
- horizontal scroll on a phone.

Fix what it reports and re-run until it prints PASS. Don't loosen a gate
to pass it:
- A number that fails `numbers` is wrong, or needs to cite the block it
  came from, or belongs in `beyond`.
- A term that fails `teaching` needs teaching earlier. Don't rename it to
  dodge the match.

## 5. Cold read: test it on someone who never saw the book

The gates prove that declared terms come in order. They can't see a term
you never declared, a symbol nobody explained, or a question with nothing
to reason from. A reader can.

`transcript.md` is the whole lesson as a study reader meets it, in plain
text:
- slide titles, text and definition cards;
- figure captions and pinned notes;
- chart axes and values;
- the text each custom widget puts on the page, read off it by `verify`:
  form fields as `[value]` (or `[ ]` empty), and a widget with `onNext`
  steps stepped through, each step's new text under "after the reader
  steps forward";
- each question, and what it reveals.

Give it to a **fresh agent** (Agent tool, general-purpose). Pass only the
transcript's path, never the book, the pack or your plan, with this brief:

> You are a curious student who has never read this book or studied its
> field. Read `<path>/transcript.md` from top to bottom: it is a whole
> lesson, slide by slide, exactly as you would see it. Before you read
> what a question reveals, answer it yourself and note whether you could.
> Report by slide number:
> 1. every word, symbol, name or abbreviation you met before it was
>    explained;
> 2. every question you couldn't reason about from what the lesson had
>    taught by then, and what was missing;
> 3. every claim or number whose meaning or point you couldn't tell;
> 4. anything that seemed to contradict an earlier slide.
>
> Be strict: if you would have had to guess, say so. Don't rewrite the
> lesson. Just say where you got lost and why, then say whether you could
> now explain the lesson's main ideas to a friend.

Fix every item. Usually that means teaching the missing piece on or
before the slide, adding a worked case, reordering, or cutting. Then
re-verify, and run a **new** cold reader, never the same one, since it has
already learned the lesson. Repeat until a cold reader reports nothing
that blocks understanding.

## 6. Look at it

```bash
uv run bookery learn shot --chapter N                     # study, top
uv run bookery learn shot --chapter N --present --slide ID
uv run bookery learn shot --chapter N --phone --dark
```

Read the PNGs. A present shot of one slide shows it after all its builds,
so every figure note is visible. Check that:
- each pin sits beside what it describes without covering it. x and y are
  fractions of the image, and a pin is about 32px across, so on a table put
  it in the empty part of the cell, not on the number;
- charts are legible;
- definition cards fit;
- nothing is cramped.

## 7. Publish

Read `work/learn/chNN/publish.json`, then publish `index.html` with the
Artifact tool: `file_path` from that file, its `description`, and `icon:
"book"`. Then record the URL so a later rebuild updates the same artifact:

```bash
uv run bookery learn record --chapter N --url <artifact url>
```

To update an already-published lesson, publish again with that `url`.

Tell the user, once: the artifact is private to them, and it contains
excerpts of a copyrighted book, so they should share it only where that is
fine.

## Links into a lesson

- `<url>#present` opens the deck.
- `#present-<slide-id>` opens the deck at a slide.
- `#<slide-id>` scrolls study mode to a slide.
