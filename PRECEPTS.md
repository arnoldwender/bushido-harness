# The Precepts

> The first utterance of the Bushido Harness. Before any agent acts, the harness opens with
> a fixed precept and then a rotating *precept of the day* from the samurai canon — Musashi,
> Hagakure, and the strategists. A word to steady the hand before the work.

## The opening precept (fixed)

```text
今日は昨日の我に勝ち
(editor's gloss: today, win against the self of yesterday)
                          — Miyamoto Musashi, Go Rin no Sho (1645)
```

Copied from what [`bin/precept`](bin/precept) actually prints, not written to match it.
The two drifting apart is the exact defect the 2026-08 audit found in this repo.

## The rotating precept

Beneath the fixed opening, the harness prints one rotating line from [`precepts.txt`](precepts.txt)
— a precept of the day, changing daily. The opening never changes; the precept rotates. Edit
[`precepts.txt`](precepts.txt) (one `Precept — Author` per line) to curate or extend the pool.

### Why the pool is in Japanese

**This document used to say "All sources are public-domain." That was false for eight of
the eleven lines, and the audit of 2026-09-10 found it.**

The trap is worth naming, because it catches anyone building from an old canon: the
Japanese and Chinese **originals** left copyright centuries ago — the **English translation
is a separate work with its own term**, running from the translator's death. Arnold
publishes from Germany, so the binding rule is life + 70, not the US publication rule.
*A text can be free while its famous English rendering is still owned.*

| Was | Translator | Now |
| --- | --- | --- |
| 5 × Musashi | as circulated from Victor Harris, 1974 († 2017 — EU term to **2088**) | original + **editor's gloss** |
| 2 × Sun Tzu | Lionel Giles, 1910 († 1958 — EU term to **2029**) | **E. F. Calthrop, 1908** († 1915) |
| Lao Tzu, *overcomes himself* | James Legge, 1891 († 1897) | unchanged — already free |
| Lao Tzu, *thousand miles* | matched **no** published translation | **Legge's actual wording**, 道德經 64 |
| Hagakure | William Scott Wilson, 1979 (**living**) | original + gloss, **reattributed** |
| Yagyū Munenori | modern; Cleary († 2021) and every other candidate | original + gloss |

Two of those are more than a licence fix.

**The *thousand miles* line was never a translation.** 千里之行，始於足下 says the journey
begins *beneath one's feet* — there is no "mile" and no "single step" in it. Legge writes
*"The journey of a thousand li commenced with a single step."* That is now the line.

**The Hagakure maxim was attributed to the wrong man.** In Book One, Yamamoto Tsunetomo is
*quoting* a maxim from the wall of **Nabeshima Naoshige**, with Ittei's reply after it. The
pool credits Naoshige.

Where no free English exists, the entry prints the **original** and marks the English as
`editor's gloss` — Arnold's own words, not a quotation put in the mouth of a translator who
never wrote it. Every line is backed by a file in [`sources/`](sources/) showing the
arithmetic per jurisdiction, and [`gate/citations.py`](gate/citations.py) fails the build if
any one loses its source.

Saying all this out loud is **誠 Makoto 3** — *name what you could not verify; the
unconfirmed never wears the clothes of a checked fact.*

The current pool:

> - 今日は昨日の我に勝ち *(editor's gloss: today, win against the self of yesterday)* — Miyamoto Musashi, Go Rin no Sho (1645)
> - 世々の道をそむく事なし *(editor's gloss: never turn your back on the Ways handed down through the ages)* — Miyamoto Musashi, Dokkodo (1645)
> - 身をあさく思世をふかく思ふ *(editor's gloss: think of yourself shallowly and of the world deeply)* — Miyamoto Musashi, Dokkodo (1645)
> - 道の鍛錬する所 *(editor's gloss: the tempering of the Way by training)* — Miyamoto Musashi, Go Rin no Sho (1645)
> - 役に立ぬ事をせざる事 *(editor's gloss: not doing what serves no purpose)* — Miyamoto Musashi, Go Rin no Sho (1645)
> - *He who knows both sides has nothing to fear in a hundred fights.* — Sun Tzu, The Art of War, tr. E. F. Calthrop (1908)
> - *To fight and conquer one hundred times is not the perfection of attainment, for the supreme art is to subdue the enemy without fighting.* — Sun Tzu, The Art of War, tr. E. F. Calthrop (1908)
> - *He who overcomes others is strong; he who overcomes himself is mighty.* — Lao Tzu, Tao Te Ching 33, tr. James Legge (1891)
> - *The journey of a thousand li commenced with a single step.* — Lao Tzu, Tao Te Ching 64, tr. James Legge (1891)
> - 大事の思案は軽くすべし *(editor's gloss: weigh a great matter lightly)* — Nabeshima Naoshige, wall maxim recorded in Hagakure (c. 1716)
> - 何事も心の一筋にとどまりたるを病とするなり *(editor's gloss: whatever the thing, a mind that lodges on it is what is called sickness)* — Yagyū Munenori, Heihō Kadensho (1632)

*The harness emits this first, on startup — [`bin/precept`](bin/precept).*
