"""W4 Content Brain - daily content briefs for WTF Avatar Factory.

Generates a complete, deterministic brief per day:
  pillar + topic (curated library) -> hook, 45s script x2 languages (hi/en),
  b-roll shot list, caption + hashtags, thumbnail idea.

Dark by design: generation runs through the STUBBED provider in providers.py
(no model, no network, no randomness). Reproducible byte-for-byte.

Quick use:
    python brain.py --date 2026-09-14 --pillar ai_systems --topic ai_systems-01
    python brain.py --date 2026-09-14 --json
    python brain.py --write-samples
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import providers
from providers import GenerationRequest, LLMProvider, ProviderError, get_provider, render_template

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_PILLARS_PATH = PKG_DIR / "pillars.yaml"
PROMPTS_DIR = PKG_DIR / "prompts"
SAMPLES_DIR = PKG_DIR / "samples"

LANGUAGES = ("en", "hi")
LANGUAGE_LABELS = {"en": "English", "hi": "Hindi"}
VIDEO_FORMAT = "9:16_vertical_reel"
DURATION_S = 45

# The fixed 45s beat sheet: 3s hook + 8s context + 3x8s points + 5s proof + 5s CTA.
BEATS: List[Dict[str, str]] = [
    {"name": "HOOK", "t": "0:00–0:03", "type": "A-roll"},
    {"name": "CONTEXT", "t": "0:03–0:11", "type": "A-roll + b-roll cutaway"},
    {"name": "POINT 1", "t": "0:11–0:19", "type": "b-roll"},
    {"name": "POINT 2", "t": "0:19–0:27", "type": "b-roll"},
    {"name": "POINT 3", "t": "0:27–0:35", "type": "b-roll"},
    {"name": "PROOF", "t": "0:35–0:40", "type": "b-roll + text card"},
    {"name": "CTA", "t": "0:40–0:45", "type": "A-roll"},
]

SCRIPT_PROMPT_TEMPLATE = "prompts/script_45s_{lang}.md"
CAPTION_PROMPT_TEMPLATE = "prompts/caption_hashtags.md"
THUMBNAIL_PROMPT_TEMPLATE = "prompts/thumbnail.md"
SHOTLIST_PROMPT_TEMPLATE = "prompts/shot_list.md"


# ---------------------------------------------------------------------------
# YAML loading (PyYAML when available, stdlib-only constrained fallback)
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")


def _mini_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "Null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _mini_split_kv(content: str):
    """Split 'key: value' on the first colon outside quotes; else (None, None)."""
    in_quote = None
    for i, ch in enumerate(content):
        if in_quote:
            if ch == in_quote and (i == 0 or content[i - 1] != "\\"):
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == ":" and (i + 1 == len(content) or content[i + 1] == " "):
            key = content[:i].strip()
            if _KEY_RE.fullmatch(key):
                return key, content[i + 1:].strip()
            return None, None
    return None, None


def _mini_block(lines, i, indent):
    content = lines[i][1]
    if content.startswith("- ") or content == "-":
        return _mini_list(lines, i, indent)
    return _mini_map(lines, i, indent)


def _mini_map(lines, i, indent):
    out: Dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if content.startswith("- ") or content == "-":
            break
        key, val = _mini_split_kv(content)
        if key is None:
            raise ValueError(
                "mini-yaml: expected 'key: value' at line {}: {!r}".format(lines[i][2], content)
            )
        if val:
            out[key] = _mini_scalar(val)
            i += 1
        elif i + 1 < len(lines) and lines[i + 1][0] > indent:
            out[key], i = _mini_block(lines, i + 1, lines[i + 1][0])
        else:
            out[key] = None
            i += 1
    return out, i


def _mini_list(lines, i, indent):
    out: List[Any] = []
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if not (content.startswith("- ") or content == "-"):
            break
        item = content[2:].strip() if content.startswith("- ") else ""
        if not item:
            raise ValueError("mini-yaml: bare '-' list items are not supported (line {})".format(lines[i][2]))
        key, val = _mini_split_kv(item)
        if key is None:
            out.append(_mini_scalar(item))
            i += 1
            continue
        entry: Dict[str, Any] = {}
        if val:
            entry[key] = _mini_scalar(val)
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                more, i = _mini_map(lines, i, lines[i][0])
                entry.update(more)
        elif i + 1 < len(lines) and lines[i + 1][0] > indent:
            entry[key], i = _mini_block(lines, i + 1, lines[i + 1][0])
        else:
            entry[key] = None
            i += 1
        out.append(entry)
    return out, i


def parse_mini_yaml(text: str) -> Any:
    """Parse the constrained YAML subset used by pillars.yaml (stdlib only).

    Supported: nested maps/lists by 2-space indentation, quoted/plain scalars,
    booleans, ints, floats, null. Not supported: flow collections, anchors,
    block scalars, multi-line strings, inline comments.
    """
    lines = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if raw.strip() and not raw.lstrip().startswith("#"):
            s = raw.rstrip()
            indent = len(s) - len(s.lstrip(" "))
            if indent % 2 != 0:
                raise ValueError("mini-yaml: odd indentation at line {}: {!r}".format(lineno, raw))
            lines.append((indent, s.strip(), lineno))
    if not lines:
        return {}
    value, idx = _mini_block(lines, 0, lines[0][0])
    if idx != len(lines):
        raise ValueError("mini-yaml: trailing unparsed content at line {}".format(lines[idx][2]))
    return value


def load_library(path: Path = DEFAULT_PILLARS_PATH) -> Dict[str, Any]:
    """Load and sanity-check the pillar library (PyYAML, else mini fallback)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data: Any
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        data = yaml.safe_load(text)
    else:
        data = parse_mini_yaml(text)
    if not isinstance(data, dict) or not isinstance(data.get("pillars"), list) or not data["pillars"]:
        raise ValueError("pillar library is malformed: {}".format(path))
    if not isinstance(data.get("languages"), list):
        raise ValueError("pillar library missing languages: {}".format(path))
    return data


