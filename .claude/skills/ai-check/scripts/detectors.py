#!/usr/bin/env python3
"""Run a text through several AI detectors and compare their scores side by side.

Every result is converted to one scale: AI likelihood, 0 (reads human) to 100 (reads AI).
The summary gives the median, the mean, the range, and how many detectors lean each way.

Local checks (always run, nothing leaves this machine):
  style        Style heuristics: sentence-length burstiness, AI-favored vocabulary,
               stock phrasing, tidy triplets, transition openers, uniform paragraphs.
  perplexity   With --perplexity: GPT-2 perplexity + burstiness, like early GPTZero.
               Needs: pip install torch transformers (~500 MB model on first run).
  rubric       With --rubric N: the 0-100 score from the ai-check skill's rubric.

Commercial detectors (opt-in with --services; each one SENDS THE TEXT to that company):
  gptzero      GPTZERO_API_KEY
  originality  ORIGINALITY_API_KEY
  copyleaks    COPYLEAKS_EMAIL and COPYLEAKS_API_KEY
  sapling      SAPLING_API_KEY
  winston      WINSTON_API_KEY
  pangram      PANGRAM_API_KEY (needs: pip install pangram-sdk)
A service without its key is skipped, not an error. Turnitin has no public API.

Usage: python3 detectors.py essay.txt [--services gptzero,sapling | all]
                            [--perplexity] [--rubric N] [--json]
       cat essay.txt | python3 detectors.py -

No detector can prove who wrote something. They disagree with each other, and they
share false positives on formal and non-native-English writing, so several agreeing
is not proof either. Use the results as revision prompts.
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
import uuid

AI_VOCAB = {
    "delve", "delves", "delving", "tapestry", "testament", "multifaceted", "intricate",
    "intricacies", "pivotal", "foster", "fostering", "underscore", "underscores",
    "embark", "embarked", "journey", "realm", "navigate", "navigating", "resonate",
    "resonates", "profound", "profoundly", "invaluable", "unwavering", "vibrant",
    "nuanced", "showcasing", "showcase", "highlighting", "crucial", "paramount",
    "meticulous", "meticulously", "transformative", "endeavor", "endeavors",
    "commitment", "instilled", "ignited", "sparked", "cultivate", "cultivated",
    "holistic", "dynamic", "landscape", "beacon", "symphony", "steadfast", "indelible",
    "boundless", "kaleidoscope", "interplay", "enduring", "empower", "empowered",
    "seamlessly", "elevate", "harness", "shaping", "leverage", "robust",
}

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

SERVICES = ("gptzero", "originality", "copyleaks", "sapling", "winston", "pangram")


class Skip(Exception):
    """The detector can't run here (no key, missing package). Not a failure."""


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def pct(x):
    """Detectors report 0-1 or 0-100; normalize to 0-100."""
    x = float(x)
    return round(100 * x if x <= 1 else x, 1)


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])[\"”’)]*\s+(?=[\"“‘(]?[A-Z0-9])", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 2]


def cv(values):
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean else 0.0


def style(text):
    words = re.findall(r"[A-Za-z0-9'’-]+", text)
    n = max(len(words), 1)
    lower = text.lower().replace("’", "'")
    sentences = split_sentences(text)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    sent_lens = [len(s.split()) for s in sentences]
    para_cv = cv([len(p.split()) for p in paragraphs])

    vocab = [w for w in re.findall(r"[a-z]+", lower) if w in AI_VOCAB]
    formulaic = [m.group(0) for p in FORMULAIC for m in re.finditer(p, lower)]
    triplets = re.findall(r"\b[\w-]+(?: [\w-]+)?, [\w-]+(?: [\w-]+)?,? and [\w-]+", lower)
    openers = [s for s in sentences if s.lower().startswith(TRANSITIONS)]
    dashes = text.count("—") + len(re.findall(r"\s--\s", text))
    concrete = (len(re.findall(r"\b\d[\d,.:]*\b", text))
                + sum(len(re.findall(r"(?<=[a-z,;] )[A-Z][a-z]+", s)) for s in sentences)
                + 2 * len(re.findall(r"[\"“][^\"”]{3,}[\"”]", text)))

    per1k = 1000 / n
    # (score 0-1, weight)
    signals = {
        "even sentence lengths": (clamp((0.55 - cv(sent_lens)) / 0.30) if len(sent_lens) >= 5 else 0.0, 0.25),
        "AI-favored vocabulary": (clamp(len(vocab) * per1k / 8), 0.20),
        "stock phrasing": (clamp(len(formulaic) * per1k / 8), 0.20),
        "tidy triplets": (clamp(len(triplets) * per1k / 6), 0.10),
        "transition openers": (clamp(len(openers) / max(len(sentences), 1) / 0.12), 0.08),
        "uniform paragraphs": (clamp((0.40 - para_cv) / 0.25) if len(paragraphs) >= 4 else 0.0, 0.07),
        "em dashes": (clamp(dashes * per1k / 10), 0.05),
        "few concrete details": (clamp((3.0 - 100 * concrete / n) / 3.0), 0.05),
    }
    total = sum(s * w for s, w in signals.values()) / sum(w for _, w in signals.values())
    top = [name for name, (s, w) in sorted(signals.items(), key=lambda kv: -kv[1][0] * kv[1][1]) if s >= 0.5]
    note = "strongest: " + ", ".join(top[:3]) if top else "no strong style signals"
    return round(100 * total, 1), note


