# simulator — shared

**Owner:** Parth Gala · **Status:** not started

A fake store: demand curves, sales events, competitor price movement, inventory drawdown.

Not throwaway — this becomes the `gymnasium` environment the RL agent trains in. It is also
what makes results reproducible: the evaluation chapter needs deterministic seeded runs, which
a live scraped feed can never give you.

Replaces the SerpAPI scraping from the 295A prototype for all development and evaluation.