# ---------------------------------------------------------------------------
# Brief model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Brief:
    id: str
    date: str
    pillar: str
    pillar_label: str
    brand: str
    brand_label: str
    topic_id: str
    title: Dict[str, str]
    angle: Dict[str, str]
    hook: Dict[str, str]
    context: Dict[str, str]
    points: Dict[str, List[str]]
    proof: Dict[str, str]
    cta: Dict[str, str]
    scripts: Dict[str, str]
    word_counts: Dict[str, int]
    caption: Dict[str, str]
    hashtags: List[str]
    shot_list: List[Dict[str, str]]
    thumbnail: str
    thumb_text: str
    format: str
    duration_s: int
    languages: List[str]
    prompt_audit: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["generated_by"] = "w4-content-brain"
        return payload

    # -- rendering ----------------------------------------------------------

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append("# Daily Brief — {} — {}".format(self.date, self.pillar_label))
        lines.append("")
        lines.append("**Brief ID:** {}".format(self.id))
        lines.append("**Brand:** {} (`{}`)".format(self.brand_label, self.brand))
        lines.append("**Topic:** {}".format(self.title["en"]))
        lines.append("**Topic (HI):** {}".format(self.title["hi"]))
        lines.append(
            "**Format:** {} · **Duration:** {}s · **Languages:** Hindi + English".format(
                self.format, self.duration_s
            )
        )
        lines.append("")
        lines.append("## Hook (first 3 seconds)")
        lines.append("- **EN:** {}".format(self.hook["en"]))
        lines.append("- **HI:** {}".format(self.hook["hi"]))
        lines.append("")
        for lang in self.languages:
            lines.append("## 45s Script — {}".format(LANGUAGE_LABELS[lang]))
            lines.append(self.scripts[lang].rstrip())
            lines.append("")
        lines.append("## B-Roll Shot List")
        lines.append("| # | Time | Type | Shot | Overlay |")
        lines.append("|---|------|------|------|---------|")
        for n, row in enumerate(self.shot_list, start=1):
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    n, row["t"], row["type"], row["shot"], row["overlay"]
                )
            )
        lines.append("")
        lines.append("## Caption + Hashtags")
        lines.append("")
        lines.append("**Caption (EN):**")
        lines.append(self.caption["en"])
        lines.append("")
        lines.append("**Caption (HI):**")
        lines.append(self.caption["hi"])
        lines.append("")
        lines.append("**Hashtags:** {}".format(" ".join(self.hashtags)))
        lines.append("")
        lines.append("## Thumbnail Idea")
        lines.append(self.thumbnail)
        lines.append("")
        lines.append("## Production Notes")
        lines.append("- Voice: cloned voice; render Hindi + English cuts separately.")
        lines.append("- Avatar: LongCat-Video-Avatar 1.5 (int8 + distill, L40S 48GB) via render-kit.")
        lines.append("- Publishing: DARK in v1. This brief becomes queued jobs; console approval gates every send. LinkedIn stays manual.")
        lines.append("- Facts: no stat goes live without a source — verified at approval time.")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def today_ist() -> date:
    try:
        from zoneinfo import ZoneInfo  # type: ignore

        return datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return datetime.now().date()


