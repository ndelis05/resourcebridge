# ResourceBridge

**Community resource discovery, shared CHW knowledge, and human-verified freshness.**

ResourceBridge is a Northwestern CS 338 client-project prototype designed for community health workers (CHWs). It focuses on one client problem: finding a resource is not enough when program information changes and practical knowledge is scattered across directories, websites, spreadsheets, and individual CHWs.

ResourceBridge helps CHWs:

1. **Discover** relevant community resources across multiple sources.
2. **Prioritize by ZIP code** when location matters.
3. **Remember** resources the team has already encountered.
4. **Share practical CHW knowledge** without mixing experience with official source data.
5. **Detect potentially stale information** with deterministic freshness checks.
6. **Propose and discuss structured corrections.**
7. **Create a Community Verified override** after distinct CHW confirmations reach the configured threshold.
8. **Preserve provenance and version history** so the original external value is never hidden.

> **Design principle:** Automation finds uncertainty. CHWs establish trust.

---

## Run the app

### Windows — easiest

Double-click:

```text
START_RESOURCEBRIDGE.bat
```

Then open:

```text
http://127.0.0.1:4173
```

### PowerShell / terminal

```powershell
python server.py
```

If `python` is not recognized, try:

```powershell
py server.py
```

No Flask, npm, database server, or paid AI API is required. The backend uses Python's standard library and SQLite.

---

## Product architecture

```text
CHW search
   ↓
ResourceBridge Python backend
   ↓
┌──────────────────────┬──────────────────────┬──────────────────────┐
│ ResourceBridge SQLite│ Local housing seed   │ Front Door Illinois  │
│ shared memory        │ inherited CSV        │ external catalog     │
└──────────────────────┴──────────────────────┴──────────────────────┘
   ↓
Normalize into one resource structure
   ↓
Weighted relevance + optional ZIP proximity
   ↓
Ranked resource results
   ↓
Encountered resources enter ResourceBridge memory
```

### Internal memory

SQLite stores:

- resources
- CHW notes and questions
- structured corrections
- comments
- unique CHW confirmations
- unresolved freshness flags
- Community Verified overrides
- version history
- freshness-check history

The database starts empty and grows as CHWs encounter resources.

---

## Search behavior

ResourceBridge searches three pools with the same ranking logic:

- resources already remembered in SQLite
- the inherited local housing seed
- Front Door Illinois when available

Each source is normalized into common fields such as:

- organization
- program/service name
- category
- description
- eligibility
- languages
- address
- ZIP code
- service area
- intake/application information

Search is deterministic. Important fields such as service name and category receive more weight than incidental matches in lower-value fields. Common synonyms are expanded so a CHW does not need to know the exact wording used by a directory.

Saved CHW knowledge can provide a small additional relevance signal, but it cannot overpower substantially better official-resource matches.

### ZIP search and proximity

A CHW can enter a 5-digit ZIP code with a resource need, or search by ZIP alone.

ResourceBridge:

1. strongly prioritizes exact ZIP matches;
2. uses Front Door latitude/longitude where available;
3. can estimate distance using ZIP centroids;
4. uses the free Zippopotam.us postal-code service only as a fallback for ZIP centroid lookup;
5. falls back to exact ZIP matching if distance cannot be resolved.

Displayed mileage is explicitly approximate because it is based on resource locations or ZIP centroids, not turn-by-turn routing.

---

## CHW Network

ResourceBridge separates two kinds of community knowledge:

### Notes / questions

Practical experience such as:

> “Call before noon; intake has been filling quickly.”

This information is searchable and resource-linked, but it does not automatically change a trusted resource field.

### Structured corrections

A correction includes:

- the affected field
- current ResourceBridge value
- proposed value
- evidence
- optional context

Other CHWs can discuss and independently confirm the correction.

The demo threshold defaults to **3 distinct CHW confirmations**. This is a prototype governance rule, not a claim that three confirmations should be the production policy.

When the threshold is reached:

- the correction becomes **Community Verified**;
- ResourceBridge creates an approved internal override;
- the original external value remains visible;
- a version-history record is created;
- a matching unresolved freshness flag is resolved when applicable.

The correction card uses one Community Verified state at the top-right; it does not show a redundant second “Verified” button.

---

## Field-level attention

ResourceBridge does not stop at showing that an entire resource needs review.

