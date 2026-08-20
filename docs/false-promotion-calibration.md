# Campaign false-promotion calibration

`CampaignFalsePromotionPolicy` controls multiplicity at two levels. Candidate
index `i` receives
`familywise_alpha * (1 - allocation_decay) * allocation_decay**i`; those
allocations sum to at most the campaign target even when challengers are
selected adaptively. Within a candidate, the allocated probability is divided
over every predeclared confirmation look. Observed or rejected candidates do
not refund alpha, and the ledger is written before evaluation so restart or a
new generator cannot reset the budget.

Repeated seeds with one `fixture_digest` are one dependence block. Screen,
confirmation, and held-out blocks must be disjoint. The default `cluster_t`
method applies a small-sample t interval to fixture-block means and assumes
those means are exchangeable with finite variance. It is the higher-power
default for ordinary matched evaluation. `bounded_hoeffding` instead requires
predeclared finite effect bounds and supplies a distribution-free option for
bounded skewed or heavy-tailed effects; it is deliberately more expensive.

## Deterministic calibration

`simulate_false_promotion_campaigns` exercises the whole adaptive sequence,
not one isolated candidate. With seed 986, 1,000 campaigns, eight possible
challengers, family-wise alpha 0.05, and allocation decay 0.5, the checked-in
calibration produced:

| Case | Method | Independent blocks/candidate | Campaign promotion rate | Average candidates |
| --- | --- | ---: | ---: | ---: |
| Null normal | cluster t | 12 | 0.012 | 7.922 |
| Null clustered fixtures | cluster t | 12 | 0.012 | 7.922 |
| Clear win, effect 0.40 | cluster t | 12 | 1.000 | 1.000 |
| Near tie, effect 0.03 | cluster t | 12 | 0.058 | 7.656 |
| Heteroskedastic win, effect 0.30 | cluster t | 24 | 1.000 | 1.004 |
| Null bounded heavy tail | bounded Hoeffding | 128 | 0.000 | 8.000 |
| Bounded heavy-tail win, effect 0.35 | bounded Hoeffding | 128 | 1.000 | 1.005 |

The null rows are the false-promotion checks; positive-effect rows measure
power, not type-I error. These finite simulations are regression calibration,
not a mathematical replacement for the union-bound guarantee. The robust row
also illustrates its cost: 128 independent fixture blocks, rather than
repeated seeds, were required for strong power at the declared `[-1, 1]`
effect bounds. The test suite reruns a smaller deterministic sample and fails
if null error exceeds the configured target or clear-win power materially
regresses.
