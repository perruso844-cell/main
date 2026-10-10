#!/usr/bin/env python3
"""AI-writing self-check for essay drafts, modeled on what school detectors look at.

Three modes:
  (default)     Style heuristics, no dependencies: sentence-length "burstiness",
                AI-favored vocabulary, formulaic constructions, tidy triplets,
                uniform paragraphs, and missing concrete detail.
  --perplexity  Adds a GPTZero-style perplexity + burstiness score using a local
                GPT-2 model. Needs: pip install torch transformers
                (downloads ~500 MB of model on first run; text stays on this machine).
  --gptzero     Sends the text to the real GPTZero API. Needs GPTZERO_API_KEY.
                The essay leaves this machine; only use with the writer's consent.

Turnitin has no public API for individuals, so it can't be called from here.

Usage: python3 ai_check.py essay.txt [--perplexity] [--gptzero] [--json]
       cat essay.txt | python3 ai_check.py -

No detector, this one included, can prove who wrote something. Scores are
probabilistic and produce false positives, especially on polished, formal, or
non-native-English writing. Use the flags as revision prompts.
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import urllib.request

AI_VOCAB = [
    "delve", "delves", "delving", "tapestry", "testament", "multifaceted", "intricate",
    "intricacies", "pivotal", "foster", "fostering", "underscore", "underscores",
    "embark", "embarked", "journey", "realm", "navigate", "navigating", "resonate",
    "resonates", "profound", "profoundly", "invaluable", "unwavering", "vibrant",
    "nuanced", "showcasing", "showcase", "highlighting", "crucial", "paramount",
    "meticulous", "meticulously", "transformative", "endeavor", "endeavors",
    "commitment", "instilled", "ignited", "sparked", "cultivate", "cultivated",
    "holistic", "dynamic", "landscape", "beacon", "symphony", "unwavering",
    "steadfast", "indelible", "boundless", "kaleidoscope", "interplay", "enduring",
    "empower", "empowered", "seamlessly", "elevate", "harness", "shaping",
]

FORMULAIC = [
    r"\bnot (?:just|only|merely)\b[^.]{0,80}\bbut\b",
    r"\bit(?:'s| is| was) not about\b[^.]{0,60}\bit(?:'s| is| was) about\b",
    r"\bin a world where\b",
    r"\bwhether it(?:'s| is| be| was)\b",
    r"\bserve[sd]? as a\b",
    r"\bst(?:ands?|ood) as a\b",
    r"\ba (?:powerful |stark |constant )?reminder (?:that|of)\b",
    r"\bthis experience (?:taught|showed|has taught) me\b",
    r"\bi (?:learned|realized|discovered) that\b",
    r"\bin essence\b", r"\bat its core\b", r"\bultimately,", r"\bin conclusion\b",
    r"\bmore than (?:just )?a\b", r"\bthe power of\b", r"\bnever forget\b",
    r"\bshaped (?:me|who i am)\b", r"\bwho i am today\b", r"\blooking back,",
    r"\bas i (?:look|move) (?:forward|ahead)\b", r"\bi am (?:eager|excited) to\b",
]

TRANSITIONS = ("moreover", "furthermore", "additionally", "however", "ultimately",
               "consequently", "nevertheless", "thus", "therefore", "in addition",
               "overall", "notably", "importantly")


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])[\"”’)]*\s+(?=[\"“‘(]?[A-Z0-9])", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 2]


def cv(values):
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean else 0.0


def heuristics(text):
    words = re.findall(r"[A-Za-z0-9'’-]+", text)
    n = max(len(words), 1)
    lower = text.lower().replace("’", "'")
    sentences = split_sentences(text)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    sent_lens = [len(s.split()) for s in sentences]

    vocab_hits = [w for w in re.findall(r"[a-z]+", lower) if w in AI_VOCAB]
    formulaic_hits = []
    for pat in FORMULAIC:
        formulaic_hits += [m.group(0) for m in re.finditer(pat, lower)]
    triplets = re.findall(r"\b[\w-]+(?: [\w-]+)?, [\w-]+(?: [\w-]+)?,? and [\w-]+", lower)
    transition_starts = [s for s in sentences if s.lower().startswith(TRANSITIONS)]
    em_dashes = text.count("—") + len(re.findall(r"\s--\s", text))

    # Human-signal: concrete specifics (numbers, mid-sentence proper nouns, dialogue).
    digits = len(re.findall(r"\b\d[\d,.:]*\b", text))
    proper = sum(len(re.findall(r"(?<=[a-z,;] )[A-Z][a-z]+", s)) for s in sentences)
    quotes = len(re.findall(r"[\"“][^\"”]{3,}[\"”]", text))
    concrete_per_100 = 100 * (digits + proper + 2 * quotes) / n

    per1k = 1000 / n
    signals = {
        "low_burstiness": {
            "value": round(cv(sent_lens), 2),
            "score": clamp((0.55 - cv(sent_lens)) / 0.30) if len(sent_lens) >= 5 else 0.0,
            "weight": 0.25,
            "explain": "Sentence lengths are very even (coefficient of variation; humans are usually > 0.5).",
        },
        "ai_vocabulary": {
            "value": round(len(vocab_hits) * per1k, 1),
            "score": clamp(len(vocab_hits) * per1k / 8),
            "weight": 0.20,
            "explain": "Words AI models overuse, per 1,000 words.",
            "hits": sorted(set(vocab_hits)),
        },
        "formulaic_phrasing": {
            "value": round(len(formulaic_hits) * per1k, 1),
            "score": clamp(len(formulaic_hits) * per1k / 8),
            "weight": 0.20,
            "explain": "Stock constructions (\"not just X but Y\", \"this taught me\", \"ultimately\"), per 1,000 words.",
            "hits": formulaic_hits,
        },
        "tidy_triplets": {
            "value": round(len(triplets) * per1k, 1),
            "score": clamp(len(triplets) * per1k / 6),
            "weight": 0.10,
            "explain": "\"X, Y, and Z\" lists, per 1,000 words.",
        },
        "transition_openers": {
            "value": len(transition_starts),
            "score": clamp(len(transition_starts) / max(len(sentences), 1) / 0.12),
            "weight": 0.08,
            "explain": "Sentences opening with Moreover/Furthermore/Ultimately etc.",
        },
        "uniform_paragraphs": {
            "value": round(cv([len(p.split()) for p in paragraphs]), 2),
            "score": clamp((0.40 - cv([len(p.split()) for p in paragraphs])) / 0.25) if len(paragraphs) >= 4 else 0.0,
            "weight": 0.07,
            "explain": "Paragraphs are all about the same length.",
        },
        "em_dashes": {
            "value": round(em_dashes * per1k, 1),
            "score": clamp(em_dashes * per1k / 10),
            "weight": 0.05,
            "explain": "Em dashes per 1,000 words.",
        },
        "low_concreteness": {
            "value": round(concrete_per_100, 1),
            "score": clamp((3.0 - concrete_per_100) / 3.0),
            "weight": 0.05,
            "explain": "Few numbers, names, places, or dialogue per 100 words.",
        },
    }
    total = sum(s["score"] * s["weight"] for s in signals.values()) / sum(s["weight"] for s in signals.values())

    def sentence_score(s):
        sl = s.lower().replace("’", "'")
        return (sum(1 for w in re.findall(r"[a-z]+", sl) if w in AI_VOCAB)
                + sum(len(re.findall(p, sl)) for p in FORMULAIC)
                + len(re.findall(r", [\w-]+,? and ", sl)))

    flagged = sorted(((sentence_score(s), s) for s in sentences), key=lambda x: -x[0])
    flagged = [s for sc, s in flagged if sc >= 2][:6]
    return {"words": len(words), "sentences": len(sentences), "score": round(100 * total),
            "signals": signals, "flagged_sentences": flagged}


def perplexity(text):
    try:
        import torch
        from transformers import GPT2LMHeadModel, GPT2TokenizerFast
    except ImportError:
        return {"error": "Perplexity mode needs: pip install torch transformers"}
    try:
        tok = GPT2TokenizerFast.from_pretrained("gpt2")
        model = GPT2LMHeadModel.from_pretrained("gpt2").eval()
    except Exception as e:  # usually no network access to huggingface.co on first run
        return {"error": f"Couldn't load the GPT-2 model (needs huggingface.co on first run): {type(e).__name__}"}

    def ppl(segment):
        ids = tok(segment, return_tensors="pt").input_ids[:, :1024]
        if ids.shape[1] < 2:
            return None
        with torch.no_grad():
            loss = model(ids, labels=ids).loss.item()
        return math.exp(loss)

    sent_ppl = [p for p in (ppl(s) for s in split_sentences(text)) if p]
    overall = ppl(text)
    burst = statistics.pstdev(sent_ppl) if len(sent_ppl) > 1 else 0.0
    # Rough GPT-2 bands: AI text tends to be low and even; student writing higher and spikier.
    if overall < 25 and burst < 40:
        verdict = "likely to be flagged (low, even perplexity)"
    elif overall < 45:
        verdict = "borderline"
    else:
        verdict = "reads human-like (high or varied perplexity)"
    return {"perplexity": round(overall, 1), "burstiness": round(burst, 1),
            "sentence_perplexity_mean": round(statistics.mean(sent_ppl), 1) if sent_ppl else None,
            "verdict": verdict}


def gptzero(text):
    key = os.environ.get("GPTZERO_API_KEY")
    if not key:
        return {"error": "Set GPTZERO_API_KEY (get one at gptzero.me) to use --gptzero."}
    req = urllib.request.Request(
        "https://api.gptzero.me/v2/predict/text",
        data=json.dumps({"document": text}).encode(),
        headers={"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except Exception as e:  # network/auth errors: report, don't crash the local checks
        return {"error": f"GPTZero request failed: {e}"}
    doc = (data.get("documents") or [{}])[0]
    keep = ("predicted_class", "class_probabilities", "completely_generated_prob",
            "average_generated_prob", "overall_burstiness", "confidence_category")
    return {k: doc[k] for k in keep if k in doc} or data


def band(score):
    if score >= 60:
        return "HIGH: reads like typical AI output; likely to draw a detector flag"
    if score >= 35:
        return "MODERATE: some AI-style patterns worth revising"
    return "LOW: few AI-style patterns"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="essay text file, or - for stdin")
    ap.add_argument("--perplexity", action="store_true", help="add local GPT-2 perplexity/burstiness")
    ap.add_argument("--gptzero", action="store_true", help="also query the GPTZero API (sends text off-machine)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args()

    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    result = {"heuristics": heuristics(text)}
    if args.perplexity:
        result["perplexity"] = perplexity(text)
    if args.gptzero:
        result["gptzero"] = gptzero(text)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    h = result["heuristics"]
    print(f"AI-style score: {h['score']}/100  ->  {band(h['score'])}")
    print(f"({h['words']} words, {h['sentences']} sentences)\n")
    print("Signals (0 = human-like, 1 = AI-like):")
    for name, s in sorted(h["signals"].items(), key=lambda kv: -kv[1]["score"] * kv[1]["weight"]):
        line = f"  {name:<20} {s['score']:.2f}   value={s['value']}   {s['explain']}"
        print(line)
        if s.get("hits") and s["score"] > 0:
            print(f"  {'':<20}        hits: {', '.join(dict.fromkeys(s['hits']))}")
    if h["flagged_sentences"]:
        print("\nSentences with the most AI-style markers:")
        for s in h["flagged_sentences"]:
            print(f"  - {s}")
    if "perplexity" in result:
        print("\nPerplexity (local GPT-2):", json.dumps(result["perplexity"]))
    if "gptzero" in result:
        print("\nGPTZero:", json.dumps(result["gptzero"]))
    print("\nNote: no detector can prove authorship, and all of them have false positives."
          "\nUse flags as revision prompts; keep drafts and version history as proof you wrote it.")


if __name__ == "__main__":
    main()