def perplexity(text):
    try:
        import torch
        from transformers import GPT2LMHeadModel, GPT2TokenizerFast
    except ImportError:
        raise Skip("pip install torch transformers")
    try:
        tok = GPT2TokenizerFast.from_pretrained("gpt2")
        model = GPT2LMHeadModel.from_pretrained("gpt2").eval()
    except Exception as e:  # usually no network access to huggingface.co on first run
        raise Skip(f"couldn't load GPT-2 (needs huggingface.co on first run): {type(e).__name__}")

    def ppl(segment):
        ids = tok(segment, return_tensors="pt").input_ids[:, :1024]
        if ids.shape[1] < 2:
            return None
        with torch.no_grad():
            return math.exp(model(ids, labels=ids).loss.item())

    overall = ppl(text)
    sent = [p for p in (ppl(s) for s in split_sentences(text)) if p]
    burst = statistics.pstdev(sent) if len(sent) > 1 else 0.0
    # Rough GPT-2 bands: AI text sits low and even (~15-25); student writing higher and spikier.
    # Map perplexity 20 -> 100 and 60 -> 0, then pull toward human when sentences vary a lot.
    score = clamp((60 - overall) / 40) * (1 - 0.5 * clamp((burst - 40) / 60))
    return round(100 * score, 1), f"perplexity {overall:.0f}, burstiness {burst:.0f}"


def post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {body}")


def need(*names):
    vals = [os.environ.get(n) for n in names]
    if not all(vals):
        raise Skip("set " + " and ".join(names))
    return vals


def dig(data, *paths):
    """First value found at any of the dotted paths (field names vary by API version)."""
    for path in paths:
        cur = data
        for part in path.split("."):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, (int, float)):
            return cur
    keys = list(data)[:10] if isinstance(data, dict) else type(data).__name__
    raise RuntimeError(f"unrecognized response (top-level keys: {keys}); the API may have changed")


def gptzero(text):
    (key,) = need("GPTZERO_API_KEY")
    data = post("https://api.gptzero.me/v2/predict/text", {"document": text}, {"x-api-key": key})
    doc = (data.get("documents") or [{}])[0]
    score = dig(doc, "class_probabilities.ai", "completely_generated_prob")
    label = doc.get("predicted_class") or doc.get("document_classification") or ""
    mixed = (doc.get("class_probabilities") or {}).get("mixed")
    note = f"class: {label}" + (f", mixed {pct(mixed):.0f}%" if mixed is not None else "")
    return pct(score), note


def originality(text):
    (key,) = need("ORIGINALITY_API_KEY")
    data = post("https://api.originality.ai/api/v1/scan/ai", {"content": text}, {"X-OAI-API-KEY": key})
    return pct(dig(data, "score.ai", "ai.score", "results.ai.confidence.AI", "ai")), ""


def copyleaks(text):
    email, key = need("COPYLEAKS_EMAIL", "COPYLEAKS_API_KEY")
    token = post("https://id.copyleaks.com/v3/account/login/api", {"email": email, "key": key}, {})["access_token"]
    data = post(f"https://api.copyleaks.com/v2/writer-detector/{uuid.uuid4().hex}/check",
                {"text": text, "sandbox": False}, {"Authorization": f"Bearer {token}"})
    return pct(dig(data, "summary.ai")), ""


