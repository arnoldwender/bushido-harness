# The Precepts

> The first utterance of the Bushido Harness. Before any agent acts, the harness opens with
> a fixed precept and then a rotating *precept of the day* from the samurai canon — Musashi,
> Hagakure, and the strategists. A word to steady the hand before the work.

## The opening precept (fixed)

```text
"Today is victory over yourself of yesterday."
                          — Miyamoto Musashi
```

## The rotating precept

Beneath the fixed opening, the harness prints one rotating line from [`precepts.txt`](precepts.txt)
— a precept of the day, changing daily. The opening never changes; the precept rotates. Edit
[`precepts.txt`](precepts.txt) (one `Precept — Author` per line) to curate or extend the pool.

> **This document used to say "All sources are public-domain." That was wrong, and the audit
> of 2026-09-10 found it.** The Japanese and Chinese originals are all long out of copyright;
> the **English translations are separate works with their own terms**, and several of them are
> still running. Eight of the eleven lines are affected. Every line is now backed by a file in
> [`sources/`](sources/) that shows the arithmetic per jurisdiction, and
> [`gate/citations.py`](gate/citations.py) fails the build if any one of them loses its source.
>
> | Lines | English | Status |
> | --- | --- | --- |
> | 5 × Musashi | as circulated from Victor Harris (1974) or translator unidentified | **not clear** — Harris died 2017 |
> | 2 × Sun Tzu | Lionel Giles, 1910 | public domain in the **US**; **not in the EU until 2029** — Giles died 1958 |
> | Lao Tzu, *overcomes himself* | James Legge, 1891 | **public domain everywhere** — Legge died 1897 |
> | Lao Tzu, *thousand miles* | no published translation matches it | **unattributed** |
> | Hagakure | William Scott Wilson, 1979 | **in copyright** — Wilson is living |
> | Yagyū Munenori | modern; attributed to Thomas Cleary | **in copyright** — every candidate is |
>
> Arnold publishes from Germany, so the EU term is the one that binds. Marking this openly is
> **誠 Makoto 3** — *name what you could not verify; the unconfirmed never wears the clothes of a
> checked fact.* Deciding what to do about it is a separate, deliberate act, and it has not been
> taken yet. The pool below is unchanged.

The current pool:

> - *Today is victory over yourself of yesterday.* — Miyamoto Musashi
> - *Accept everything just the way it is.* — Miyamoto Musashi
> - *Think lightly of yourself and deeply of the world.* — Miyamoto Musashi
> - *The Way is in training.* — Miyamoto Musashi
> - *Do nothing which is of no use.* — Miyamoto Musashi
> - *If you know the enemy and know yourself, you need not fear the result of a hundred battles.* — Sun Tzu
> - *Supreme excellence consists in breaking the enemy's resistance without fighting.* — Sun Tzu
> - *He who overcomes others is strong; he who overcomes himself is mighty.* — Lao Tzu
> - *A journey of a thousand miles begins with a single step.* — Lao Tzu
> - *Matters of great concern should be treated lightly.* — Yamamoto Tsunetomo
> - *To fix the mind obsessively on anything is considered sickness.* — Yagyu Munenori

*The harness emits this first, on startup — [`bin/precept`](bin/precept).*