def _find_pillar(lib: Dict[str, Any], pillar_id: str) -> Dict[str, Any]:
    for pillar in lib["pillars"]:
        if pillar["id"] == pillar_id:
            return pillar
    raise ValueError(
        "unknown pillar {!r}; available: {}".format(
            pillar_id, ", ".join(p["id"] for p in lib["pillars"])
        )
    )


def _find_topic(pillar: Dict[str, Any], topic_id: Optional[str], day: date, variation: int) -> Dict[str, Any]:
    topics = pillar["topics"]
    if topic_id is None:
        return topics[(day.toordinal() + int(variation)) % len(topics)]
    for topic in topics:
        if topic["id"] == topic_id:
            return topic
    raise ValueError(
        "unknown topic {!r} for pillar {!r}; available: {}".format(
            topic_id, pillar["id"], ", ".join(t["id"] for t in topics)
        )
    )


def _find_brand(lib: Dict[str, Any], brand_id: str) -> Dict[str, Any]:
    for brand in lib["brands"]:
        if brand["id"] == brand_id:
            return brand
    raise ValueError("unknown brand {!r}".format(brand_id))


def _prompt_text(template_rel: str, slots: Dict[str, Any]) -> str:
    path = PKG_DIR / template_rel
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError("prompt template missing: {}".format(path))
    return render_template(text, slots)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _first_words(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[:n])


def _mid_sentence(topic: str) -> str:
    """Lowercase a leading article so {topic_lc} reads naturally mid-sentence."""
    for article in ("The ", "A ", "An "):
        if topic.startswith(article):
            return topic[0].lower() + topic[1:]
    return topic


def _render_script(lang_label: str, words: int, vo_lines: List[str], overlays: List[str]) -> str:
    out = ["**{}** — ~{} words ≈ {}s at natural pace".format(lang_label, words, DURATION_S), ""]
    for beat, vo, overlay in zip(BEATS, vo_lines, overlays):
        out.append("[{}] {}".format(beat["t"], beat["name"]))
        out.append('  VO: "{}"'.format(vo))
        out.append("  ON-SCREEN: {}".format(overlay))
        out.append("")
    return "\n".join(out)


