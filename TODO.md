# Meshsites — feature todo

Running list of features. Check items off as they land; add new ideas at the
bottom of the relevant section.

## Done

- [x] Protocol v1 (draft 7) server: beacons, chunked DEFLATE responses,
      etag revalidation, response cache, BUSY/dup handling, hop_limit=1 hygiene
- [x] Static `.md` pages and dynamic `.py` handlers (GET/POST)
- [x] Sanitized, size-bounded storage helper (`meshsites.Store`)
- [x] CLI: `meshsites serve` / `meshsites init`, serial autodetect, `--tcp`
- [x] Access and error logging: one access line per request (sender, method,
      path, outcome, duration) incl. rejected/malformed requests; `--log-file`

## Planned

- [ ] Access-log test coverage (assert log lines in test_server)
- [ ] Meshdown lint: warn at startup about pages that exceed the compressed
      cap or use malformed form blocks
- [ ] Stats page example handler (requests served, top pages) fed from the
      access log
- [ ] Client / browser CLI (`meshsites browse`) — discovery list, fetch,
      render, cache with etag revalidation
- [ ] systemd unit + install docs polish

## v2 (spec-gated, out of scope for now)

- [ ] Chunk re-request (selective NACK)
- [ ] Binary resource frames (images)
- [ ] Multi-packet requests
