# Rating model

AOS uses a required five-star integer scale:

- Minimum: 1
- Maximum: 5
- Increment: 1 whole star

Boolean values, NaN, infinity, floats with fractional increments, negative values and values over five are rejected as `INVALID_RATING`.

Ad and seller aggregates are calculated from canonical `Approved` review rows only. Values are rounded to two decimal places. Zero-review targets store and return `0.0` and `0`. Clients cannot submit averages, counts or rating distributions.
