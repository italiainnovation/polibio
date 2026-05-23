# The interlocutor library

This is how the agents learn your field's **way of seeing** — not its conclusions.

An interlocutor library holds the canonical works of the traditions your analysis applies. An agent reads them to learn *how* a scholar identifies a phenomenon in unfamiliar evidence — the perceptual move, not the summary. A researcher who has read the source classifies by recognition, not by matching a definition; the library is the system's equivalent of that training.

## How it works

1. Choose, per analytical move (see `CLAUDE.md` §3), the one or two works that *teach the move* — the paper a doctoral student would read to learn to do it, not a textbook summary.
2. Place the texts under this folder, grouped by tradition, and list them in `manifest.md` (template below).
3. Point the relevant Layer-2 agent at its tradition's folder.

The agent applies the **method** to your evidence. It never attributes a finding to an interlocutor; the scholar's name appears only in methodological citation.

## Do not commit copyrighted PDFs

This repository ships the **mechanism**, not anyone's library. Keep PDFs out of version control (they are git-ignored). Share a `manifest.md` of *what to read*, not the files.

## `manifest.md` template

```
# Interlocutor library — manifest

## <Tradition A>   (teaches: <which analytical move>)
- <Author, Year, "Title">. <one line: the move this text teaches>

## <Tradition B>   (teaches: <move>)
- <Author, Year, "Title">. <…>
```