def sapling(text):
    (key,) = need("SAPLING_API_KEY")
    data = post("https://api.sapling.ai/api/v1/aidetect", {"key": key, "text": text, "sent_scores": False}, {})
    return pct(dig(data, "score")), ""


def winston(text):
    (key,) = need("WINSTON_API_KEY")
    data = post("https://api.gowinston.ai/v2/ai-content-detection", {"text": text, "sentences": False},
                {"Authorization": f"Bearer {key}"})
    human = float(dig(data, "score"))  # Winston reports a human score, always 0-100
    return round(100 - human, 1), f"human score {human:.0f}"


def pangram(text):
    (key,) = need("PANGRAM_API_KEY")
    try:
        from pangram import Pangram
    except ImportError:
        raise Skip("pip install pangram-sdk")
    data = Pangram(api_key=key).predict(text)
    if not isinstance(data, dict):
        data = getattr(data, "__dict__", {})
    label = data.get("prediction_short") or data.get("prediction") or ""
    return pct(dig(data, "fraction_ai", "ai_likelihood")), f"class: {label}" if label else ""


DETECTORS = {"gptzero": gptzero, "originality": originality, "copyleaks": copyleaks,
             "sapling": sapling, "winston": winston, "pangram": pangram}


def run(name, fn, text):
    try:
        score, note = fn(text)
        return {"detector": name, "ai": score, "note": note}
    except Skip as e:
        return {"detector": name, "skipped": str(e)}
    except Exception as e:  # network/auth/format errors: report, keep going
        return {"detector": name, "error": f"{type(e).__name__}: {e}"[:240]}


def summarize(rows):
    scores = [r["ai"] for r in rows if "ai" in r]
    if not scores:
        return {}
    ai = sum(s >= 60 for s in scores)
    human = sum(s < 40 for s in scores)
    spread = max(scores) - min(scores)
    if len(scores) == 1:
        agreement = "only one score; nothing to compare"
    elif spread >= 50:
        agreement = f"detectors disagree strongly ({spread:.0f}-point spread); trust no single number"
    elif ai == len(scores):
        agreement = "all lean AI"
    elif human == len(scores):
        agreement = "all lean human"
    else:
        agreement = f"{ai} lean AI, {human} lean human, {len(scores) - ai - human} in between"
    return {"count": len(scores), "median": round(statistics.median(scores), 1),
            "mean": round(statistics.mean(scores), 1), "min": min(scores), "max": max(scores),
            "agreement": agreement}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="text file, or - for stdin")
    ap.add_argument("--services", default="",
                    help="comma-separated detectors to send the text to, or 'all' (default: none)")
    ap.add_argument("--perplexity", action="store_true", help="add local GPT-2 perplexity")
    ap.add_argument("--rubric", type=float, help="add the ai-check rubric score (0-100)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args()

    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    wanted = SERVICES if args.services.strip() == "all" else [s.strip().lower() for s in args.services.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in DETECTORS]
    if unknown:
        ap.error(f"unknown service(s): {', '.join(unknown)}. Choose from: {', '.join(SERVICES)}")

    rows = [run("style (local)", style, text)]
    if args.perplexity:
        rows.append(run("perplexity (local)", perplexity, text))
    if args.rubric is not None:
        rows.append({"detector": "ai-check rubric", "ai": round(clamp(args.rubric, 0, 100), 1), "note": ""})
    rows += [run(name, DETECTORS[name], text) for name in wanted]
    summary = summarize(rows)

    if args.json:
        print(json.dumps({"words": len(text.split()), "results": rows, "summary": summary}, indent=2))
        return

    if len(text) < 300:
        print("Warning: under ~300 characters; most detectors are unreliable on text this short.\n")
    print(f"{'Detector':<20} {'AI likelihood':>13}   Notes")
    for r in rows:
        if "ai" in r:
            print(f"{r['detector']:<20} {r['ai']:>12.0f}%   {r['note']}")
        else:
            print(f"{r['detector']:<20} {'--':>13}   {'skipped: ' + r['skipped'] if 'skipped' in r else 'error: ' + r['error']}")
    if summary:
        print(f"\nMedian {summary['median']:.0f}%  |  mean {summary['mean']:.0f}%  |  "
              f"range {summary['min']:.0f}-{summary['max']:.0f}%  |  {summary['count']} score(s)")
        print(f"Agreement: {summary['agreement']}")
    print("\nThe median is the better single number: one outlier moves it less than the mean."
          "\nDetectors measure different things and share false positives; none proves authorship.")


if __name__ == "__main__":
    main()
