#!/usr/bin/env python3
"""Check Common App activity/honors entries against character limits.

Input: a text/markdown file with one entry per line, prefixed by its field:
    position: Captain (11-12); Varsity Starter (10)
    org: Lincoln High School Robotics Team
    desc: Designed drivetrain for 18-member team; robot placed 4th of 62 at state.
    honor: National Merit Commended Student
Blank lines separate activities. Lines without a known prefix are ignored,
so notes and headings can live in the same file.

Usage: python3 activity_check.py essays/activities.md
"""
import re
import sys

LIMITS = {"position": 50, "org": 100, "desc": 150, "honor": 100}
WEAK_STARTS = ("i ", "i'm", "member", "participated", "helped", "was ", "responsible for")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    activities = honors = over = 0
    in_activity = False
    for lineno, raw in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
        line = raw.strip()
        if not line:
            in_activity = False
            continue
        m = re.match(r"^[-*\s]*(position|org|desc|honor)\s*:\s*(.*)$", line, re.I)
        if not m:
            continue
        field, text = m.group(1).lower(), m.group(2).strip()
        if field == "honor":
            honors += 1
        elif not in_activity:
            activities += 1
            in_activity = True
        n, limit = len(text), LIMITS[field]
        status = "OK " if n <= limit else "OVER"
        over += n > limit
        notes = []
        if n > limit:
            notes.append(f"cut {n - limit}")
        if field == "desc":
            if text.lower().startswith(WEAK_STARTS):
                notes.append("start with a strong verb")
            if not re.search(r"\d", text):
                notes.append("no numbers: add scale/results?")
            if n < 90:
                notes.append(f"{limit - n} chars unused")
        print(f"{status} L{lineno:<3} {field:<8} {n:>3}/{limit}  {text[:70]}{'…' if len(text) > 70 else ''}"
              + (f"\n{'':>18}-> {'; '.join(notes)}" if notes else ""))
    print(f"\n{activities} activities (max 10), {honors} honors (max 5), {over} over limit.")
    if activities > 10 or honors > 5:
        print("Too many entries: trim to the strongest.")


if __name__ == "__main__":
    main()