If a freshness check or proposed CHW correction concerns a specific field, that exact field is outlined in amber on the Overview page. Supported fields include:

- phone
- hours
- address
- ZIP code
- website
- languages
- eligibility
- program status
- application/intake availability

The field also displays the reason for the warning and, for a proposed correction, the proposed replacement value.

Community Verified fields remain visually distinct from fields that still need review.

---

## Deterministic freshness monitoring

The freshness checker runs only against resources already stored in ResourceBridge. It is designed as an **anomaly detector**, not a truth engine.

### Structured source comparison

When an updated source record is available, ResourceBridge compares fields such as:

- phone
- website
- address
- ZIP code
- hours
- languages
- eligibility
- program status
- source-assured date

Formatting differences are normalized before comparison where possible.

### Official website scan

ResourceBridge then visits the resource's known official/source URL and deterministically examines the page.

It checks:

- HTTP reachability
- 404 / 410 responses
- unexpected domain redirects
- visible phone numbers
- `tel:` links
- JSON-LD structured phone data
- structured address and ZIP information
- JSON-LD opening hours
- structured language-access information
- explicit eligibility restrictions
- intake/application/waitlist language
- closure or program-status language
- field-specific phrases such as “updated hours,” “new eligibility requirements,” or “relocated to”
- a page-content fingerprint to determine whether the page changed

ResourceBridge strips scripts/styles from visible-text analysis and also parses structured JSON-LD when a site provides it.

A changed webpage **alone is not a flag**. ResourceBridge requires a stronger field-specific signal before warning a CHW.

Temporary network problems such as DNS failures, timeouts, or many transient server errors are recorded as inconclusive rather than treated as evidence that a program closed.

To keep warnings useful, ResourceBridge maintains one strongest unresolved warning per field instead of stacking repetitive alerts about the same field.

### Time-sensitive CHW notes

Older CHW notes containing terms such as “currently,” “accepting,” “waitlist,” “walk-in,” or “hours” can also be flagged for reconfirmation after the configured age threshold.

### What automation never does

The checker never silently rewrites trusted data.

```text
Possible discrepancy
      ↓
Field-specific flag
      ↓
CHW investigates
      ↓
Structured correction
      ↓
CHW discussion + confirmations
      ↓
Community Verified override
```

---

## External data

### Front Door Illinois

ResourceBridge can cache and locally search Front Door's Illinois HSDS dataset. Front Door's free sample contains the same field structure as the full dataset; broader access can be configured with `FRONT_DOOR_API_KEY` if the team later obtains partner access.

ResourceBridge performs its own search, ZIP prioritization, and ranking rather than relying on Front Door to provide full-text or distance search.

Source: <https://localcarehub.org/open-data/illinois>

### ZIP centroid fallback

For ZIP codes that cannot be resolved from available resource coordinates, ResourceBridge can use the free Zippopotam.us API for approximate ZIP centroid lookup.

Source: <https://docs.zippopotam.us/>

If that service is unavailable, search still works; ResourceBridge simply falls back to exact ZIP matching where possible.

---

## Reset the demo

Double-click:

```text
RESET_DEMO.bat
```

or run:

```powershell
python reset_db.py
```

This returns ResourceBridge to an empty internal database.

---

## Run freshness checks manually

```powershell
python weekly_freshness.py
```

The server also contains a weekly background freshness loop while the application is running.

---

## Configure consensus threshold

Default: **3 distinct CHW confirmations**.

Example:

```powershell
$env:RESOURCEBRIDGE_CONSENSUS_THRESHOLD="4"
python server.py
```

Production governance should be established with the client.

---

## Intentionally removed from the inherited prototype

The final product removes features that did not directly solve the client's resource-navigation problem:

- patient charts
- encounter logging
- caseload management
- calendar / appointment management
- generic profile management
- public organization ratings / trust scores

This keeps ResourceBridge focused and avoids unnecessary PHI, privacy, and security scope.

---

## Production considerations

This is a submission-ready MVP, not a production healthcare system. Before real-world deployment, add:

- real authentication instead of demo CHW identities
- role-based permissions
- audit logs tied to authenticated users
- client-approved moderation / consensus governance
- secure hosted database and backups
- privacy/security review
- external-data agreements and broader source coverage
- CHW usability testing
- cross-source entity resolution for duplicate programs
- production monitoring and rate-limit handling

Do not use real patient/client-identifying information in this prototype.
