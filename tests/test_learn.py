"""`bookery learn`: schema, the static gates, and the build payload.

Everything runs on a small synthetic book written into tmp_path, so the
tests need neither a real book nor a browser.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from PIL import Image

from learn import build, gates, runner, schema, transcript
from learn.source import ChapterSource

PARA_OFFERS = ("In most ultimatum games the average offer is 40 percent of the pie, and offers "
               "below 20 percent are rejected about half the time.")
PARA_TRUST = ("Trust is a bet on another person's reciprocity, and in the trust game investors "
              "send about half of their endowment.")


def _block(n: int, type_: str, page: int, text: str = "", **kw) -> dict:
    return {"id": f"ch03-b{n:04d}", "type": type_, "page": page, "text": text, **kw}


@pytest.fixture
def book(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "reconcile").mkdir(parents=True)
    blocks = [
        _block(1, "heading", 53, "Fairness", level=1),
        _block(2, "heading", 53, "3.1 Ultimatum Games", level=2, label="3.1"),
        _block(3, "paragraph", 54, PARA_OFFERS),
        _block(4, "figure", 55, caption="Figure 3.1 Distribution of offers.", label="3.1",
               label_kind="figure", asset="img/ch03/fig-3-1.png"),
        _block(5, "heading", 56, "3.2 Trust", level=2, label="3.2"),
        _block(6, "paragraph", 57, PARA_TRUST),
    ]
    (work / "reconcile" / "ch03.json").write_text(json.dumps({"chapter": 3, "title": "Fairness", "blocks": blocks}))
    (work / "triage.json").write_text(json.dumps({
        "source": {"title": "A Synthetic Book", "author": "A. Author", "format": "pdf"},
        "boundary_map": {"chapters": [
            {"number": 3, "title": "Fairness", "printed_page": 41, "pdf_page": 53, "pdf_page_end": 57},
        ]},
    }))
    img = tmp_path / "site" / "static" / "img" / "ch03" / "fig-3-1.png"
    img.parent.mkdir(parents=True)
    Image.new("RGB", (60, 40), (10, 120, 200)).save(img)
    (work / "assets.json").write_text(json.dumps({"ch03": [
        {"block": "ch03-b0004", "file": "img/ch03/fig-3-1.png", "width": 60, "height": 40, "label": "3.1"},
    ]}))
    return tmp_path


@pytest.fixture
def src(book: Path) -> ChapterSource:
    return ChapterSource(3, book)


LESSON = {
    "schema": 1,
    "title": "Fairness",
    "objectives": ["Predict ultimatum offers", "Explain trust"],
    "minutes": 10,
    "sections": [{
        "title": "Ultimatum",
        "slides": [
            {"id": "offers", "title": "Offers bunch near 40 percent", "figure": {"ref": "fig 3.1"},
             "text": "The average offer is 40 percent of the pie.", "sources": ["b0003"]},
            {"id": "check", "title": "Check yourself", "sources": ["b0003"],
             "widget": {"type": "quiz", "question": "How often are low offers rejected?",
                        "options": [
                            {"text": "About half the time", "correct": True, "why": "Offers below 20 percent: about half."},
                            {"text": "75 percent of the time", "why": "Too high."},
                        ]}},
            {"id": "trust", "title": "Trust is a bet", "sources": ["b0006"],
             "quote": {"text": "Trust is a bet … in the trust game investors send about half", "block": "b0006"}},
        ],
    }],
}


def lesson(**changes) -> dict:
    out = copy.deepcopy(LESSON)
    out.update(changes)
    return out


def slides(les: dict) -> list[dict]:
    return les["sections"][0]["slides"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_a_well_formed_lesson_has_no_schema_problems(src, tmp_path):
    assert schema.validate(lesson(), tmp_path) == []
    assert gates.gate_schema(lesson(), src, tmp_path).ok


def test_schema_rejects_two_main_parts_and_a_bad_quiz(tmp_path):
    les = lesson()
    slides(les)[0]["stats"] = [{"value": "40%", "label": "average offer"}]
    slides(les)[1]["widget"]["options"][1]["correct"] = True
    problems = [p.message for p in schema.validate(les, tmp_path)]
    assert any("one main part per slide" in m for m in problems)
    assert any("exactly one option" in m for m in problems)


def test_schema_gate_catches_a_figure_that_does_not_exist(src, tmp_path):
    les = lesson()
    slides(les)[0]["figure"] = {"ref": "fig 3.9"}
    gate = gates.gate_schema(les, src, tmp_path)
    assert not gate.ok
    assert "matches no exhibit" in gate.detail["problems"][0]["message"]


# ---------------------------------------------------------------------------
# Numbers and quotes
# ---------------------------------------------------------------------------


def test_numbers_accept_percent_twins_and_ignore_distractors(src):
    les = lesson()
    # "0.4" is the proportion twin of the book's "40 percent"; the wrong
    # option's "75" is there to be wrong, so it isn't a claim.
    slides(les)[0]["text"] = "Offers average 0.4 of the pie."
    gate = gates.gate_numbers(les, src)
    assert gate.ok, gate.detail


def test_numbers_flag_an_invented_figure(src):
    les = lesson()
    slides(les)[0]["text"] = "Offers average 45 percent of the pie."
    gate = gates.gate_numbers(les, src)
    assert not gate.ok
    assert gate.detail["invented"][0]["value"] == "45"


def test_a_distractor_explanation_is_still_a_claim(src):
    les = lesson()
    slides(les)[1]["widget"]["options"][1]["why"] = "That would be 75 percent; the book says half."
    gate = gates.gate_numbers(les, src)
    assert not gate.ok
    assert gate.detail["invented"][0]["field"].endswith("why")


def test_quotes_may_elide_with_an_ellipsis_but_not_alter(src):
    assert gates.gate_quotes(lesson(), src).ok
    les = lesson()
    slides(les)[2]["quote"]["text"] = "Trust is a wager on another person's reciprocity"
    gate = gates.gate_quotes(les, src)
    assert not gate.ok
    assert gate.detail["failures"][0]["slide"] == "trust"


# ---------------------------------------------------------------------------
# Coverage and density
# ---------------------------------------------------------------------------


def test_coverage_needs_every_section_taught_or_skipped(src):
    les = lesson()
    slides(les)[2]["sources"] = ["b0003"]
    slides(les)[2]["quote"]["block"] = "b0003"
    gate = gates.gate_coverage(les, src)
    assert not gate.ok and gate.detail["missing"] == ["3.2"]
    les["skipped"] = [{"section": "3.2", "reason": "Covered in chapter 4."}]
    assert gates.gate_coverage(les, src).ok


def test_coverage_counts_a_figure_as_teaching_its_section(src):
    gate = gates.gate_coverage(lesson(), src)
    taught = {r["section"]: r["slides"] for r in gate.detail["sections"]}
    assert "offers" in taught["3.1"]
    assert gate.detail["exhibits_used"] == ["fig 3.1"]


def test_density_flags_a_wall_of_text_and_too_few_checks(src):
    les = lesson()
    slides(les)[0]["text"] = " ".join(["word"] * 80)
    slides(les).pop(1)      # the only quiz
    gate = gates.gate_density(les, src)
    assert not gate.ok
    assert any(o["slide"] == "offers" and o["field"] == "text" for o in gate.detail["over_budget"])
    assert any("recall checks" in p for p in gate.detail["lesson_problems"])


def test_density_wants_one_good_check_not_a_quota(src):
    # One quiz in three slides used to fail "one interaction every 5 slides"
    # once a lesson grew; now a single check is enough.
    les = lesson()
    gate = gates.gate_density(les, src)
    assert not any("recall" in p or "interactive" in p for p in gate.detail["lesson_problems"])


def test_a_static_custom_widget_is_visual_but_not_interactive(src, tmp_path):
    (tmp_path / "stages.js").write_text("Bookery.widget('stages', function () {});")
    les = lesson()
    slides(les)[2] = {"id": "stages", "title": "Four stages", "sources": ["b0006"],
                      "widget": {"type": "custom", "module": "stages", "caption": "The order.", "static": True}}
    assert schema.validate(les, tmp_path) == []
    assert not schema.is_active(slides(les)[2])
    assert schema.is_active(slides(les)[1])
    gate = gates.gate_density(les, src)
    assert gate.detail["interactive"] == 1
    assert gates._is_visual(slides(les)[2])
    slides(les)[2]["widget"]["static"] = "yes"
    assert any("static" in p.message for p in schema.validate(les, tmp_path))


def test_a_custom_widget_may_keep_the_stage_width(tmp_path):
    (tmp_path / "stages.js").write_text("Bookery.widget('stages', function () {});")
    les = lesson()
    slides(les)[2] = {"id": "stages", "title": "Four stages", "sources": ["b0006"],
                      "widget": {"type": "custom", "module": "stages", "caption": "The order.", "wide": True}}
    assert schema.validate(les, tmp_path) == []
    slides(les)[2]["widget"]["wide"] = "full"
    assert any("wide" in p.message for p in schema.validate(les, tmp_path))


def _sheet(transcribed=None) -> dict:
    les = lesson()
    widget = {"type": "custom", "module": "sheet", "caption": "Scores, column by column.",
              "props": {"rows": [{"name": "Founders", "scores": [5, 4, 19]}]}}
    if transcribed is not None:
        widget["transcribed"] = transcribed
    slides(les)[2] = {"id": "sheet", "title": "The author's sheet", "sources": ["b0006"], "widget": widget}
    return les


def test_transcribed_numbers_go_to_review_with_their_images(src, tmp_path):
    # 19 is nowhere in the chapter's text: without `transcribed` it's invented.
    gate = gates.gate_numbers(_sheet(), src)
    assert not gate.ok and "19" in {i["value"] for i in gate.detail["invented"]}
    les = _sheet(["fig 3.1"])
    gate = gates.gate_numbers(les, src)
    assert gate.ok, gate.detail
    assert gate.detail["transcribed_for_review"] == [{"slide": "sheet", "from": ["fig 3.1"], "values": ["4", "5", "19"]}]
    # The images count as shown (coverage, exhibits) and must exist.
    assert gates.figure_refs(slides(les)[2]) == ["fig 3.1"]
    assert gates.gate_coverage(les, src).detail["exhibits_used"] == ["fig 3.1"]
    (tmp_path / "sheet.js").write_text("Bookery.widget('sheet', function () {});")
    assert gates.gate_schema(les, src, tmp_path).ok
    assert not gates.gate_schema(_sheet(["fig 3.9"]), src, tmp_path).ok


def test_transcribed_needs_a_data_widget(tmp_path):
    les = lesson()
    slides(les)[1]["widget"]["transcribed"] = ["fig 3.1"]
    assert any("transcribed only applies" in p.message for p in schema.validate(les, tmp_path))


def test_figure_refs_include_figures_inside_steps():
    slide = {"figure": {"ref": "fig 3.1"}, "widget": {"type": "steps", "steps": [
        {"title": "a", "figure": {"ref": "fig 3.2"}}, {"title": "b"}, "not a step"]}}
    assert gates.figure_refs(slide) == ["fig 3.1", "fig 3.2"]


# ---------------------------------------------------------------------------
# Sources and the build payload
# ---------------------------------------------------------------------------


def test_printed_pages_follow_the_triage_offset(src):
    assert src.page_offset == 12
    assert src.printed_page(src.by_id["ch03-b0003"]) == 42


def test_unnumbered_headings_fall_back_to_the_second_level(tmp_path):
    work = tmp_path / "work" / "reconcile"
    work.mkdir(parents=True)
    blocks = [
        _block(1, "heading", 0, "Optimal Stopping", level=1),
        _block(2, "paragraph", 0, "Opening words."),
        _block(3, "heading", 0, "The Secretary Problem", level=2),
        _block(4, "paragraph", 0, "Look then leap."),
        _block(5, "heading", 0, "When to Park", level=2),
    ]
    (work / "ch03.json").write_text(json.dumps({"chapter": 3, "title": "Optimal Stopping", "blocks": blocks}))
    s = ChapterSource(3, tmp_path)
    assert [(x.label, x.title) for x in s.sections] == [("s1", "The Secretary Problem"), ("s2", "When to Park")]
    assert s.printed_page(blocks[1]) is None     # no page markers, no pages


def test_payload_embeds_figures_sources_and_resolves_quote_blocks(src):
    data, n_figures = build.payload(lesson(), src)
    assert n_figures == 1
    fig = data["figures"]["fig-3-1"]
    assert fig["src"].startswith("data:image/webp;base64,")
    assert (fig["label"], fig["caption"], fig["page"]) == ("Figure 3.1", "Distribution of offers.", 43)
    first = slides(data["lesson"])[0]
    assert first["figure"]["key"] == "fig-3-1"
    # The figure's block travels as a source alongside the cited paragraph.
    assert first["sources"] == ["ch03-b0003", "ch03-b0004"]
    assert data["sources"]["ch03-b0003"]["page"] == 42
    assert data["sources"]["ch03-b0003"]["section"] == "3.1 Ultimatum Games"
    # The runtime looks quote pages up by full block id.
    assert slides(data["lesson"])[2]["quote"]["block"] == "ch03-b0006"
    assert data["chapter"]["pages"] == "41-45"
    assert [s["status"] for s in data["sections"]] == ["taught", "taught"]


def test_the_page_stands_alone_without_the_artifact_skeleton(src, tmp_path):
    # Opened as a downloaded file, nothing declares the encoding or hides
    # [hidden] elements unless the page does it itself.
    data, _ = build.payload(lesson(), src)
    page = build.render_page(data, lesson(), tmp_path)
    head = page.split("<script", 1)[0]
    assert head.startswith('<meta charset="utf-8">')
    assert 'name="viewport"' in head
    assert "[hidden]" in head


def test_centred_present_slides_spill_where_the_overflow_check_sees():
    # Plain centring pushes half of a too-tall slide above the stage, out of
    # scrollHeight's reach, and a statement's text then runs under its pinned
    # kicker while the runtime gate still passes.
    css = (build.RUNTIME / "app.css").read_text()
    rules = {}
    for kind in ("is-section", "is-statement"):
        sel = f".bk-canvas .bk-slide.{kind} {{"
        rules[kind] = css.split(sel, 1)[1].split("}", 1)[0]
        assert "justify-content: safe center" in rules[kind]
    top = int(css.split(".bk-slide.is-statement .bk-card-top {", 1)[1].split("top:", 1)[1].split("px", 1)[0])
    assert int(rules["is-statement"].split("padding-top:", 1)[1].split("px", 1)[0]) > top + 40


def test_a_shot_rebuilds_the_page_it_shoots(book, monkeypatch):
    # A preview from before the last edit would show a page that no longer exists.
    monkeypatch.setattr(runner.config, "ROOT", book)
    monkeypatch.setattr(runner, "find_chrome", lambda: "chrome")
    shot_urls = []
    monkeypatch.setattr(runner, "_chrome", lambda chrome, url, size, extra: shot_urls.append(url))
    (book / "lessons" / "ch03").mkdir(parents=True)
    (book / "lessons" / "ch03" / "lesson.json").write_text(json.dumps(lesson(title="Fairness, edited")))
    stale = book / "work" / "learn" / "ch03" / "preview.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("<p>Fairness</p>")
    # verify's transcript holds what the widgets drew, which a plain build can't know.
    script = stale.parent / "transcript.md"
    script.write_text("  it shows:\n    what the page drew\n")
    runner.shot(3, None, present=True, dark=False, phone=False, out=book / "s.png")
    assert "Fairness, edited" in stale.read_text()
    assert shot_urls == [stale.as_uri() + "#present"]
    assert script.read_text() == "  it shows:\n    what the page drew\n"


def test_payload_embeds_transcribed_images_as_keys(src):
    les = _sheet(["fig 3.1"])
    slides(les)[0].pop("figure")
    data, n_figures = build.payload(les, src)
    assert n_figures == 1
    assert slides(data["lesson"])[2]["widget"]["transcribed"] == ["fig-3-1"]
    assert "fig-3-1" in data["figures"]


# ---------------------------------------------------------------------------
# Teaching order and the cold-read transcript
# ---------------------------------------------------------------------------

ULTIMATUM = {"term": "Ultimatum game", "def": "One player proposes a split; the other accepts it or both get nothing.",
             "example": "Of $10, you offer $3; if I say no, we both get $0.", "aliases": ["ultimatum"],
             "sources": ["b0003"]}


def _taught(**slide_changes) -> dict:
    les = lesson(glossary=[copy.deepcopy(ULTIMATUM)])
    slides(les)[0]["teaches"] = ["Ultimatum game"]
    for sid, changes in slide_changes.items():
        next(s for s in slides(les) if s["id"] == sid).update(changes)
    return les


def test_teaching_passes_when_a_term_is_taught_before_it_is_used():
    les = _taught(check={"title": "Check: ultimatum games"})
    gate = gates.gate_teaching(les)
    assert gate.ok, gate.detail
    assert gate.detail["taught"] == {"Ultimatum game": "offers"}


def test_a_question_cannot_use_a_term_before_the_slide_that_teaches_it():
    les = _taught()
    slides(les)[0].pop("teaches")
    slides(les)[2]["teaches"] = ["Ultimatum game"]
    # The quiz on slide 2 asks about it; the slide that teaches it is 3.
    slides(les)[1]["widget"]["question"] = "In an ultimatum game, how often are low offers rejected?"
    gate = gates.gate_teaching(les)
    assert not gate.ok
    early = gate.detail["used_before_taught"]
    # The section's own title ("Ultimatum") names it too, above slide 1.
    assert [(e["slide"], e["field"], e["taught_on"]) for e in early] == [
        ("offers", "section title", "trust"), ("check", "widget.question", "trust")]


def test_aliases_plurals_and_links_count_as_uses():
    for text in ("Ultimatums are cheap to run.", "Two [[Ultimatum game|games]] later.", "ULTIMATUM GAMES differ."):
        les = _taught()
        slides(les)[0].pop("teaches")
        slides(les)[2]["teaches"] = ["Ultimatum game"]
        slides(les)[0]["text"] = text
        assert not gates.gate_teaching(les).ok, text


def test_every_glossary_term_is_taught_once_and_links_name_real_terms():
    les = _taught()
    slides(les)[0].pop("teaches")
    slides(les)[1]["text"] = "See [[Dictator game]]."
    gate = gates.gate_teaching(les)
    assert gate.detail["never_taught"] == ["Ultimatum game"]
    assert gate.detail["unknown_links"] == [{"slide": "check", "term": "Dictator game"}]
    les = _taught(trust={"teaches": ["Ultimatum game"]})
    assert gates.gate_teaching(les).detail["taught_twice"][0]["first"] == "offers"


def test_a_glossary_term_needs_an_example(tmp_path):
    les = _taught()
    les["glossary"][0].pop("example")
    assert any("example" in p.message for p in schema.validate(les, tmp_path))


def test_the_transcript_shows_what_a_reader_sees_and_not_speaker_notes(src):
    les = _taught()
    slides(les)[0]["notes"] = "Presenter only: pause here."
    data, _ = build.payload(les, src)
    text = transcript.render(data)
    assert "NEW TERM **Ultimatum game**" in text and "Example: Of $10" in text
    assert "FIGURE from the book (Figure 3.1): Distribution of offers." in text
    assert "QUIZ: How often are low offers rejected?" in text
    assert "A is right: Offers below 20 percent: about half." in text
    assert "Presenter only" not in text


def _widget_data(widget: dict, text: str = "") -> dict:
    les = lesson()
    slides(les)[2] = {"id": "w", "title": "A widget", "text": text, "sources": ["b0006"], "widget": widget}
    return {"lesson": les, "chapter": {"number": 3, "title": "Fairness"}, "book": {}, "figures": {}}


def test_a_widgets_ids_stay_out_of_its_transcript():
    # A cold reader took "alts | diff" for words on the page.
    rows = [{"key": "alts", "label": "Competitive alternatives"}, {"id": "diff", "label": "Differentiators"}]
    data = _widget_data({"type": "custom", "module": "checkup", "caption": "Check it.", "props": {"rows": rows}})
    for shown in (None, "Check it"):
        text = transcript.render(data, {"w": shown} if shown else None)
        assert "Competitive alternatives | Differentiators" in text
        assert "alts" not in text and "diff " not in text


def test_the_transcript_steps_through_a_widget_and_shows_its_fields():
    # What the runtime read off the page: the start, then each step's new lines.
    data = _widget_data({"type": "custom", "module": "sheet", "caption": "Step through it.", "static": True})
    shown = {"w": "Awareness\n[Founders]\n[ ]\n[next]\nRanked by affinity: Founders 5."}
    text = transcript.render(data, shown)
    assert "    [Founders]\n    [ ]\n  after the reader steps forward, it adds:\n    Ranked by affinity: Founders 5." in text
    assert "[next]" not in text


LABEL_WIDGET = """
Bookery.widget("label", function (el, props, api) {
  var s = api.svg, art = s("svg", { viewBox: "0 0 200 80", width: 200, height: 80 });
  var t = s("text", { x: 10, y: 20 });
  ["A place to write", "and read stories"].forEach(function (line, j) {
    var sp = s("tspan", { x: 10, dy: j ? "1.2em" : "0" });
    sp.textContent = line;
    t.appendChild(sp);
  });
  var tv = s("text", { x: 120, y: 20 });
  tv.textContent = "TV";
  art.appendChild(t);
  art.appendChild(tv);
  el.appendChild(art);
});
"""


@pytest.mark.skipif(runner.find_chrome() is None, reason="needs Chrome")
def test_a_drawings_labels_reach_the_transcript_line_by_line(book):
    # innerText runs SVG text together: "A place to writeand read stories TV".
    les = lesson()
    slides(les)[2] = {"id": "w", "title": "A drawing", "sources": ["b0006"],
                      "widget": {"type": "custom", "module": "label", "caption": "Read it.", "static": True}}
    (book / "lessons" / "ch03" / "widgets").mkdir(parents=True)
    (book / "lessons" / "ch03" / "lesson.json").write_text(json.dumps(les))
    (book / "lessons" / "ch03" / "widgets" / "label.js").write_text(LABEL_WIDGET)
    built = build.build(3, book)
    shown = runner.gate_runtime(built.preview).detail["shown"]
    assert shown["w"] == "A place to write\nand read stories\nTV"


@pytest.mark.parametrize("words", [40, 400])
def test_the_gallery_description_keeps_the_book_title_whole(book, words):
    # A 300-character cut once ended a card at "...chapter 7 of Obviously Awesome:".
    subtitle = " ".join(["positioning"] * words)
    (book / "lessons" / "ch03").mkdir(parents=True)
    (book / "lessons" / "ch03" / "lesson.json").write_text(json.dumps(lesson(subtitle=subtitle)))
    description = json.loads(build.build(3, book).publish.read_text())["description"]
    assert description.endswith(" (an interactive lesson on chapter 3 of A Synthetic Book).")
    assert len(description) <= build.DESCRIPTION_MAX
    assert description.startswith(subtitle if words == 40 else "positioning positioning")


def test_a_record_the_reader_has_not_seen_whole_goes_whole():
    props = {"offers": [{"give": 4, "rejected": 37}, {"give": 5, "rejected": 3}]}
    data = _widget_data({"type": "custom", "module": "responder", "caption": "Decide.", "props": props})
    text = transcript.render(data, {"w": "Offer 1 of 2\nYou $4\nrejected: 37"})
    # The first offer is all on the page; the second needs its "give" to say which offer it is.
    assert "it can also show (from the widget's data): give: 5; rejected: 3" in text


def test_a_record_keeps_only_its_name_and_what_is_new():
    props = {"columns": [{"name": "Affinity", "park": 2, "reason": "Affinity 2 or less"}]}
    data = _widget_data({"type": "custom", "module": "sheet", "caption": "Score.", "props": props})
    text = transcript.render(data, {"w": "Affinity\nAt 2 or less a row moves."})
    assert "it can also show (from the widget's data): name: Affinity; reason: Affinity 2 or less" in text
    assert "park" not in text


def test_term_links_read_as_their_words():
    data = _widget_data({"type": "custom", "module": "sheet", "caption": "Scores."},
                        text="Think [[Purchasing agency]] and [[Budget scope|budgets]].")
    text = transcript.render(data)
    assert "Think Purchasing agency and budgets." in text
