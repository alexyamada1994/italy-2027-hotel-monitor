# Hotel price monitor — Italy, May 2027

Monitors 6 legs / 16 nights against the Scrappa Google Hotels API, on a 10h
cadence, inside the 500-credit free tier.

```bash
python run_cycle.py            # one cycle, prints the run JSON to stdout
python run_cycle.py --dry-run  # show planned queries, spends no credits
```

Layout: `monitor/config.py` (trip, zones, bands, quota) · `geo.py` (zone
distances) · `source.py` (API client + credit charging) · `state.py`
(snapshot, ledger, append-only history) · `core.py` (normalisation,
constraints, deltas, alerts) · `run_cycle.py` (orchestration).

State lives in `state/`: `credits.json`, `snapshot.json`, `history.ndjson`,
`runs.ndjson`. History is append-only and never rewritten.

## Deviations from the original spec

These are places where the source did not behave as the spec assumed. Each was
verified against the live API.

**1. The six mandated query strings return nothing.** `"hotels Rome city
center"` yields `properties: []` — not a date-horizon effect, it is empty for
near-term dates too. Zone-anchored queries (`"Fontana di Trevi, Rome"`) return
~20 properties that are essentially all inside the target radius, because
Google does the geo-targeting server-side.

**2. Zone filtering needs anchored queries, not a city-wide page.** A city-wide
query returns 20 of ~15,000 properties ranked by metro-wide relevance. Measured
yield of in-zone *and* in-band properties was 0/20 for Rome, Florence and
Venice — the "<3 in_band options" alert would have fired permanently. With
anchored queries the same legs return 26 / 7 / 3 candidates.

To cover every centroid at one listing per leg, `anchors` rotate across cycles
(Spagna → Trevi → Pantheon → Barberini). At ~73 cycles/month each centroid is
sampled many times. This keeps the cycle at 6 credits and the cadence at 10h.
Rotation is why the snapshot **accumulates** instead of being rebuilt each run:
a hotel outside this cycle's sample has not disappeared.

**3. Free cancellation is not obtainable.** Absent from the listing, and a
detail call on `property_token` returned no cancellation or refund field at
all. `free_cancellation_until` is therefore always `null` and
`"free_cancellation"` is always in `unverified_constraints`. As agreed, it is
**excluded from the `new_in_band` alert gate** — leaving it in would mean that
alert could never fire for any property, ever.

**4. Detail calls buy nothing; the budget went to listings.** The detail
response carries the same `amenities`/`excluded_amenities` as the listing and
an empty `prices`. AC, parking and breakfast are all resolvable from the
listing, so `DETAIL_CREDITS = 0` and all 500 credits fund listings.
`pending_validation` is consequently always empty.

**5. `rate_name` has no source equivalent.** No vendor rate names are exposed
(`prices` came back empty), so `rate_name` records *which figure was read*
rather than how a vendor names its rate. It is constant, so `rate_changed` is
always `false` — the delta audit the spec wanted is not available here.

**6. City tax is not in the feed.** The gap between `lowest` and
`before_taxes_fees` was ~0.5% (EUR 102 vs 101.51), nowhere near Rome's ~EUR 15
per night for two adults. It is applied from the static table in
`config.CITY_TAX_PER_PERSON_PER_NIGHT` and reported separately in
`city_tax_eur`, so the estimate never hides inside the source price. **Verify
these rates before relying on them.**

**7. `"Val Gardena"` returns zero properties** — it is a valley, not a
destination. The three localities (Ortisei, Selva, Santa Cristina) rotate as
anchors instead.

**8. Output shape.** One run object per cycle with a `legs[]` array, rather
than the singular `leg_id`/`city` template, so run-level counters and `errors`
are not duplicated six times. Chosen because you had no preference.

## Rating floor

Minimum guest rating is **4.5+**, applied server-side via the API's `rating`
parameter. It takes a coded value, not a number: `7` = 3.5+, `8` = 4.0+,
`9` = 4.5+ — a literal `4.5` is rejected with HTTP 422. Set
`config.MIN_RATING_CODE` to change it.

Filtering at the source is what makes the bar affordable. A page is 20
properties either way, so asking for 4.5+ returns 20 qualifying hotels per
credit instead of ~8 survivors out of 20. Measured cost of raising the floor:
the cheapest full trip moved from EUR 3,273 to EUR 3,276, while the tracked set
halved from 91 properties to 47.

**A rating is only as good as its evidence.** A 5.0 from 3 reviews is not a
recommendation. Properties below `MIN_REVIEWS_FOR_CONFIDENCE` (30) are flagged
`low_confidence`, demoted in the tiebreak so a thin 4.8 never outranks a
well-reviewed 4.6, and badged on the dashboard. **Nothing is ever dropped for
it** — the flag informs, it does not filter.

The dashboard applies the current floor at presentation time and hides
properties not re-observed within `STALE_AFTER_DAYS` (14), so a price from
weeks ago cannot keep anchoring the headline total. History keeps everything;
only the view is filtered.

## Hostels

Excluded by name match (`config.EXCLUDE_NAME_PATTERNS`), not by the API.

The source reports hostels as `type: "hotel"`, so the type field cannot
separate them. The API's `property_types` parameter *is* honoured, but it is an
include-list — excluding one category would mean enumerating every other code,
discovered by trial and fragile to change. Name matching is deterministic,
costs no credits, and is auditable.

Patterns are word-boundary anchored, verified against all 274 distinct property
names captured so far: 7 matched (all genuine hostels), with `Hostellerie du
Lac`, `Hotel Ostella`, `Hostal Barcelona` and `Hosteria del Mar` correctly
kept. The filter applies both at collection time and in the dashboard, so
hostels tracked before it existed drop out of the view.

In practice they were never competing for a recommendation — the ones seen
priced at EUR 36–78/night against band floors of EUR 150–220, so they could
only ever land in `below_band`.

## Known behaviour, not bugs

- **Unpriced properties.** Many properties return no price this far out — 17/20
  in Selva, 15/18 in Milan for the single night of 28 May. They are recorded in
  `excluded` with reason `no price returned by source`, and should thin out as
  the dates approach.
- **Legs 4 and 5 are frequently empty.** Dolomite hotels open inventory late;
  the `<3 in_band` alert is suppressed there until 2027-02-01.
- **Vacation rentals are included.** Many Rome and Florence hits are apartments
  or B&Bs (`property_type` records this). The spec never excluded them; filter
  on `property_type` if you want hotels only.

## Cold start

The ledger starts at zero only when there is no snapshot *and* no history. If
the counter is missing while other state exists, that is treated as loss and
the agent assumes quota exhaustion until the 1st-of-month reset. The ledger is
charged before each request and flushed immediately, since under-counting is
what silently overruns the quota.