def daily_brief(
    day: Optional[date] = None,
    *,
    pillar: Optional[str] = None,
    brand: Optional[str] = None,
    topic: Optional[str] = None,
    variation: int = 0,
    provider: Optional[LLMProvider] = None,
    library: Optional[Dict[str, Any]] = None,
) -> Brief:
    """Build the complete daily brief. Deterministic for identical inputs."""
    day = day or today_ist()
    lib = library if library is not None else load_library()
    provider = provider if provider is not None else get_provider()

    if pillar is None:
        pillar = lib["rotation"]["weekly"][day.strftime("%A").lower()]
    pillar_obj = _find_pillar(lib, pillar)
    topic_obj = _find_topic(pillar_obj, topic, day, variation)
    brand_id = brand or topic_obj.get("brand") or pillar_obj["default_brand"]
    brand_obj = _find_brand(lib, brand_id)

    title = {"en": topic_obj["title_en"], "hi": topic_obj["title_hi"]}
    angle = {"en": topic_obj["angle_en"], "hi": topic_obj["angle_hi"]}
    points = {"en": list(topic_obj["points_en"]), "hi": list(topic_obj["points_hi"])}
    proof = {"en": topic_obj["proof_en"], "hi": topic_obj["proof_hi"]}
    props = list(topic_obj["props"])
    thumb_text = topic_obj["thumb_text"]

    audit: List[Dict[str, str]] = []

    def _gen(kind: str, slots: Dict[str, Any], template_rel: str, language: str) -> str:
        prompt_text = _prompt_text(template_rel, dict(slots, language=language, language_label=LANGUAGE_LABELS[language]))
        audit.append(
            {
                "kind": kind,
                "language": language,
                "template": template_rel,
                "prompt_sha256": _sha256(prompt_text),
            }
        )
        request = GenerationRequest(
            kind=kind,
            slots=slots,
            prompt_template=template_rel,
            prompt_text=prompt_text,
            variation=variation,
        )
        return provider.generate(request)

    common = {
        "topic": None,  # replaced per language
        "topic_en": title["en"],
        "topic_hi": title["hi"],
        "pillar": pillar_obj["id"],
        "pillar_label": pillar_obj["label"],
        "brand": brand_obj["id"],
        "brand_label": brand_obj["label"],
        "duration_s": DURATION_S,
        "format": VIDEO_FORMAT,
        "date": day.isoformat(),
        "variation": variation,
        "props": props,
    }

    hook: Dict[str, str] = {}
    context: Dict[str, str] = {}
    proof_out: Dict[str, str] = {}
    cta: Dict[str, str] = {}
    parts: Dict[str, List[str]] = {}

    for lang in LANGUAGES:
        slots = dict(
            common,
            topic=title[lang],
            topic_lc=_mid_sentence(title[lang]),
            angle=angle[lang],
            hooks=[t for t in pillar_obj["hook_templates_" + lang]],
            contexts=list(pillar_obj["context_templates_" + lang]),
            ctas=list(pillar_obj["cta_templates_" + lang]),
            points=points[lang],
            proof=proof[lang],
            thumb_text=thumb_text,
        )
        hook[lang] = _gen("hook", slots, SCRIPT_PROMPT_TEMPLATE.format(lang=lang), lang)
        context[lang] = _gen("context", slots, SCRIPT_PROMPT_TEMPLATE.format(lang=lang), lang)
        point_lines = json.loads(_gen("points", slots, SCRIPT_PROMPT_TEMPLATE.format(lang=lang), lang))
        proof_out[lang] = _gen("proof", slots, SCRIPT_PROMPT_TEMPLATE.format(lang=lang), lang)
        cta[lang] = _gen("cta", slots, SCRIPT_PROMPT_TEMPLATE.format(lang=lang), lang)
        parts[lang] = [hook[lang], context[lang]] + point_lines + [proof_out[lang], cta[lang]]

    scripts: Dict[str, str] = {}
    word_counts: Dict[str, int] = {}
    for lang in LANGUAGES:
        words = sum(len(part.split()) for part in parts[lang])
        overlays = [
            thumb_text.upper(),
            brand_obj["label"].upper(),
            *[_first_words(p, 5).upper() for p in points[lang]],
            "PROOF",
            "FOLLOW",
        ]
        scripts[lang] = _render_script(LANGUAGE_LABELS[lang], words, parts[lang], overlays)
        word_counts[lang] = words

    # caption per language (uses the already-generated CTA line)
    caption: Dict[str, str] = {}
    for lang in LANGUAGES:
        slots = dict(common, topic=title[lang], angle=angle[lang], title=title[lang], cta_line=cta[lang])
        caption[lang] = _gen("caption", slots, CAPTION_PROMPT_TEMPLATE, lang)

    # hashtags (language-agnostic): brand tags, then pillar tags, then core
    hashtag_pool: List[str] = []
    for tag in list(brand_obj["hashtags"]) + list(pillar_obj["hashtags"]) + list(lib["core_hashtags"]):
        if tag not in hashtag_pool:
            hashtag_pool.append(tag)
    hashtags = json.loads(
        _gen(
            "hashtags",
            {
                **common,
                "topic": title["en"],
                "hashtag_pool": hashtag_pool,
                "max_hashtags": int(lib["format"].get("max_hashtags", 12)),
            },
            CAPTION_PROMPT_TEMPLATE,
            "en",
        )
    )

    # thumbnail
    thumb_slots = dict(
        common,
        topic=title["en"],
        concepts=list(pillar_obj["thumbnail_concepts"]),
        thumb_text=thumb_text,
        expression=pillar_obj["expressions"][variation % len(pillar_obj["expressions"])],
    )
    thumbnail = _gen("thumbnail", thumb_slots, THUMBNAIL_PROMPT_TEMPLATE, "en")

    # b-roll shot list: one row per beat, backed by the topic's own props
    shot_rows: List[Dict[str, str]] = []
    shot_plan = [
        "Tight close-up, direct to camera — hook delivered straight to lens",
        "Medium walk-and-talk; cutaway b-roll — {}".format(props[0]),
        "B-roll — {}".format(props[1]),
        "B-roll — {}".format(props[2]),
        "B-roll — {}".format(props[3 % len(props)]),
        "Proof beat — {} + text card".format(props[0]),
        "A-roll close, direct CTA; end card with handle",
    ]
    beat_overlays = [
        thumb_text.upper(),
        brand_obj["label"].upper(),
        *[_first_words(p, 5).upper() for p in points["en"]],
        "PROOF",
        "FOLLOW",
    ]
    for beat, shot_text, overlay in zip(BEATS, shot_plan, beat_overlays):
        shot_rows.append({"t": beat["t"], "type": beat["type"], "shot": shot_text, "overlay": overlay})
    shot_list = json.loads(
        _gen("shot_list", dict(common, topic=title["en"], beats=shot_rows), SHOTLIST_PROMPT_TEMPLATE, "en")
    )

    brief_id = "cb-{}-{}-{}".format(day.isoformat(), pillar_obj["id"], topic_obj["id"].split("-")[-1])

    return Brief(
        id=brief_id,
        date=day.isoformat(),
        pillar=pillar_obj["id"],
        pillar_label=pillar_obj["label"],
        brand=brand_obj["id"],
        brand_label=brand_obj["label"],
        topic_id=topic_obj["id"],
        title=title,
        angle=angle,
        hook=hook,
        context=context,
        points=points,
        proof=proof_out,
        cta=cta,
        scripts=scripts,
        word_counts=word_counts,
        caption=caption,
        hashtags=hashtags,
        shot_list=shot_list,
        thumbnail=thumbnail,
        thumb_text=thumb_text,
        format=VIDEO_FORMAT,
        duration_s=DURATION_S,
        languages=list(LANGUAGES),
        prompt_audit=audit,
    )


