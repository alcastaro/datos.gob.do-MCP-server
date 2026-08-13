# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.14.0] — 2026-08-13

The version in `pyproject.toml` and `__init__.py` reads **0.14.0**, not 0.13.1,
because the listing-envelope change below breaks a caller reading
`structuredContent.result`. It is a minor bump rather than a patch by that fact
alone. It was raised as soon as the breaking change landed rather than at release
time: leaving it at 0.13.0 meant two different trees both answering `--version`
with `0.13.0`, one of them with a different interface — which is exactly the
confusion the new `--version` flag exists to end. The bump also changes
`_parser_build()`, so every Parquet written by 0.13.0 is superseded rather than
served by a reader that may treat it differently.

Two rounds of findings with one thing in common: the platform most of this
server's audience uses had never run it. The first came from re-reading the
[MCP debugging guidance](https://modelcontextprotocol.io/docs/tools/debugging)
against this server — a client-launched stdio server inherits neither a
meaningful working directory nor the operator's shell environment. The second
came from the first real Windows session, on Windows 11 build 26200 with
Defender active and a non-administrator account.

### Fixed

- **A page that opens its files from JavaScript looked like a page with no files.**
  `pagelink` read anchors only, and the Tribunal Constitucional publishes all
  three of its formats as
  `<div class="file-block" onclick="window.location.assign('…​.csv')">` — no `href`
  anywhere. So a page offering CSV, ODS and XLSX side by side was answered with
  "no data file linked on it", which was our defect reported as the publisher's.
  The navigation calls (`window.location.assign` / `.replace` / `window.open` /
  `location.href =`) and the `data-href` family of attributes are now read, scored
  by the same function as any `href`, and the declared format still breaks the tie
  between three spellings of one table. Verified live: that page now answers
  **27,759 rows × 7 columns**. Nine resources across three pages of the 209 that
  answer with a page.
- **One ragged line could silently reduce a CSV to a single column.** The worst
  shape of failure there is, because nothing fails. With `IGNORE_ERRORS` and no
  padding, a single row whose field count surprises DuckDB's sniffer makes it fall
  back to *one* column named after the entire header row — and it still returns
  every row, each one a single string. Line 1,423 of DGP's passport series does
  exactly this: 1,434 rows, all of them one field, presented as a successful read.
  `null_padding` reads the same file as its four real columns and fills the short
  row with NULLs, which is a fact about that row rather than a verdict on the file.
- **The catalog's declared format was trusted in one direction and refused in the
  other.** The magic-byte correction answered `PK` → XLSX, but `PK` is how every
  ODS starts too, so an ODS registered as CSV went to `read_xlsx` and came back as
  "No [Content_Types].xml found in xlsx file" — a sentence about this server's
  internals, about a file whose own name ended in `.ods`. The reverse case was not
  handled at all: a CSV registered as ODS was refused for not starting like a
  spreadsheet, which describes the declaration and not the file. The container is
  now identified from what is inside it — the `mimetype` member for ODS, a
  workbook part for XLSX — and a text file is recognised as CSV or JSON when the
  declared spreadsheet reader has already refused it. Verified live: the Tribunal
  Constitucional's `mayo-2026.ods` reads as 160 × 3 with its real headers, and
  DGP's series as 1,434 × 4. 24 resources in the sibling corpus.
- **A ZIP holding one data file is an archive, not a workbook.** Three MIVHED
  resources are declared JSON and are a zipped-up `.json`. The single member is
  unpacked and read — after the digest is taken, because whoever re-downloads the
  URL gets the archive, so that is what `source_sha256` has to name. A ZIP with
  several data files is left alone: which one is "the data" is not something to
  guess.
- **A pre-2007 `.xls` now says what it is.** `d0cf11e0` is OLE2, and `read_xlsx`
  cannot read BIFF — it complains about a missing ZIP member, which reads like
  corruption. `xls` is the worst-served format in the catalog (12 of 22 readable);
  the ones that stay unreadable at least say why, and what to ask the publisher
  for.
- **A clearer lock error had become an unhandled one.** Naming the cache-lock
  timeout `CacheLockError` was right, and it moved the failure *out* of
  `_ENVELOPE_ERRORS`: the old `OSError: [Errno 36]` was in that tuple, so a
  contended index used to come back as `{"error": ...}` and now arrived as a
  protocol-level traceback. `clear_cache` was the exposed path — it is
  synchronous, so `_tool_envelope`, which only wraps coroutines, never covered it
  either. The type is in the tuple and `clear_cache` catches it directly. Windows
  only, because POSIX `flock` queues and never times out; that is, it fired only
  on the platform the clearer message was written for.
- **A Parquet the index never heard about was invisible to the size ceiling.**
  `finalize` is deliberately best-effort — a lock it cannot take must not turn a
  correct answer into a failed call — but that leaves a valid Parquet with no
  index entry. Eviction and `stats()` both walked the index alone, so such a file
  could not be reclaimed and was not counted: the cache could pass its 1 GB cap
  with `get_cache_stats` reporting less than `du` and nothing ever bringing it
  back down. Both now see the files on disk, and an orphan evicts first — it has
  no access time and cannot be served, since a hit needs the index. The same hole
  opened without Windows in the picture, because `put_path` records only in
  memory: a process that died between writing the Parquet and calling `finalize`
  left the identical orphan. `get_cache_stats` reports `orphan_entries`, since a
  non-zero value means contention rather than a healthy cache.
- **A clean clone could not run the suite.** `pytest`, `pytest-asyncio` and
  `pytest-httpx` were declared only in the `dev` *extra*, while `uv sync`
  installs *groups* by default — so `uv sync && uv run pytest` produced dozens of
  `fixture 'httpx_mock' not found`, and a tester following the README had every
  reason to conclude the server was broken on Windows. The three now sit in
  `[dependency-groups].dev` as well, so the documented commands work with no
  flags to remember. The extra stays for `pip install .[dev]`.
- **A legitimate skip on Windows counted as an error.** The `O_NOFOLLOW` test
  called `pytest.skip()` from inside its body, after the `mock_csv_endpoint`
  fixture had already registered two mocked responses; unrequested mocks fail
  `pytest-httpx` at teardown. The skip is now a decorator, so Windows reports
  `518 passed, 5 skipped` with no error — the same 519 outcomes as macOS, one
  moved from passed to skipped because the platform has no `O_NOFOLLOW`.
- **A test would have expanded `~` to the real profile on Windows.** It patched
  `HOME`, which `ntpath.expanduser` never reads — it reads `USERPROFILE`. Found
  before it shipped, by that Windows report arriving the same night.
- **Four ways past the Windows path denylist, all closed.** Testing on Windows
  found what reasoning about it had not. `\\?\C:\Windows\…` and
  `//?/C:/Windows/…` — the extended-length prefix — passed, and the usual defence
  of checking the raw path alongside the resolved one does not help there:
  `Path.resolve()` keeps the prefix, unlike the /etc → /private/etc case on macOS
  that trick was written for. The administrative shares
  (`\\localhost\C$\Windows`, `\\127.0.0.1\ADMIN$`) reached the
  same directory over UNC. And the list hard-coded the drive letter `c:`, so on a
  machine with Windows installed on another drive there was no protection at all.
  UNC destinations are now refused wholesale — a CSV a person will open does not
  need to be written to a host they did not name — and the system directories are
  matched behind any drive letter.
- **The temp-directory exception can no longer switch that denylist off.** The
  exception exists because macOS puts the per-user temp dir under
  /private/var/folders, which the POSIX list covers. It applied to any temp dir,
  and `TEMP` is `C:\Windows\Temp` for the SYSTEM account and some services —
  disabling the guard precisely where it matters most.
- **The Windows cache lock waits properly instead of dying.** `msvcrt.locking`'s
  blocking mode retries once a second, ten times, and does not queue, so a waiter
  can watch the holder reacquire on every retry. Measured on real hardware: two
  writers doing 200 entries each produced one 6.2 s wait against that ~10 s
  ceiling, and at four writers two processes died with
  `OSError: [Errno 36] Resource deadlock avoided`. It now uses the non-blocking
  mode with exponential backoff and jitter — 44 to 48 attempts in ten seconds
  where `LK_LOCK` managed about ten. The ten-second give-up is kept deliberately,
  but raises `CacheLockError` naming the index and the way out
  (`DATOSGOBDO_CACHE_DIR`) rather than an errno from inside the standard library.

  **What this does not do is shorten the waits, and the second Windows run
  measured that:** two writers waited 6,209 ms at worst before the change and
  6,251 ms after — the same figure. Retry granularity decides how quickly you take
  the lock once it is free; it cannot change how long the other process holds it.
  Four writers now reach ~10 s of waiting and none of them die, which is the whole
  of the improvement: a process that used to be killed now skips one index entry
  and says so.
- **An eviction that cannot free space now says so.** Windows refuses to delete a
  file another process holds open, so a reader can pin every eviction candidate at
  once: measured on Windows 11, 60 pinned Parquet files held the cache at 122,000
  bytes against a 5,000-byte cap — **24× over the limit, with nothing logged
  anywhere**. It recovers by itself the moment the reader lets go, so the defect
  was never the size; it was that the size had no explanation, which turns "the
  cache is 20 GB and I do not know why" into an unanswerable question. One warning
  per pass now names how far over the limit it is, how many files could not be
  deleted and how many bytes they hold. Retrying was considered and rejected: it
  cannot help while the read continues, and the grace period already covers the
  ordinary case.
- **A network destination is refused as a network destination.** `save_query_to_csv`
  reported `\\servidor\equipo\salida.csv` as a "system path", which an ordinary
  company share is not — the reader then looks for the problem where it is not, and
  never learns the actual policy. UNC paths, including admin shares and the
  `\\?\UNC\` spelling, now get their own message and a hint saying to write
  locally and copy afterwards.
- **A lock timeout no longer discards work already done.** `finalize` and `touch`
  are bookkeeping: by the time they run the Parquet is written and correct, so a
  lock they cannot get is logged and skipped rather than turned into a failed
  tool call. The cost is one re-download later. `evict_to_fit` still raises,
  because silently not enforcing a size ceiling is how a cache eats a disk.
- **The cache index is written as UTF-8 explicitly.** `read_text()`/`write_text()`
  with no encoding means the ANSI codepage on Windows (cp1252 on a Spanish
  install). It happens to be harmless today because `json.dumps` defaults to
  `ensure_ascii=True` — one flag away from an index that corrupts on one platform
  and nowhere else.
- **The pydantic_settings start-up warning is actually filtered now.** The filter
  also required the warning to be attributed to `pydantic_settings.*`, and
  `module` matches the module a warning is attributed to, which follows the
  stacklevel the emitting library passes — here the `mcp` module defining the
  settings class. So a Windows tester saw `IncompleteFieldDefinitionWarning` as
  their first impression of the server while a test asserted it was suppressed:
  the test attributed its synthetic warning by hand, so code and test agreed with
  each other and neither agreed with a real start-up.
- **A relative `dest` in `save_query_to_csv` is now refused with an explanation.**
  It used to be resolved against a working directory nobody chose — `/` under a
  client on macOS — so `dest="export.csv"` became `/export.csv` and the write
  failed with `[Errno 30] Read-only file system`, an error the caller could not
  act on. On a writable root it would have landed where nobody would look.
  `~/x.csv` is now expanded rather than resolved to a literal `~` directory.
- **A misconfigured `DATOSGOBDO_ARCHIVE_DIR` warns instead of going quiet.** A
  value that does not name a directory — most often a relative path, which
  resolves nowhere for the same reason as above — silently left the archive
  fallback off while the operator believed it was armed. The module's promise is
  "opt-in, never silent"; that now holds for the misconfigured case too. One
  warning per bad value, not one per fetch.

### Changed

- **The four listing tools answer with one named object instead of a bare list.**
  `list_organizations`, `list_groups`, `list_tags` and `autocomplete` returned
  lists, which FastMCP serialises as one content block per element — two hundred
  blocks for two hundred tags — under a schema that calls the payload `result`. A
  client that assumed the shape of `search_datasets`, a single object, read
  `content[0]` and saw one institution. They now return
  `{organizations, count, limit_reached}`, `{groups, count}`,
  `{tags, count, limit_reached}` and `{suggestions, count, kind, query}`.

  This is a **breaking change**, taken deliberately before the API freezes: a
  caller reading `structuredContent.result` must read the named field instead.
  `limit_reached` is new information rather than decoration — `limit` caps at 200
  against 266 institutions, and a tag listing without a `query` is a sample of
  874, so a full page used to be indistinguishable from a complete answer. The
  error shape from the CKAN layer is lifted to the top level rather than becoming
  the first element of the payload.

  One thing got worse and is worth saying: the generated `outputSchema` is now a
  generic object, the same as `search_datasets` and `get_dataset`, where before it
  described an array. Typed envelopes were then written and measured, and cost
  **1,673 bytes** of the tool list — 42,542 against the 42,000 ceiling — for
  schema that mostly restates the field names in the docstring. The analytics
  tools are typed because their payload *is* the answer and a host validates it;
  these four are navigational, and every conversation would pay the difference. So
  they stay untyped by decision, with the number written down, rather than by
  omission.
- **The tool-list context ceiling moved from 41,000 to 42,000 bytes.** The
  measured figure was 40,990 — ten bytes of headroom, which is a trap for the next
  docstring rather than a tripwire for a verbose new tool. The current figure is
  40,869.

### Added

- **`--version` and `--help`.** Windows testing reached for
  `uvx dominican-open-data-mcp --help` to find out which version was installed —
  the obvious move — and got a server that started, received EOF on stdin and shut
  down, printing nothing that answered the question. Both flags now answer and
  exit before any session begins, so stdout stays the protocol channel while
  serving and behaves like a command line when nobody is serving.
- **A Windows-specific hint when a destination path is too long.** With long paths
  disabled, which is the Windows default, a 288-character destination returned
  `[WinError 206] The filename or extension is too long` and `hint: null`. The
  reply now says how long the path actually is and names both ways out.
, not the requested
  one: network-guard mode and whether the archive is on. A client passes only a
  limited subset of the environment to a stdio server, so an operator who set
  `DATOSGOBDO_NETGUARD` in a shell is running with the default and has no other
  way to notice. `get_cache_stats` already reported the same mode as
  `server.netguard_mode`.

### Documentation

- **Readability is 561 of 1,056, not 540 — and the breakdown moves with it.** The
  2026-08-13 re-measurement of everything that had failed for a reason inside this
  server's control recovered 21 census resources: 19 of the 37 that served a web
  page now resolve to the file the page links, and 2 of the 8 unreadable files now
  parse. The per-cause table is recomputed rather than labelled stale, and the
  arithmetic closes: 561 + 495 = 1,056.

  The first version of this entry said 572 and 928,878 rows, which counted the
  whole recovery against a denominator that never contained it. 11 of the 32
  recovered resources are *siblings* — a second or third format of a dataset whose
  one-per-dataset representative was already in the census — and they hold 846,388
  of those rows, including SeNaSa's ODS. They are now reported separately, because
  a dataset becoming readable in another format is a real gain and still not a
  change in how many of the 1,056 can be read.
- **Windows is now a tested platform with its limits written down.** README §17
  says what a real Windows 11 session established — green suite, encoding intact
  end to end, an aggregation matching an independent `Decimal` recomputation to the
  cent, no measurable Defender penalty — and what it did not reach: an accented
  user profile (`C:\Users\José Pérez\`, common in the Dominican Republic), a
  Downloads folder redirected into OneDrive, Claude Desktop as the client, Windows
  on a drive other than `C:`, and a before-and-after Defender exclusion, which
  needs administrator rights.
- **The exported CSV has no BOM, and Excel on Spanish Windows cares.** The file is
  valid UTF-8 with CRLF, and Excel will still read it as cp1252 and show `AÃ±o`.
  Documented with the two ways around it rather than changed: `Data → From
  Text/CSV`, or LibreOffice.


- **The `env` block, which the READMEs listed variables without ever showing.**
  `export DATOSGOBDO_NETGUARD=strict` does not reach a client-launched server —
  a documented security control that was not applying. README §13 now shows the
  JSON, §15 and `SECURITY.md` say where to set it and how to verify what is in
  force.
- Hosted mode: nothing captures stderr under `streamable-http`, so log
  collection or OpenTelemetry is the operator's job.
- The protocol's own logging channel (`notifications/message`) is deprecated as
  of spec `2026-07-28`; stderr-only was already what the specification now
  recommends, so this is recorded as "nothing to migrate, do not add it".
- Why two version numbers appear: the spec links point at `2026-07-28` while the
  server negotiates `2025-11-25`, because `mcp>=1.9.0,<2` is pinned deliberately.
  `server/discover`, per-request `_meta` and per-request log levels are therefore
  not implemented.
- Tutorial §2.1 and Step 7 (both languages) generalize the lesson: require
  absolute paths for path-valued inputs, and log the configuration you looked
  for and did not find.

## [0.13.0] — 2026-08-12

Both entries below were found by running the reference
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) against the
server. Neither was reachable from this project's own test suite, because both
are about how the server looks from outside a language model.

### Added

- **Resources and prompts, which the handshake had been promising.** The
  protocol has three primitives, distinguished by who decides when to use
  them: the model calls tools, the application attaches resources, the user
  picks prompts. This server advertised all three capabilities at
  `initialize` and served only tools — `resources/list`,
  `resources/templates/list` and `prompts/list` each returned zero, so a
  client opening those panels found the empty sections the handshake had
  announced.

  Three resources now carry the catalog as read-only context:
  `datosgobdo://catalog/overview`, `datosgobdo://catalog/institutions`, and
  the template `datosgobdo://dataset/{dataset_id}`. All three are stamped
  `source: catalog_metadata`, like every catalog reply — what the catalog says
  about a file is not what the file contains, and a resource attached silently
  into a prompt is exactly where that distinction gets lost.

  A fourth resource, `datosgobdo://guia/verificacion`, is the checking
  protocol itself — the four fields that make a figure citable and what to do
  when one is missing. A resource rather than a prompt because it is not a
  request: it is the half-page a journalist keeps open beside the
  conversation.

  Six prompts encode the habits the live sessions had to learn the hard way.
  `empezar_aqui` takes no arguments and is the one to open first: twenty-four
  tools is not an invitation, and someone who has never seen this catalog does
  not know that payrolls, budget execution and provincial investment are what
  it covers best. `serie_temporal` carries the rules a time series needs here —
  the year is a dimension and not a measure, the real coverage rarely matches
  the title, and ordering periods as text put `MAYO` above `JUNIO` in a
  measured session. The other four:
  `auditar_nomina` (report what the coercion excluded, the digest and the
  SQL), `verificar_fuente` (check reachability first, and never answer with a
  different file), `explorar_institucion` (separate what the catalog claims
  from what was read), and `cruzar_fuentes` (declare units and periods before
  concluding anything). The specification describes prompts as a way to
  "showcase how to best use the MCP server", and 24 tools is a lot of surface
  for someone who has never seen this catalog.

- **The server says what it is and how this catalog behaves.** `serverInfo`
  carried a name and a version and nothing else — no link back to the project,
  and no word to an agent about a catalog where half the files cannot be
  downloaded. It now carries `websiteUrl`, and the connection carries
  `instructions`: check reachability before recommending a source, never
  answer with a different file, separate what the catalog claims from what was
  read, and pass on the coercion, digest and SQL that make a figure checkable.
  Clients deliver instructions once at connect, so this reaches every agent —
  including the ones that never open the prompts panel — at no per-call cost.

### Fixed

- **`list_organizations` returns the number of institutions asked for.** CKAN
  caps `organization_list?all_fields=true` at 25 per response no matter what
  `limit` says, and the cap is silent: asking for 500 of this portal's 266
  institutions returned 25 and looked complete. Pages are now fetched until
  the requested count is reached. This had a second victim — the hint on a
  failed `get_organization` said "use `list_organizations`", which for any
  institution outside the first 25 alphabetically was advice that could not
  work.

- **A wrong slug is answered with the right one.** Institution slugs here are
  the full registered name, so the acronym anyone would type never resolves:
  `get_organization("indotel")` returned 404 and a hint suggesting two other
  tools. The suggestion is now made instead of recommended — one autocomplete
  lookup, and the reply names the match: *Did you mean
  'instituto-dominicano-de-las-telecomunicaciones-indotel' (Instituto
  Dominicano de las Telecomunicaciones (INDOTEL))?* When nothing matches, the
  reply says so and explains that slugs are full names rather than acronyms.

- **One language per audience.** Ten of the twenty-four tool descriptions were
  in Spanish and fourteen in English, with parameter descriptions mixed the
  same way. Everything a model reads — tool descriptions, parameter
  descriptions, error messages — is now English, which is what the ecosystem's
  conventions and every other MCP server use, and matters for the agents that
  are not Claude. Everything a person reads — prompt titles and descriptions,
  resource names, the verification guide — stays Spanish, because the audience
  is Dominican. Spanish column names in examples (`"Año"`, `"Abril"`) stay as
  they are: they are data, not prose.

- **A resource search now names who published the file.** CKAN's
  `resource_search` answers with the file and nothing around it: a name, a
  format and a URL, with no dataset and no institution. In this catalog that
  is close to useless — files are registered under names like `clss.csv`, and
  "who publishes this?" is the first question anyone asks of a government
  file. Each result now carries its dataset and its institution, resolved in
  one additional request for the whole page rather than one per row; if that
  lookup fails the search still returns, without the parent fields, since a
  list without institutions beats an error.

- **A failed call now says it failed.** This server answers a handled failure
  with a normal result whose body is `{"error": ..., "hint": ...}`, which
  serves an assistant well — a structured hint is something it can act on. It
  told nothing *other* than a model that anything had gone wrong: measured
  with the Inspector, an unknown tool exited 5 while this server's own
  "Column not found" exited 0, so a CI pipeline chaining on `&&` walked
  straight past the failure.

  `isError` is now set on those replies while their structured payload is
  kept. The SDK offers no way to have both — its success path hardcodes
  `isError=False` and its error path discards `structuredContent` — so the
  reply is amended after the fact. A test pins the assumption, so an SDK whose
  shape changes fails loudly instead of silently reverting the behaviour.

## [0.12.0] — 2026-08-12

One feature with one purpose: make every figure this server produces
**checkable by a third party**. The motivating measurement, from a live
session: an assistant's prose figures were exact while its retyped table
drifted 300 million from what its own script had computed. A number that
arrives in `structuredContent` was computed; a number a model retypes may not
survive the trip — and from the outside the two are indistinguishable.

### Added

- **`source_sha256` in every reply.** The digest of the source bytes exactly
  as they were parsed — after following a page to its file, before any
  transcoding — stored with the cache entry, so it travels on the first call
  and every one after. Against an independent capture of the same file (the
  8-August mirror keeps one per resource), a matching digest proves both reads
  saw the same bytes. A **truncated download carries no digest**: hashing part
  of a file and presenting it as the file's digest would be precisely the
  false confidence the field exists to kill.

- **`computation` on `aggregate_resource` and `query_resource`.** The SQL that
  ran — naming only the `data` view, never a server path — and the number of
  rows it scanned. Same digest + same SQL = same figure, reproducible by
  anyone with the file. The verification protocol for a journalist becomes
  three questions: does the reply say `source: file_contents`? does the digest
  match an independent capture? does the computation reproduce the number?

- **`get_cache_stats` now says who is answering**: server name, version,
  netguard mode and transport. The version travels in the initialize
  handshake, which clients read and never hand to the model — a live tester
  asking "which version is running?" could only answer with the portal's CKAN
  version. This is the one tool that describes the server rather than the
  catalog, so identity rides here instead of costing the tool list a 25th
  entry.

## [0.11.1] — 2026-08-12

Hardening for the platform most of this server's audience uses. A code audit
ahead of the first Windows test run found three gaps; none had ever produced a
bug report, because the code had never run on Windows at all.

### Fixed

- **The system-path denylist now covers Windows.** `save_query_to_csv` refused
  `/etc` and `/usr` while `C:\Windows\Temp\evil.csv` passed every check — the
  protection existed on the platforms the developers use and not on the
  platform most users do. Windows prefixes (`C:\Windows`, `C:\Program Files`,
  `C:\ProgramData`) are compared case-folded with slashes normalised, because
  that filesystem is case-insensitive and paths arrive in both spellings;
  POSIX prefixes keep exact case, since `/Etc` is legitimately a different
  directory from `/etc`.

- **The cache lock is now a real lock on Windows.** Index and eviction
  mutations were serialised with `fcntl.flock`, which does not exist there, so
  two server instances sharing a cache directory raced unprotected. Windows
  now locks through `msvcrt.locking` over one byte of the same lock file. Its
  `LK_LOCK` mode retries for about ten seconds and then raises rather than
  waiting forever — a mutation under this lock is a JSON write measured in
  milliseconds, so a loud failure after ten seconds beats an indefinite
  silent wait.

- **Startup is quiet again.** Some versions of the mcp SDK's settings model
  trip a `pydantic_settings` warning at import time, printing a wall of text
  to stderr before the server says anything — every fresh install saw a
  warning as its first impression. Filtered narrowly, by module and message,
  so anything else `pydantic_settings` might warn about still comes through.

## [0.11.0] — 2026-08-09

Everything here came from driving the server through a real MCP client instead
of a test harness, and watching what an assistant did when the server was
unclear. None of it was reachable from the automated batteries, for one reason:
a battery calls tools, an assistant makes decisions, and the decisions are
where the gaps show.

### Added

- **`check_resources`** — ask up to 25 URLs whether their files can actually be
  downloaded, without downloading them. The catalog says a resource exists; it
  does not say the file is still at that address, and in this catalog a large
  share are not. Asked for the best payroll sources, an assistant recommended an
  institution whose every resource returns 403. It could not have known: nothing
  exposed reachability, so the only way to find out was to try, which happens
  after the recommendation.

  The reply is a class per URL, not a boolean. A host that refuses `HEAD` while
  serving `GET`, and a host that answers 200 with a page for every unknown path,
  are neither reachable nor refused; rounding either way would state something
  that was not measured.

- **Catalog replies say they are catalog replies.** Asked about water
  utilities, an assistant described five datasets in confident detail — how many
  files each held, what the columns measured — with every word taken from the
  `description` field, and every one of those files unreachable. Nothing was
  fabricated and nothing marked the difference. Catalog tools now carry
  `source: catalog_metadata`; analytics tools carry `source: file_contents`.

### Fixed

- **A browser challenge is no longer reported as a refusal.** A 403 carries at
  least two different decisions in this catalog. A site rule refuses every
  client. An interactive challenge refuses only programs — it answers 403 with
  `cf-mitigated: challenge`, and a person with a browser downloads the file
  without noticing. Verified against a live host: every header combination a
  client can send still failed while a browser succeeded.

  Both used to surface as the raw text of the HTTP error plus a link to MDN,
  with `hint` and `next_step` null. That is not merely unhelpful. Handed a
  resource it could not fetch, an assistant found a similar file from a
  different institution and answered with it — figures a million apart, the
  substitution named once in passing. An error that offers no path invites the
  caller to invent one, so every explanation now ends by saying that answering
  from another source without declaring it is worse than not answering. Where an
  archived copy of that exact URL exists it is offered with its capture date and
  digest; where none exists, nothing is promised.

- **Aggregation errors name the keys.** `column` and `function` are the obvious
  names and they are wrong, which used to produce `Aggregation not allowed: `
  with nothing after the colon, because `fn` was missing rather than invalid.
  Both received and expected keys are now named, and an invalid function lists
  the valid ones. Filters got the same treatment.

- **A `limit` with no `order_by` says its result is arbitrary.** It returned an
  arbitrary slice of groups shaped exactly like a top N, and nothing in the
  reply said otherwise. The warning appears only when the cut actually happened;
  warning on queries that fit under the limit is noise.

- **Filters read a text-stored number as a number.** Aggregations already did,
  so the same column that summed fine raised a binder error when compared
  against an integer — and the obvious workaround, comparing against a string,
  succeeded while comparing alphabetically, where `"00" > "0"` is true and the
  number is not. Numeric operands are coerced and declared in
  `numeric_coercion`; string operands keep their meaning, since `=` against a
  text code is a legitimate question, and are flagged as alphabetical.

- **Provenance survives the cache.** `resolved_from` and `parse_warning` were
  reported by the download path only, so a caller told once that its data came
  from a URL it had not asked for was never told again — and the warm path
  serves every call but the first. Both are now stored with the entry and
  returned on hits.

- **Google Drive links registered as viewer pages are read.** Five resources
  are registered as `drive.google.com/file/d/<id>/view`, which is the viewer
  page — HTML, no data — so they counted as unreadable files. The catalog also
  registers others as `uc?export=download&id=<id>`, and those always read fine,
  which is what makes this a normalisation rather than a workaround: the target
  is the form the publisher already uses when they get it right. Both addresses
  are the same document under the same permissions; a private file stays
  private and the request still passes the SSRF guard.

  Google's download endpoint refuses `Sec-Fetch-Site: cross-site` outright —
  the same URL answers 303 then 200 without those headers and 403 with them,
  reproducibly — so fetch-metadata headers are now chosen per host and omitted
  there. Omitting is not the same as sending false values: this request really
  is a cross-site programmatic fetch, and claiming `navigate`/`document` to get
  past the check would be the exact lie the header block promises not to tell.

- **A resource whose declared format contradicts its bytes is read as what it
  is.** One resource is registered as CSV and is a spreadsheet; DuckDB read the
  ZIP header as a column name and answered `Parser Error: unterminated quoted
  identifier at or near ""PK`, which names nothing the caller can act on and
  reads like a bug in this server. The file holds 9,427 rows. The catalog says
  what someone typed into it; the bytes say what the file is. A resource
  declared CSV, TSV or JSON that starts with the ZIP signature is now read as a
  spreadsheet, and the reply carries `format_corrected` — declared, actual, how
  it was detected, and that the wrong format is a finding about the publisher.
  Like the rest of the provenance it is stored with the cache entry, so the
  warm path repeats it.

- **Three more ways a page can still name its file.** An embedded file is a
  named file: `iframe` and `embed` sources now count as links, and Drive's own
  share and download forms are recognised as download handlers. When nothing
  matches the declared format, the other files on the page are returned as
  candidates saying what they actually are — a page offering three PDFs when a
  CSV was asked for is not the same situation as a page offering nothing. And
  the largest group gets its own sentence: sixteen of these pages carry
  hundreds of anchors and not one to a data file, because the list is fetched
  when a browser opens the page, so no number of link hops reaches it.

  A share link with an empty file id does not count. One page embeds
  `drive.google.com/file/d//preview` with the id left out, the real one
  base64-encoded in the page's own query string; accepting it turned "no
  candidate" into a confident answer pointing at nothing. Measured over the 37
  pages: six resolve as before, ten now hand back candidates where four did,
  and the caller gets something to act on in sixteen instead of ten.

## [0.10.1] — 2026-08-08

### Fixed

- **A file that parses into a single cell no longer passes as a success.** The
  dangerous case was never the error, it was the success: a 12.8 MB JSON array
  that DuckDB folds into one value came back as "1 row, 1 column" with no error
  at all, and an assistant would report that cell as the dataset. Measured
  across 1,926 readable resources of the catalog, 12 do this — six of them
  JSON, the rest spreadsheets whose real table sits behind a cover sheet.

  The response now carries `cache.parse_warning` naming the size, the shape and
  what usually causes it. It warns rather than refuses: a one-column file is
  legal, and blocking would trade a rare wrong answer for a certain lost one.

  Found while checking a different hypothesis. A first pass compared row counts
  across formats of the same dataset and reported that a third of them
  disagreed; the comparison was invalid — a dataset holds many different tables,
  not one table in three formats — and dropping it surfaced this instead.

## [0.10.0] — 2026-08-08

### Added

- **An archived copy can answer when the portal will not.** Point
  `DATOSGOBDO_ARCHIVE_DIR` at a directory holding a `manifest.json` and its
  Parquet files; the portal is still tried first, and only when it fails does
  the server fall back.

  Government links rot. The 2026-08-08 census of all 1,056 catalog resources
  found 15 URLs already dead and 99 institutions whose sites had grown rules
  refusing programmatic access — so a figure cited from a resource today may be
  uncheckable next year. The archive also makes a number reproducible: the same
  `sha256` recomputes the same answer years later.

  **The reply always says so.** `cache.provenance` carries the capture date, the
  digest, the licence and the reason the origin was not used. A tool that
  quietly returned yesterday's copy as though it were today's would stop being
  usable for an audit, so this is not a silent cache — it is off by default and
  it is always declared.

  What it does *not* do, since this is the natural assumption and it is wrong:
  an archive only holds what could be downloaded, so it does not contain the
  resources a portal refuses.

- `sweep/mirror_sync.py` writes `manifest.json` — `sha256`, capture date,
  licence, row count and the parser build that produced each file. Without it a
  folder of Parquet is a rumour: nothing downstream can say what a copy is,
  whether it may be redistributed, or whether it still matches the source. The
  licence gate already in place keeps unlicensed resources out of it.

## [0.9.1] — 2026-08-08

### Changed

- **The tool list costs 4,863 fewer bytes of context**, 43,582 → 38,719, with
  nothing removed that a reader uses. Every conversation pays for these 23
  schemas before the user asks anything.

  The plan called this a "description diet", and measuring it first showed the
  plan was aimed at the wrong target: the prose descriptions are only 6,083 of
  the 43,582 bytes. The weight is in the schemas — 15,930 output, 14,797 input —
  and a third of the output half was boilerplate Pydantic generates. Every field
  arrived titled (`non_null_count` carrying `"title": "Non Null Count"`) and
  every optional one stamped `"default": null`. The property key already carries
  the name, and an absent optional field is absent.

  The prose was left alone deliberately. The largest failure mode measured
  across the catalog was malformed calls, and the guidance that prevents them is
  worth more than the bytes it costs.

  Two tests hold the line: no generated field titles in any schema, and a
  41,000-byte ceiling on the whole tool list — a tripwire, not a target, since a
  single verbose result model can otherwise add kilobytes to every conversation
  in a project without anyone noticing.

## [0.9.0] — 2026-08-08

### Added

- **A resource URL that serves a web page is no longer a dead end.** In a census
  of the whole catalog, 37 of 1,056 resources answered with a page instead of a
  file, and the reply was "the URL returned an HTML page" — true, and nowhere to
  go. Opening all 37 showed they are five different situations, not one, so the
  server now tries to resolve them and says which situation it found.

  **Measured against those 37 over the protocol: 6 resolve to their file
  (12,596 rows recovered), 4 come back as a choice, 27 are genuinely dead** —
  15 link no data file at all, 7 are logins, 3 hold the data in an HTML table.

  There is deliberately **no per-portal knowledge** in this. A curated map of
  how each institution lays out its site would rot with the next redesign and
  would be useless for the other countries in the regional catalog. What
  replaces it is the request itself: the URL the caller asked for usually names
  the resource, so the links a page offers are scored against it. Measured, the
  URL-derived hint resolves 6 while fetching CKAN metadata for the resource name
  resolves only 4 — the cheaper signal is also the better one.

  The decisive signal turned out to be the **declared format**. These portals
  publish the same file three times, as `.csv`, `.ods` and `.xlsx`; the three
  names are identical so they score within 0.001 of each other, and no name
  matching can separate them. It does not have to — the caller already said
  which format the resource is registered as.

- **When the links cannot be told apart, the caller gets them.** An ambiguous
  page returns `linked_files` with each candidate's URL, name and score instead
  of an error. The caller is an assistant holding the user's actual question,
  and it chooses better than a string-similarity ratio can. Two real cases could
  never be resolved by any algorithm — one portal names its files `clss.csv` and
  `xls.csv`, another offers six navigation links — so guessing would have put a
  plausible wrong table in front of someone with no way to check it.

- **Following a link is declared.** `resolved_from: {page, followed}` travels in
  the response. The caller asked for one URL and received data from another;
  staying silent about that would break the trail an audit depends on. Only one
  hop is followed, and it passes the SSRF guard like any other download.

### Changed

- The failure message now names which of the four situations a page is — no
  linked file, a login, an HTML table, or a chain of pages. Thirty-seven
  resources used to share one sentence, and the reader's next move differs in
  each.

## [0.8.0] — 2026-08-08

### Added

- **Columns holding numbers as text can now be measured.** This was the single
  largest failure class in the catalog: **202 of 284 errors (71 %)** from the
  battery of analyst-written calls, over **90 columns in 54 readable files**.
  A payroll publishes `SUELDO BRUTO (RD$)` as text because a handful of cells
  say `N/A`, and every `SUM` and `AVG` over it failed — for a column that is
  unambiguously a measure, held hostage by three bad rows.

  `aggregate_resource`, `quantiles_resource` and `detect_outliers_resource` now
  read such a column as a number where each value permits it. The cleanup
  removes thousands separators, non-breaking spaces and currency prefixes; the
  values that still refuse — `N/A`, `-`, `#REF!`, `PROCESO CANCELADO`, and
  header rows the publisher left inside the data — are excluded.

  **The reply always says so.** A `numeric_coercion` block reports how many
  values were used, how many were dropped and which ones, with counts. This is
  an audit tool: absorbing a publisher's defect quietly would make the server
  the last place that defect is visible, and the caller would receive a total
  with no reason to doubt it.

  Chosen by measurement over the 1,133 text columns in the mirror. The cleanup
  rescues 41 columns that a plain cast cannot read at all — payroll
  (`Sueldo bruto`, `AFP`, `ISR`, `NETO`), water quality
  (`INDICE_POTABILIDAD_(%)`, `CLORO_RESIDUAL_(Mg/l)`), production volumes. A
  variant that also stripped spaces was tested and rejected: it rescued one
  further column and would have read the three codes `10 20 30` as 102030.

  Two limits are deliberate. A column under 90 % parseable stays text and the
  reply explains why — answering a question about a measure from an arbitrary
  subset of rows is worse than refusing. And `count` / `count_distinct` are
  never coerced: counting text is a legitimate question about text.

## [0.7.11] — 2026-08-08

### Security

- **The HEAD request that builds the cache key bypassed the SSRF guard.**
  `ensure_cached` probes a resource for its ETag before downloading it, and
  that probe went out with no guard installed — so a caller naming an internal
  address had a real request delivered to it, ahead of the download the guard
  correctly refused. Demonstrated against a loopback service: the HEAD arrived
  and its ETag came back, landing in the cache key. Blind, but a working
  network-probe primitive from inside the perimeter — enough to test liveness
  of cloud metadata endpoints, internal panels and open ports through timing
  and through the version tag. It also let `strict` mode be bypassed, since the
  hostname was never checked on this path. The guard is now installed on that
  client, and the request raises rather than returning empty: "this URL is not
  allowed" is a different fact from "this host did not answer".

  Introduced in 0.6.0 (P2a), which wired the guard into downloads. A HEAD is
  not a download. Matters most for hosted deployments, where the caller is not
  necessarily the operator.

### Fixed

- **Resource requests now state their fetch context.** A sweep of all 1,056
  datasets found 67 hosts answering HTTP 403. Two of them —
  `deepblue.simv.gob.do` and `migracion.gob.do`, **16 datasets** between them —
  answer 200 as soon as `Sec-Fetch-Mode`, `Sec-Fetch-Site` and `Sec-Fetch-Dest`
  are present, and 403 without them, reproducibly across repeats. The
  User-Agent is not the discriminator: an honest `datosgobdo-mcp/…` and a
  Chrome string get the same answer either way, so nothing here impersonates a
  browser. The values sent are the true ones for this client — a cross-site
  programmatic fetch whose destination is not a document.

  The other 65 hosts refuse every header combination tried, which points at the
  network path rather than the request. That question is still open.

## [0.7.10] — 2026-08-08

### Fixed

- **The cache could not tell that we had changed.** Entries were keyed on URL +
  ETag, which answers "is the source still the same?" but not "would we parse
  it the same way today?" When 0.7.5 corrected the codepage detection, ten
  Parquet files written wrongly by 0.7.4 stayed valid under that key and kept
  serving `A隳` and `Informaci≤n` to every caller. The fix shipped, the tests
  passed, and nothing changed for anyone whose cache was already warm; the
  files had to be deleted by hand. The key now also carries a **parser build**
  — this package's version and DuckDB's, since its CSV sniffer decides the
  column types and a dependency upgrade changes our output without changing
  our code.
- **The warm path needed its own check.** `ensure_cached` matches on URL alone
  and returns before the HEAD request, so it never computes a key and the fix
  above does not reach it. Entries now carry the build that wrote them, and one
  written by different code is refused there explicitly. Entries predating the
  stamp are treated as stale, which also clears any 0.7.4 leftovers still in a
  user's cache.

Invalidation is deliberately coarse: a release that could not have altered the
parse still drops its entries. Over-invalidating costs one re-download inside a
1 GB LRU. Under-invalidating means silently serving corrupted data from a tool
whose whole purpose is to be trusted about what a file says.

### Changed

- `get_cache_stats` reports `parser_build` and `stale_entries`.

## [0.7.9] — 2026-08-08

Two more error messages turned into instructions, from the directed battery run
over 450 files: 1,679 calls composed from each file's real schema.

### Changed

- **A failed `CAST` now names the value that broke it and offers `TRY_CAST`.**
  This was the single largest remaining failure class — **112 of 284 errors
  (39 %)**. The values are stereotyped across this catalog: thousands
  separators (`1,145`, `41,300.00`), a non-breaking space glued to a number
  (`159065.95\xa0`), and placeholders standing in for missing data (`N/A`,
  `-`, `#REF!`, `PROCESO CANCELADO`). The reply now says which value stopped
  the cast and hands over a query that survives it.
- **`query_resource` naming a column that does not exist now lists the real
  ones**, instead of relaying DuckDB's "Referenced column not found". Same
  treatment the typed tools already got in 0.7.4.

## [0.7.8] — 2026-08-08

### Fixed

- **Encoding detection asked the wrong question.** It scored how *suspicious* a
  decoding looked, but every candidate renders the same bytes as some odd
  symbol, so a byte that is odd under all of them is noise. In a real payroll
  one such character recurred 132 times in the body and outvoted the header,
  choosing the reading that turned `AÑO` into `A¥O`. The score now measures how
  much Spanish a decoding *recovers* — `Año`, `Región`, `ÁREA` against `A¥o`,
  `A±o`, `┴REA` — with an absolute penalty only for scripts that cannot occur
  here at all. Across the 146 non-UTF-8 files in the mirror, 145 now decode
  correctly.
- The one that does not is a file encoded in **two codepages at once**: its body
  is CP1252 (132 correctly recovered accented letters) while its header's `Ñ`
  is a CP850 byte. No single codepage is right for it; CP1252 is the correct
  global choice and the header's one character stays wrong.

## [0.7.7] — 2026-08-08

### Changed

- **`SUM` over a text column now says why and what to do instead.** The single
  largest remaining class of failure in the directed battery (23 of 487 calls):
  a spreadsheet mixes a footnote, a total or `"N/D"` into a numeric column, the
  whole column loads as text, and DuckDB answers that `sum(VARCHAR)` does not
  exist — true, and useless to a caller with no way to know the fix is a cast.
  The reply now names the cause and hands over a working `query_resource` query.
  Other DuckDB messages pass through unchanged.

## [0.7.6] — 2026-08-08

Found while scaling the protocol run from 129 to 500 datasets.

### Fixed

- **A header wrapped across two lines inside a quoted field killed the file.**
  That is legal CSV, and a live price series publishes one; DuckDB's sniffer
  refuses the whole resource over it. A `strict_mode=false` retry — paid only by
  files that already failed — reads it correctly: 153 columns instead of an
  error.
- **"IO Error: Failed to open zip for reading."** Portals answer a gated or
  moved download with a login page carrying the original filename and HTTP 200,
  and not every such page is shaped like the HTML the existing guard
  recognises. Zip-container formats (XLSX/XLSM/ODS) now check the magic bytes
  first, so the caller is told the portal served a web page instead of being
  handed a message that reads like a bug in this server.

## [0.7.5] — 2026-08-08

### Fixed

- **Eleven tools returned no `structuredContent` at all.** An unparameterised
  `-> dict` return annotation makes FastMCP skip the `outputSchema`, and without
  one the tool answers with text only. Every discovery and catalog tool was in
  that state — `search_datasets`, `get_dataset`, `get_resource`,
  `search_resources`, `list_recent_datasets`, `list_organizations`,
  `get_organization`, `list_groups`, `list_tags`, `autocomplete`,
  `get_site_stats` — which is the entire entry point of a conversation. The
  analytics tools were unaffected because they return Pydantic models. Caught by
  fetching catalog metadata through the protocol instead of through CKAN
  directly; a test now asserts all 23 tools declare an output schema.
- **Encoding detection regression from 0.7.4.** Letting chardet's
  low-confidence guess win outright was worse than the problem it fixed:
  Latin-1 bytes decode without error as macroman, cp1250, cp874 and even cp424,
  and chardet volunteers those at ~5% confidence, so `Año` came back as `AÒo`,
  `AŃO` or `A๑o`. The guess now only competes if it is a codepage this catalog
  could plausibly contain, and the scorer penalises Greek, Cyrillic, CJK,
  box-drawing and maths blocks — the characters a DOS-codepage misreading
  produces (`A±o`, `investigaci≤n`). Across the 37 non-UTF-8 files in the
  mirror, 37 now decode cleanly; before this pass, 8 did not.

## [0.7.4] — 2026-08-07

The second pass of the same protocol audit, this time driving the tools with
calls composed from each file's schema alone, with no knowledge of its
contents — the closest thing to how an assistant actually uses this server. 129 files, 487
calls. Two failure modes accounted for most of it, and neither was the data.

### Fixed

- **`group_by` and `columns` rejected `[{"col": "Año"}]`.** Three of the four
  list parameters on these tools (`filters`, `order_by`, `having`) take objects
  keyed by `col`, and one takes bare strings. Models generalise from the
  majority: **190 of 487 directed calls** were written the object way, and every
  one of them died in schema validation *before the tool ran*, so the caller got
  a Pydantic traceback instead of an answer. Both spellings are now accepted.
- **Column names were validated against a character class instead of being
  resolved against the file.** A header the publisher had mangled (`A¤o`) made
  every tool refuse the whole resource. Names supplied by the caller are now
  matched against the columns the open view actually has — case- and
  whitespace-insensitively, so `año` finds `AÑO` — and a name that matches
  nothing returns "Column not found, columns are: …" instead of a SQL error.
  Names that came from DuckDB's own `DESCRIBE` are escaped, never validated.
  Aggregation aliases, which are invented by the model and have nothing to
  match, keep the strict path.
- **`Año` reached users as `A¤o`.** These files are CP850/CP437 — the DOS
  codepages Excel still emits in Latin America, where `0xA4` is `ñ`. chardet
  identified them correctly but at ~5% confidence, under the 0.7 threshold, so
  the guess was discarded for a blind CP1252 fallback. Candidate decodings are
  now scored for characters that would be extraordinary in Spanish text and the
  cleanest one wins. Five files in the sample were affected.
- **Two structurally broken CSVs are now readable.** One is a semicolon file
  Excel padded with five empty comma columns, so commas were the most consistent
  separator and the real record became column one. The other had every line
  quoted as a single field. Both are detected — the table collapses to one
  usable field under the sniffed delimiter while another delimiter splits it
  into three or more — and rewritten before parsing. An 18,235-row book registry
  went from 1 unusable column to 12; a complaints series from 6 to 4.
- **`quantiles_resource` refused percentiles 0 and 1**, which are the minimum
  and maximum and which DuckDB computes without complaint.

## [0.7.3] — 2026-08-07

Findings from running every tool over the MCP protocol — a real stdio client
session, not in-process calls — against 129 files from the live catalog.
Measured over the same 1,121 calls before and after: success **91.5% → 94.0%**,
errors **51 → 10**, total response payload **4.69 MB → 1.29 MB**.

### Fixed

- **`get_resource_schema` returned up to 352 KB.** `sample_rows` defaulted to
  its own 1000 ceiling, so the tool the server tells the model to call *first*
  ("cheap reconnaissance step") was also the most expensive thing it could do —
  roughly 88k tokens of an assistant's context spent learning column names. The
  default is now 6 distinct values per column, enough to recognise what a column
  holds; the 1000 ceiling remains available on request. Largest reply in the
  same benchmark fell to **7.4 KB**.
- **`download_resource_preview` never used the cache.** It called the download
  path directly, so it re-fetched the file on every call: **20-25× slower than
  every other tool** (median 0.77 s against 0.03 s, worst 11.25 s) and a fresh
  request to a government portal each time an assistant glanced at a file it had
  already read. It now reads from the Parquet cache when the resource is already
  there. Median **0.77 s → 0.017 s**.
- **`download_resource_preview` refused ODS**, which is about a third of this
  catalog, while the analytics tools read the same files without trouble — 27 of
  129 resources failed for this reason alone. ODS now goes through the cached
  path. Success rate **79% → 100%**.
- **Column names were rejected for characters nobody can see.** A real header
  read `Cod.Capí\xadtulo`, where `\xad` is a soft hyphen. The name looks correct
  on screen, so the error was impossible to act on. Unicode format characters
  (soft hyphen, zero-width space, bidi marks) are now stripped rather than
  rejected.
- **`detect_outliers_resource` reported an error when a column had no spread.**
  "Which values are outliers?" has a correct answer there — none — and the
  column being flat is itself worth knowing. It returned an error on 13 of 113
  real columns (years, constants, small repeated sets), leaving the assistant
  with nothing to report. It now returns an empty result with an explanation.

## [0.7.2] — 2026-08-07

Robustness fixes found by running the analytics pipeline against a sample of
the real datos.gob.do catalog rather than against test fixtures. All three
defects were invisible to the hermetic suite because the fixtures are
well-formed and the real catalog is not.

### Fixed

- **Handled failures escaped as unhandled exceptions.** `NetGuardError` — most
  often raised for a resource whose host no longer resolves in DNS, which is
  common in a catalog spanning 266 institutions — was caught nowhere, so the
  MCP client received a protocol-level traceback instead of a readable error.
  The same applied to any failure occurring *after* a tool's `ensure_cached`
  call, such as identifier validation. Every analytics tool is now wrapped in
  a single error envelope covering `httpx.HTTPError`, `AnalyticsError`,
  `duckdb.Error`, `NetGuardError` and `OSError`; `download_resource_preview`
  handles `NetGuardError` too.
- **Real government column names were rejected as invalid identifiers.** The
  allowlist accepted only word characters, dots and spaces, so headers like
  `Sueldo Bruto (RD$)`, `% Abastecimiento de la Demanda`,
  `RANGO DE EDAD 60 - 70` and `FECHA DE REGISTRO / ADQUISICIÓN` failed
  validation and made the entire file unqueryable. The character class now
  covers the punctuation that actually appears in these files; the substring
  denylist (`--`, `/*`, `*/`, `;`), the control-character rejection and the
  double-quote escaping that provides the real protection are unchanged.
  Headers that wrap across spreadsheet lines are whitespace-normalized when
  the file is opened, and a single unusable column name now degrades that
  column's samples instead of failing the whole call.
- **A single ODS file could exhaust the machine's memory and hang the server
  indefinitely.** ODS was read with `odf.opendocument.load()`, which builds the
  entire document as a Python object tree. Measured on a real catalog file: a
  **0.70 MB spreadsheet peaked at 0.41 GB of RSS** — roughly 580x — and took
  8–12 s. Since the download cap is 100 MB, the worst case was tens of
  gigabytes; the sweep hit exactly that, reaching **9.3 GB RSS with a core
  pinned at 100% for over 15 minutes** before being killed. DuckDB's
  `memory_limit` does not apply (this is pure Python), and because the parse
  ran synchronously on the event loop, no timeout could fire — the timers
  themselves were blocked. ODS is about a third of this catalog, so this was
  not an edge case.

  `content.xml` is now parsed as a stream, keeping memory proportional to one
  row. On the same file: **0.4 s and 0.053 GB**, byte-identical CSV output
  (11,788 lines). Grid-padding repeat counts are dropped rather than expanded.
- **Blocking work moved off the event loop.** ODS transcoding, encoding
  detection and the DuckDB→Parquet conversion now run in worker threads
  (`asyncio.to_thread`), so the server keeps responding during a cold-path
  load and the query-timeout interrupt can actually fire. The Parquet
  conversion also goes through `_execute_guarded`, so
  `DATOSGOBDO_QUERY_TIMEOUT` now bounds ingestion, not just user SQL.
- **HTML error pages were parsed as data.** Several portals answer a dead or
  gated download link with a styled web page and **HTTP 200**. Read as CSV,
  such a page became a one-column table named `<!DOCTYPE html>` that the
  assistant would relay to the user as real data. Downloads are now checked
  for HTML markup before parsing, in both the analytics and preview paths, and
  rejected with an explanation. A wrong answer is worse than a failed one.

- **Spreadsheets were lost to a single stray cell.** Government workbooks put
  totals, footnotes or `#REF!` thousands of rows below the data, after DuckDB
  has already inferred `DOUBLE` from the top of the column; the load then
  failed outright. A failed typed read now retries with every column as text.
  Worse types beat no data — this recovers about 6% of the sampled catalog.
- **Some errors said nothing at all.** Several httpx timeout classes carry an
  empty message, so a real failure surfaced as `Could not load resource:` with
  nothing after the colon. Error text now falls back to the exception class
  name.

### Added

- `sweep/` (development only, not shipped): a catalog sweep harness that walks
  datos.gob.do through this server's own pipeline and records per-resource
  outcomes. It found every defect listed above.

## [0.7.1] — 2026-08-07

Install-breakage hotfix. Anyone who ran `uvx dominican-open-data-mcp` or
installed fresh after 2026-07-28 hit one of the two failures below.

### Fixed

- **`ModuleNotFoundError: No module named 'mcp.server.fastmcp'` on fresh
  installs.** The MCP Python SDK released **v2.0.0** on 2026-07-28 (the
  `2026-07-28` protocol revision), which renamed `FastMCP` to `MCPServer` and
  removed the old import path with no compatibility shim. Our dependency was
  pinned `mcp>=1.9.0` with no upper bound, so any fresh resolution pulled 2.x
  and the server failed at import. Now pinned `mcp>=1.9.0,<2`, with a
  regression test. Migration to the v2 SDK is tracked separately; the SDK v2
  serves older protocol revisions, so there is no client-side urgency.
  Workaround for anyone on an older release:
  `uvx --with "mcp<2" --from dominican-open-data-mcp datosgobdo-mcp`.
- **`uvx dominican-open-data-mcp` failed with "An executable named
  dominican-open-data-mcp is not provided by package".** The distribution
  shipped only the short `datosgobdo-mcp` console script, but the MCP Registry
  entry (`runtimeHint: uvx` + the PyPI identifier) implies a command matching
  the distribution name — which is also what third-party install pages print.
  A `dominican-open-data-mcp` alias entry point now exists; both names launch
  the same server. Pre-existing bug, unrelated to the SDK break.
- **`serverInfo.version` reported the MCP SDK version instead of the package
  version** (clients saw e.g. `1.27.1`). `FastMCP` accepts no `version`
  argument, so the low-level server fell back to the installed SDK version.

### Changed

- Test suite verified against both `mcp` 1.27.1 and 1.29.0 (316 tests, 88%
  coverage, no omits).

## [0.7.0] — 2026-06-10

### Added (hosted readiness, experimental)

- **`DATOSGOBDO_TRANSPORT=streamable-http`**: serve MCP over stateless HTTP
  (`DATOSGOBDO_HOST`/`DATOSGOBDO_PORT`). In hosted mode `save_query_to_csv` and
  `clear_cache` are disabled (server filesystem / shared cache) and
  `get_cache_stats` omits server paths.
- **DuckDB resource ceilings**: `DATOSGOBDO_DUCKDB_MEMORY` (default 2GB),
  `DATOSGOBDO_DUCKDB_THREADS` (default 4), and `DATOSGOBDO_QUERY_TIMEOUT`
  (wall-clock interrupt for free-form SQL; off by default locally).
- **Cache hardening**: atomic `_index.json` writes (tmp + rename), cross-process
  `flock` around finalize/eviction/clear (no-op on Windows), deterministic LRU
  tie-break.

### Added

- **SSRF guard (`netguard.py`)** wired into every resource download via an httpx
  request hook — validates the initial URL **and each redirect hop**. Default mode
  `public-only`: http/https only, every resolved address must be globally routable
  (cloud metadata `169.254.169.254`, loopback, RFC-1918, link-local, IPv6 ULA all
  blocked). `DATOSGOBDO_NETGUARD=strict|off`, `DATOSGOBDO_ALLOW_HOSTS` for
  operator-trusted hosts. Adversarial tests incl. redirect-to-private.
- **Optional GCP pipeline (`gcp.py`, `pip install 'dominican-open-data-mcp[gcp]'`)** —
  3 tools that register only when the google-cloud libraries are installed:
  `load_resource_to_bigquery` (Parquet cache → GCS → BigQuery External Table or
  Load Job), `list_bigquery_exports`, `get_bigquery_table_info`. Pairs with
  Google's BigQuery MCP: this server ingests, theirs queries. Base install keeps
  exactly 23 tools.
- Full-package coverage discipline: no coverage omits, floor 85% (actual ~88%),
  `ckan.py` and `server.py` at 100% via hermetic tests. macOS CI job added.

### Fixed

- `search_resources` interpolated the raw user query into CKAN's
  `resource_search` `name:{query}` — `:`/`"` are now sanitized out.
- `list_tags` / `autocomplete` no longer fail silently: degraded `[]` returns now
  log a warning.

## [0.6.2] — 2026-06-09

### Added

- **Version-drift guard** (`tests/test_version_sync.py`): CI now fails if
  `pyproject.toml`, `server.json`, `__init__.__version__` or `USER_AGENT` disagree.
- **Symlink hardening in `save_query_to_csv`**: final write uses `O_NOFOLLOW`, closing
  the TOCTOU window where a symlink swapped in after path validation (with
  `overwrite=True`) could redirect the write.

### Changed

- `download_resource_preview` ODS rejection now hints at the analytics tools (which DO
  support ODS) instead of suggesting a manual download.

### Fixed

- **`save_query_to_csv` rejected legitimate writes to the OS temp dir on macOS.** The
  `/private/var` system-path denylist entry also matched `/private/var/folders/…`, which
  is the macOS per-user temp dir (`$TMPDIR`). Writes there — including the entire pytest
  `tmp_path` suite — were blocked. The hermetic suite was therefore **red on macOS while
  green on Linux CI** (where temp lives in `/tmp`). Now paths under
  `tempfile.gettempdir()` are allowed before the denylist runs; real system subtrees such
  as `/private/var/db` stay blocked (regression test added).
- **`_quote_ident` allowed a trailing newline in column identifiers.** The allowlist
  regex was anchored with `^…$`; in Python `$` also matches just before a trailing
  newline, so `"col\n"` passed an allowlist meant to reject control characters. Re-anchored
  with `\A…\Z`. Embedded-newline and trailing-CR cases added to the test matrix.

## [0.6.1] — 2026-06-03

### Fixed

- **`ensure_cached` crash on zero-byte download** — `UnboundLocalError: raw_ods` when
  the server returned an empty body. `raw_ods` was declared inside the `try` block after
  the zero-byte check; moved before `try` so the `finally` cleanup always works.

### Changed

- **Coverage floor raised 75% → 80%** (actual 83%).
- Added hermetic tests for XLSX, JSON, ODS, Latin-1 encoding, and zero-byte error path
  in `analytics.py`; 171 → 184 tests.

## [0.6.0] — 2026-06-03

### Added

- **Typed `outputSchema` / `structuredContent`** for the 12 data-producing tools
  (schema, summarize, filter, aggregate, query, quantiles, find_duplicates,
  detect_outliers, preview, save_query_to_csv, get_cache_stats, clear_cache).
  New `models.py` with Pydantic response models — hosts can now validate tool output.
  Models use `extra="allow"` so dynamic keys (quantile p-values, JSON-preview variants)
  pass through with zero data loss. Navigational CKAN-metadata tools keep dict returns.
- **`Tutorial.md` + `Tutorial_es.md`** — bilingual educational guide: how the server
  works, how to use it, and a step-by-step recipe for building your own MCP server.

### Fixed

- README tool count corrected to **23** (was "17" in EN / "12" in ES).

## [0.5.0] — 2026-06-03

### Added

- **`quantiles_resource`** — percentile distribution (p25/p50/p75/p90/p95/p99 by default) of numeric columns. Fills the gap `aggregate_resource` leaves (only exposes `median`).
- **`find_duplicates_resource`** — find rows duplicated on specified columns (or all columns), sorted by frequency. Essential for payroll and census data-quality checks.
- **`detect_outliers_resource`** — IQR method outlier detection on a single numeric column. Returns outlier rows sorted by distance from the median.
- **`save_query_to_csv`** — export any filter or SQL result to a local CSV file. Defaults to `~/Downloads/datosgobdo-exports/`; supports explicit `dest` (validated, no traversal, no system paths). `overwrite=False` by default.

### Fixed

- **Warm cache no longer issues a HEAD request** on every call. The cache index now stores URL→key mappings; warm-path reads skip the network entirely. Cached data survives a portal outage. `ensure_cached()` gains a `force_refresh=False` parameter for explicit invalidation.
- **ckan.py error model unified** — all public functions now return `{"error": ..., "hint": ...}` on failures instead of raising `RuntimeError`. The model can read the hint and try a recovery tool. Consistent with the `analytics.py` pattern.

### Tools count

19 → 23.

## [0.4.2] — 2026-06-03

### Added

- **ruff + mypy + pytest-cov** gates in CI and `pyproject.toml`. Coverage floor 75% (omitting the HTTP adapter layer covered by live tests).
- **Python 3.13** classifier and CI leg.
- **Dependabot** (pip + GitHub Actions, weekly).
- **CodeQL** SAST workflow (weekly + on push).
- **CONTRIBUTING.md** with dev setup, PR checklist, and security reporting pointer.
- **`pre-commit`** config (ruff lint+format + mypy) for local enforcement.

### Changed

- `mcp` dependency floor raised from `>=1.2.0` to `>=1.9.0` (tested minimum).
- Added `Changelog` and `Bug Tracker` URLs to `pyproject.toml`.

### Fixed

- `_new_con()` no longer runs `INSTALL httpfs/excel` on every call — extensions are bundled in DuckDB ≥1.0 and `LOAD` suffices. Eliminates the network round-trip and the silent-failure risk on cold starts.
- `USER_AGENT` unified to a single source-of-truth (`__init__.py`) across `ckan.py`, `download.py`, and `analytics.py` — previously drifted at `0.1` / `0.2` / `0.3`.
- Removed `_tool_count()` which accessed a private FastMCP attribute (`_tool_manager._tools`) brittle against version upgrades.
- mypy errors in `analytics.py`: `fetchone()` null-safety guards + `_build_agg_expr` / `_build_order_by` now validate `col` is `str` before passing to `_quote_ident`.

## [0.4.1] — 2026-06-02

### Security

- **`query_resource` sandbox (HIGH).** Model-supplied SQL could call DuckDB
  table functions (`read_text` / `read_csv` / `read_blob` / `glob`) to read
  arbitrary local files or reach the network — the keyword denylist did not
  cover them. The resource is now materialized into an in-memory table and the
  connection is locked down (`enable_external_access=false`,
  `lock_configuration=true`) before the query runs. Added adversarial tests
  proving local-file access is blocked while legitimate queries still work.
- Added `SECURITY.md` (disclosure process + threat model).

### Fixed

- **`get_resource_schema.sample_rows` had no effect.** The per-column distinct
  sample was hardcoded to `LIMIT 5`; the documented `sample_rows` parameter is
  now honored.

### Added

- **Tool annotations on all tools** (`title`, `readOnlyHint`, `openWorldHint`,
  and `destructiveHint` on `clear_cache`) — satisfies the Anthropic Directory
  review criteria and lets hosts auto-approve read-only calls.

## [0.4.0] — 2026-05-24

### Added

- Raw read-only SQL escape hatch (`query_resource`).
- ODS support (auto-converted to CSV on the cold path).
- pytest suite (now 139 hermetic tests) + hardened `_quote_ident` against SQL
  comments.

## [0.3.0]

### Added

- Parquet on-disk cache with LRU eviction (`cache.py`).
- Typed `aggregate_resource` and `filter_resource` (GROUP BY / WHERE without SQL).

## [0.2.0]

### Added

- DuckDB-backed `get_resource_schema` and `summarize_resource`.

## [0.1.0]

### Added

- Initial release: CKAN discovery, resource, catalog, and autocomplete tools.

[0.4.1]: https://github.com/alcastaro/datos.gob.do-MCP-server/releases/tag/v0.4.1
[0.4.0]: https://github.com/alcastaro/datos.gob.do-MCP-server/releases/tag/v0.4.0
