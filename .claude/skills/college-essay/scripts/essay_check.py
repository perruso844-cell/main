#!/usr/bin/env python3
"""Word count + quick flags for a college essay draft.

Usage: python3 essay_check.py essay.txt [--limit 650]
       cat essay.txt | python3 essay_check.py - --limit 350
"""
import argparse
import re
import sys
from collections import Counter

CLICHES = [
    "comfort zone", "opened my eyes", "make a difference", "ever since i was",
    "since i was young", "since i was little", "rollercoaster", "roller coaster",
    "passion for", "passionate about", "it's not about winning", "hard work pays off",
    "taught me the value", "learned the importance", "step out of", "broaden my horizons",
    "webster's dictionary", "defines", "in today's society", "in today's world",
    "at the end of the day", "everything happens for a reason", "think outside the box",
    "give back to", "well-rounded", "life-changing", "a testament to", "tapestry",
    "delve", "embark on", "journey", "plethora", "myriad", "utilize", "multifaceted",
    "profound", "unwavering", "invaluable", "foster", "ignite",
]

FILLER = [
    "very", "really", "just", "actually", "basically", "literally", "truly",
    "extremely", "in order to", "the fact that", "i think", "i feel", "i believe",
    "i realized that", "kind of", "sort of",
]

STOP = set("""a an the and or but of to in on at for with by from as is was were be been
it its this that these those i me my we our you your he she they them his her their
not no so if then than when what which who how all into out up about had have has do
did does am are can could would should will just very""".split())


def count_phrase(text_lower, phrase):
    return len(re.findall(r"(?<![a-z'])" + re.escape(phrase) + r"(?![a-z'])", text_lower))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="path to essay text, or - for stdin")
    ap.add_argument("--limit", type=int, default=None, help="word limit")
    args = ap.parse_args()

    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    words = re.findall(r"[A-Za-z0-9'’-]+", text)
    n = len(words)
    lower = text.lower().replace("’", "'")

    print(f"Words: {n}   Characters: {len(text)}   Paragraphs: {len([p for p in text.split(chr(10)*2) if p.strip()])}")
    if args.limit:
        diff = args.limit - n
        status = f"{diff} under limit" if diff >= 0 else f"OVER by {-diff} — cut needed"
        print(f"Limit: {args.limit}   ({status})")

    sentences = [s for s in re.split(r"[.!?]+\s", text) if s.strip()]
    if sentences:
        lens = [len(s.split()) for s in sentences]
        print(f"Sentences: {len(sentences)}   avg {sum(lens)/len(lens):.1f} words   longest {max(lens)}")

    def report(title, phrases):
        hits = [(p, c) for p in phrases if (c := count_phrase(lower, p))]
        if hits:
            print(f"\n{title}:")
            for p, c in sorted(hits, key=lambda x: -x[1]):
                print(f"  {p!r} x{c}")

    report("Cliché / inflated phrases", CLICHES)
    report("Filler / hedging", FILLER)

    i_count = sum(1 for w in words if w.lower() == "i")
    if n:
        print(f"\n'I' count: {i_count} ({100*i_count/n:.1f}% of words)")
    em = text.count("—") + text.count(" -- ")
    if em > 3:
        print(f"Em dashes: {em} (consider fewer)")

    content = Counter(w.lower() for w in words if w.lower() not in STOP and len(w) > 3)
    repeats = [(w, c) for w, c in content.most_common(10) if c >= 4]
    if repeats:
        print("\nMost repeated words:", ", ".join(f"{w} x{c}" for w, c in repeats))


if __name__ == "__main__":
    main()