# ---------------------------------------------------------------------------
# Sample briefs (10 curated briefs — the W4 showcase artifacts)
# ---------------------------------------------------------------------------

SAMPLE_START = date(2026, 9, 14)


def write_samples(out_dir: Path = SAMPLES_DIR, start: date = SAMPLE_START) -> List[Dict[str, Any]]:
    """Generate the 10 sample briefs (2 per pillar) + index.json into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lib = load_library()
    entries: List[Dict[str, Any]] = []
    order = 0
    for pillar_obj in lib["pillars"]:
        for topic_obj in pillar_obj["topics"]:
            order += 1
            day = start.fromordinal(start.toordinal() + order - 1)
            brief = daily_brief(
                day,
                pillar=pillar_obj["id"],
                topic=topic_obj["id"],
                variation=order - 1,
                library=lib,
            )
            fname = "brief_{:02d}_{}.md".format(order, topic_obj["id"])
            payload = brief.to_markdown().encode("utf-8")
            (out_dir / fname).write_bytes(payload)
            entries.append(
                {
                    "order": order,
                    "file": fname,
                    "brief_id": brief.id,
                    "date": brief.date,
                    "pillar": brief.pillar,
                    "brand": brief.brand,
                    "topic_id": brief.topic_id,
                    "title_en": brief.title["en"],
                    "title_hi": brief.title["hi"],
                    "languages": list(brief.languages),
                    "word_counts": brief.word_counts,
                    "hashtag_count": len(brief.hashtags),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
    index = {
        "version": "1.0",
        "generated_by": "w4-content-brain",
        "start_date": start.isoformat(),
        "briefs": entries,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="brain.py", description="W4 Content Brain — daily briefs")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today, IST)")
    parser.add_argument("--pillar", help="pillar id (default: weekday rotation)")
    parser.add_argument("--brand", help="brand id (default: pillar/topic default)")
    parser.add_argument("--topic", help="topic id (default: date rotation)")
    parser.add_argument("--variation", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="print machine-readable brief")
    parser.add_argument("--out", help="write markdown brief to this path")
    parser.add_argument("--write-samples", action="store_true", help="regenerate the 10 samples")
    parser.add_argument("--samples-dir", default=str(SAMPLES_DIR))
    args = parser.parse_args(argv)

    if args.write_samples:
        entries = write_samples(Path(args.samples_dir))
        for entry in entries:
            print("{}  {}  {}  {}".format(entry["file"], entry["brief_id"], entry["pillar"], entry["title_en"]))
        print("OK: {} briefs + index.json in {}".format(len(entries), args.samples_dir))
        return 0

    day = date.fromisoformat(args.date) if args.date else today_ist()
    try:
        brief = daily_brief(day, pillar=args.pillar, brand=args.brand, topic=args.topic,
                            variation=args.variation)
    except (ValueError, ProviderError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(brief.to_dict(), ensure_ascii=False, indent=2))
        return 0
    markdown = brief.to_markdown()
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        print("wrote {}".format(out))
        return 0
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
