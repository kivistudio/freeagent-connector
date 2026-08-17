# freeagent-mcp-remote — Tool Inventory

> Reference material for implementing the tool layer. Not meant to be read top-to-bottom
> — look up the module you're implementing, then read [Cross-cutting
> rules](#cross-cutting-rules) and the [Quirks that will bite
> you](#quirks-that-will-bite-you) before writing a line.
>
> **This document was rewritten from scratch against
> [FreeAgent's own docs](https://dev.freeagent.com/docs/).** The previous version
> inventoried the tool set of the TypeScript project this repo takes inspiration from,
> which is sales-and-time-tracking shaped (estimates, credit notes, invoice sending).
> The owner's actual goal is bookkeeping, reconciliation, and tax prep for a UK limited
> company, which needs a substantially different surface — journals, the nominal ledger,
> bank transaction explanations, VAT, corporation tax and payroll were all absent before
> and are the core of the work now.
>
> Every endpoint and field below was read from FreeAgent's documentation — which is a
> starting point, not the truth. It has gaps, internal contradictions and copy-paste
> errors. **The live API is authoritative**: check anything you rely on with the
> `request` tool (`.claude/skills/freeagent-api/SKILL.md`), and **never invent a
> field name**.

## How to read this document

**This is a guide, not a specification.** It records what research suggests is worth
building — deciding against something here is a normal outcome, not a deviation.

Entries are labelled on **two independent axes**, because "we're building this" and "we
know what it returns" are different facts.

**Scope** — are we building it?

| Label | Means |
|---|---|
| **Confirmed** | In scope. |
| **Recommended** | Research suggests it's worth building; not yet decided. |
| **Recommended to exclude** | Suggested to leave out, reason recorded. |
| **Confirmed not needed for now** | Out of scope. May become relevant later. |

**Verification** — do we know what the API actually returns?

| Tag | Means |
|---|---|
| **Shape confirmed** | Called against the live API; date and any surprises noted. |
| *(no tag)* | Docs-derived only. Treat as unverified. |

Almost everything here is scope-**Confirmed** but carries **no shape tag** — decided on,
but designed from docs that have gaps and at least two copy-paste errors. That is the
normal starting state. Use `.claude/skills/freeagent-api/SKILL.md` to call the real API and
add **Shape confirmed** as you go, noting anything that contradicted the docs.

## What the owner wants to do, and which modules serve it

| Goal | Modules |
|---|---|
| Go through all the transactions | `ledger` (nominal ledger), `banking` |
| All the journals | `ledger` (journal sets) |
| Reconciliation / bookkeeping | `reconciliation`, `banking`, `bills`, `expenses` |
| End of year | `year_end`, `reports`, `assets`, `ledger` (account locks) |
| Taxes (limited company) | `taxes`, `reports`, `company` |
| PAYE | `payroll` |
| Decide when I'll be debt free | `reports` (P&L trajectory) + HMRC balances supplied externally |
| Dividends vs salary | `reports` (profit + distributable ceiling), `payroll` (salary paid) |
| Timeslips, tasks, projects | `timeslips`, `tasks`, `projects`, `users`, `contacts` |

## How the two analysis goals are actually served

Both of these are **forward-looking calculations driven by the profit & loss report**,
not reconstructions of historical records. That framing matters, because it changes which
endpoints are load-bearing and makes both goals well supported rather than marginal.

**Dividends vs salary — a calculation over current profit, not a dividend history.** The
owner needs to decide how to split *available* profit, so the inputs are:

- **Profit available** — `freeagent_get_profit_and_loss_summary`.
- **The distributable ceiling** — `retained_profit_carried_forward`, which FreeAgent's
  docs describe as *distributable profit*. This is the direct API answer to "how much can
  legally be taken as dividend". Research located the field on the reporting endpoints;
  confirm which report carries it when implementing this module.
- **Salary already paid** — payslips give `basic_pay`, `tax_deducted`, `employee_ni`,
  `employer_ni`, pension and student-loan fields. The student-loan fields genuinely matter:
  repayments hit salary but not dividends, which shifts the optimum.
- **Corporation tax impact** — derived from the P&L, since salary is deductible and
  dividends are not.

Claude supplies the tax rates, thresholds and allowances itself (they are not in the API)
and must name the tax year it assumes.

> **Historical dividend records are a different question, and the API cannot answer it.**
> There is no `/v2/dividends` and no dividend field anywhere across the 57 resources —
> FreeAgent's web app has the feature, the v2 API does not expose it. This does not block
> the split calculation above. It only means "what did I actually take in dividends last
> year" would have to be reconstructed by finding the dividend equity category via
> `/v2/categories` at runtime and filtering journals and explanations by its URL. **Never
> hardcode a nominal code** for this — FreeAgent's own API forum describes the published
> ranges as "aspirational guidance rather than current implementation".

**Debt-free date — profit trajectory plus externally-supplied HMRC balances.** The owner
brings the HMRC debt figures from outside this server; the API's contribution is the
income trajectory (P&L over successive periods) and the cash position. Note that
`freeagent_get_tax_timeline` does supply `amount_due` for *upcoming* obligations, which
covers part of the picture, though not arrears the owner already knows about.

Hire purchases are **out of scope** — the owner doesn't use them. (For the record, had
they been needed: `/v2/hire_purchases` exposes only `url`, `description`, `bill` and two
category URLs — no balance, term, instalment or rate — so it would have been a pointer
resource requiring reconstruction from the trial balance.)

## The one dangerous quirk

**`/v2/cashflow` silently returns `total: 0` for future dates instead of erroring.** This
is the single most dangerous behaviour in this document: a model that misses it reads "no
money coming in" and forecasts catastrophically wrong. It is historical and monthly only.
The tool description must state this explicitly.

More broadly, `/v2/company/tax_timeline` is the *only* genuinely forward-looking endpoint
FreeAgent exposes. Every other projection is Claude's modelling over historical data.

## Recommended build order

Not a requirement, but the sequence that gets to something useful soonest. **Phase 1 is
the smallest set that makes the tool genuinely usable for bookkeeping.**

| Phase | Modules | Why here |
|---|---|---|
| **0 — done** | `company` | Establishes the registration pattern |
| **1** | `reports`, `ledger`, `banking`, `reconciliation` | Read the books, then reconcile them. Includes both write paths: explaining bank transactions and posting journals. |
| **2** | `bills`, `expenses`, `assets` | Day-to-day bookkeeping entry and year-end asset review |
| **3** | `taxes`, `payroll`, `year_end` | Tax prep and PAYE |
| **4** | `timeslips`, `tasks`, `projects`, `users`, `contacts` | Running the business rather than accounting for it |

## Cross-cutting rules

Apply mechanically to every row unless a module says otherwise.

1. **Every path-interpolated ID uses `SafeId`.** No exceptions, including `nominal_code`
   in `categories` (whose hyphen in `750-1` the regex already permits) and
   `period_ends_on` date-keyed paths.
2. **Full-URL reference params are a different class from path IDs.** FreeAgent
   cross-references resources by URL (`bank_account=https://api.freeagent.com/v2/bank_accounts/1`),
   and these appear in query strings and bodies, **not** in the path — so `client.py`'s
   origin guard does not cover them, because it guards the path only. Recommendation:
   tools accept bare `SafeId` values and construct the URL server-side. Accepting a
   caller-supplied full URL puts model-derived and FreeAgent-derived strings straight into
   request params.
3. **`build_body` must not drop falsy zeros.** Projects mark `budget` required and the
   docs say send `0` when there is no budget. The `is not None` test is load-bearing.
4. **Pagination — `page`/`per_page`, with `X-Total-Count` and `Link` headers.**
   **Shape confirmed** against the live API on 2026-08-17 using `/contacts`:
   - `per_page` is honoured (`per_page=1` returned exactly one of 29 records).
   - `X-Total-Count` is present and gives the true total.
   - `Link` is present with `rel='next'` and `rel='last'`.
   - **Gotcha: the `Link` header uses single quotes** — `rel='next'`, not the
     double-quoted `rel="next"` that RFC 5988 specifies and that most parsers expect.
     Any pagination-following code must handle this or it will silently find no next page.

   Still unverified: whether the documented default of 25 and maximum of 100 hold, and
   whether every list endpoint paginates. The risk remains that a model asking about a
   year analyses one page and believes it has everything, so **list tools should request
   an explicit `per_page` rather than relying on the default.**
5. **`nested=true`-style params default to `false`.** They inflate responses with
   third-party-controlled free text that the model reads back.
6. **Enum casing is inconsistent and must be encoded as literal types.** Projects carry
   `budget_units` (`Hours`/`Days`/`Monetary`, title case) and `billing_period`
   (`hour`/`day`, lower case) on the *same object*.

---

## Module: `company` — **Confirmed** · **Shape confirmed**

Built and tested. Shape verified against the live account on 2026-08-17 — which corrected
one docs-derived assumption: `sales_tax_rates` is an array of strings, not of objects.
`annual_accounting_periods` confirmed present as an array of `starts_on`/`ends_on` pairs,
along with `initial_vat_basis` and `initially_on_frs`.

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_get_company` | GET `/company` | R |
| `freeagent_get_tax_timeline` | GET `/company/tax_timeline` | R |

Load-bearing for everything else: `annual_accounting_periods`,
`first_accounting_year_end`, `initial_vat_basis` (Invoice/Cash) and `initially_on_frs`
are needed to frame a year-end or VAT question at all. The tax timeline's `is_personal`
boolean separates the director's obligations from the company's.

**Caution:** the VAT fields are `initial_*` — the position at registration. If the company
has since changed scheme they are stale. A current VAT basis / flat-rate percentage is
**not exposed anywhere in the API** (looked for, not found). Neither are UTR, PAYE
reference, or Companies House filing dates.

**Excluded:** `PUT /company` (settings a prompt injection would most want to change);
`/company/business_categories` (static picklist); `/v2/currencies` (that docs page is a
static ISO-4217 list, not a callable endpoint).

---

## Module: `reports` — **Confirmed**

| Tool | Method & path | Params |
|---|---|---|
| `freeagent_get_profit_and_loss_summary` | GET `/accounting/profit_and_loss/summary` | `from_date`+`to_date` or `accounting_period` |
| `freeagent_get_balance_sheet` | GET `/accounting/balance_sheet` | `as_at_date` |
| `freeagent_get_balance_sheet_opening_balances` | GET `/accounting/balance_sheet/opening_balances` | — |
| `freeagent_get_trial_balance_summary` | GET `/accounting/trial_balance/summary` | `from_date`, `to_date` |
| `freeagent_get_trial_balance_opening_balances` | GET `/accounting/trial_balance/summary/opening_balances` | — |
| `freeagent_get_cashflow` | GET `/cashflow` | — |

**No `SafeId` needed in this module** — every path is a fixed literal and all input is
query params. Unusual for this repo; worth noting at review so its absence doesn't read
as an oversight.

### Quirks
- **Report paths are inconsistent — do not pattern-match.** P&L *requires* the `/summary`
  suffix; balance sheet has *none*; trial balance requires it and nests its
  opening-balances variant beneath it (`/trial_balance/summary/opening_balances` vs
  `/balance_sheet/opening_balances`).
- **Three different period schemes.** P&L: `from_date`/`to_date`, or `accounting_period`
  formatted `2022/23`, capped at ≤12 months or one accounting year. Balance sheet: a
  single `as_at_date` selecting the period *containing* that date. Trial balance:
  `from_date`/`to_date` with different defaulting.
- **P&L has no category breakdown** — aggregate `income`/`expenses` plus a `less` array.
  Category-level analysis comes from the trial balance, joined on `display_nominal_code`
  (**not** `nominal_code`, whose second segment is a resource ID for code 750 and 900–910).
- **`/cashflow` returns `total: 0` for future dates rather than erroring.** See limit 3.

### Unverified
Whether the balance sheet has a `long_term_liabilities` section (absent from the example,
may appear for companies that have such balances — directly relevant to the debt
question); `/cashflow`'s default period and currency; trial balance behaviour with
`from_date` alone.

---

## Module: `ledger` — **Confirmed**

The nominal ledger, manual journals, the chart of accounts, and period locks.

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_transactions` | GET `/accounting/transactions` | R |
| `freeagent_list_journal_sets` | GET `/journal_sets` | R |
| `freeagent_get_journal_set` | GET `/journal_sets/{id}` | R |
| `freeagent_get_opening_balances` | GET `/journal_sets/opening_balances` | R |
| `freeagent_create_journal_set` | POST `/journal_sets` | W |
| `freeagent_update_journal_set` | PUT `/journal_sets/{id}` | W |
| `freeagent_delete_journal_set` | DELETE `/journal_sets/{id}` | W |
| `freeagent_list_categories` | GET `/categories` | R |
| `freeagent_get_category` | GET `/categories/{nominal_code}` | R |
| `freeagent_list_account_locks` | GET `/account_locks` | R |

### Quirks
- **Path trap: it is `/accounting/transactions`, not `/transactions`.** The only resource
  in this scope with the extra segment.
- **Hard 12-month cap on transactions**, verbatim: *"Requested date periods must be equal
  or less than 12 months or be contained within a single accounting year."* Multi-year
  questions need one call per year. **With no dates it silently returns only the current
  accounting period to date** — a model that omits dates will quietly analyse a partial
  year.
- **Journal set updates are three-way, driven by `url` presence**: omit to add, include to
  modify, include with `_destroy: true` to remove. This must be in the tool description or
  Claude will send a replacement array and duplicate every entry.
- **`tag` is a footgun** — tagged journal sets become uneditable in the FreeAgent web app.
  Keep it opt-in, never defaulted.
- **`GET /categories/{nominal_code}` does not return a `category` key.** It returns a
  singular object under the plural *set* name (`{"income_categories": {…}}`), varying by
  set. Do not unwrap generically.
- **`/journal_sets/opening_balances` collides with `/journal_sets/{id}`** — `SafeId`'s
  regex happily accepts the literal string `opening_balances`, so this needs its own
  hardcoded-path tool rather than being reachable through the by-ID tool.
- **`account_locks` requires `Full Access`**, higher than anything else here, and
  `user_lock` is *omitted* (not `false`) on system locks.

### Unverified
Pagination on transactions and journal sets; whether journal entry `description` is
settable on create (it appears in every response example including the POST response, but
is absent from the attributes table); whether `dated_on` is required on create
(FreeAgent's own Required column marks it `?`); whether an account lock actually rejects
journal writes inside the locked period; the 8xx VAT nominal code band.

**Excluded:** `/notes` — attaches only to contacts and projects, never to a transaction or
journal, so it has no ledger relevance.

---

## Module: `banking` — **Confirmed**

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_bank_accounts` | GET `/bank_accounts` | R |
| `freeagent_get_bank_account` | GET `/bank_accounts/{id}` | R |
| `freeagent_list_bank_transactions` | GET `/bank_transactions` | R |
| `freeagent_get_bank_transaction` | GET `/bank_transactions/{id}` | R |
| `freeagent_list_bank_feeds` | GET `/bank_feeds` | R |

Read-only by design: creating and deleting bank accounts serves none of the stated goals
and is the kind of change that should happen in FreeAgent's own UI.

`view` values on transactions, confirmed: `all`, `unexplained`, `explained`, `manual`,
`imported`, `marked_for_review`. **`view=unexplained` is the reconciliation worklist** and
the most important filter in the project. `marked_for_review` is distinct — FreeAgent
guessed an explanation and it awaits approval.

**Injection surface:** `bank_transaction.description` is free text derived from payment
references — attacker-influencable content that the model reads back. Treat accordingly.

### Unverified
Pagination on `/bank_transactions` (not mentioned on the page); the `current_balance` sign
convention for `CreditCardAccount`; the allowed values of `bank_feeds.state`.

---

## Module: `reconciliation` — **Confirmed** (highest value)

Bank transaction explanations. This is what "doing the bookkeeping" actually means.

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_bank_transaction_explanations` | GET `/bank_transaction_explanations` | R |
| `freeagent_get_bank_transaction_explanation` | GET `/bank_transaction_explanations/{id}` | R |
| `freeagent_explain_bank_transaction` | POST `/bank_transaction_explanations` | W |
| `freeagent_update_bank_transaction_explanation` | PUT `/bank_transaction_explanations/{id}` | W |
| `freeagent_delete_bank_transaction_explanation` | DELETE `/bank_transaction_explanations/{id}` | W |

### Quirks — read all of these before implementing
- **The docs state verbatim that all identifiers here use full FreeAgent URLs, not bare
  IDs.** Affects `bank_account`, `bank_transaction`, `category`, `paid_invoice`,
  `paid_bill`, `paid_user`, `transfer_bank_account`, `project`, `stock_item`,
  `disposed_asset`, `property`, `direct_contact`. See cross-cutting rule 2 for how to
  handle this safely.
- **`bank_account` XOR `bank_transaction` on create is a genuine hazard.** Passing
  `bank_transaction` explains an existing imported transaction — what the owner wants.
  Passing `bank_account` makes FreeAgent **create a new bank transaction**, fabricating
  ledger entries the bank statement doesn't contain. This needs a hard warning in the tool
  description.
- **Transfers auto-create the opposite side** (confirmed verbatim), returning read-only
  `linked_transfer_explanation` / `linked_transfer_account`. Explaining a transfer from
  both ends double-counts it.
- **`type` is read-only** — derived from which linking field you populate, not chosen. A
  model looking for a `type` parameter will fail.
- Approving a FreeAgent guess is done by setting `marked_for_review: false`.

### Unverified — highest priority
**How split and partial explanations work.** The docs contain zero discussion of
splitting, partial explanation, or `unexplained_amount`; it is only structurally
inferable from the array plus the residual field. Split explanations are routine
bookkeeping, so this needs resolving live before this module can be trusted. Also
unverified: whether `ec_status` is genuinely mandatory on POST; the element shape of the
embedded explanations array; and two documented singular-path oddities that look like
typos (`DELETE /v2/bank_transaction/:id`, `.../v2/bank_transaction_explanation/125`).

---

## Module: `bills` — **Confirmed**

| Tool | Method & path | R/W | Body key |
|---|---|---|---|
| `freeagent_list_bills` | GET `/bills` | R | — |
| `freeagent_get_bill` | GET `/bills/{id}` | R | — |
| `freeagent_create_bill` | POST `/bills` | W | `bill` |
| `freeagent_update_bill` | PUT `/bills/{id}` | W | `bill` |
| `freeagent_delete_bill` | DELETE `/bills/{id}` | W | — |

Line items are a real `list[BillItem]` Pydantic model, never a JSON-stringified string.
Confirmed fields: `category`, `description`, `total_value`, `sales_tax_rate`. Max 40 items.

### Quirks
**The line-item PUT is a three-way protocol**, and getting it wrong silently corrupts a
bill: `url` + fields = edit an existing item; `{"url": "...", "_destroy": 1}` = delete it
(integer `1`, **not** boolean `true`); `{"url": "", ...}` = add a new one. **Omitting an
item does not delete it** — a model sending a "replacement" array will append rather than
replace.

### Unverified
The `property` field (listed among full-URL fields, absent from the attributes table);
the exact writable-attribute set on PUT.

---

## Module: `expenses` — **Confirmed**

| Tool | Method & path | R/W | Body key |
|---|---|---|---|
| `freeagent_list_expenses` | GET `/expenses` | R | — |
| `freeagent_get_expense` | GET `/expenses/{id}` | R | — |
| `freeagent_create_expense` | POST `/expenses` | W | `expense` |
| `freeagent_update_expense` | PUT `/expenses/{id}` | W | `expense` |
| `freeagent_delete_expense` | DELETE `/expenses/{id}` | W | — |
| `freeagent_get_mileage_settings` | GET `/expenses/mileage_settings` | R | — |

### Quirks
- **Expenses have no nested line-item array.** Flat, one category per record. Do not
  mirror the bill shape.
- **`category` is polymorphic** — a full URL normally, but the literal string `"Mileage"`
  for mileage claims.
- **Batch create uses a plural root** (`{"expenses": [...]}`) versus singular for single
  create and update.

### Unverified
`/expenses/mileage_settings` response field names (described in prose only, no schema).

---

## Module: `assets` — **Recommended**

Capital assets — year-end review. Hire purchases are out of scope (not used).

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_capital_assets` | GET `/capital_assets` | R |
| `freeagent_get_capital_asset` | GET `/capital_assets/{id}` | R |
| `freeagent_list_capital_asset_types` | GET `/capital_asset_types` | R |

### Quirks
- **Capital assets are read-only** — two GETs only. Assets are created implicitly by
  coding a bill item or expense to a capital-asset category.
- **No `net_book_value` or `purchase_value` on capital assets.** Must be summed from
  `capital_asset_history` or read off the balance sheet. A real gap for year-end.
- `depreciation_profile` is a nested write-side model with no endpoints of its own — a
  discriminated union on `method` (`asset_life_years` 2–25 for straight line,
  `annual_depreciation_percentage` 1–99 for reducing balance).

---

## Module: `taxes` — **Confirmed**

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_vat_returns` | GET `/vat_returns` | R |
| `freeagent_get_vat_return` | GET `/vat_returns/{period_ends_on}` | R |
| `freeagent_list_corporation_tax_returns` | GET `/corporation_tax_returns` | R |
| `freeagent_get_corporation_tax_return` | GET `/corporation_tax_returns/{period_ends_on}` | R |
| `freeagent_list_self_assessment_returns` | GET `/users/{user_id}/self_assessment_returns` | R |
| `freeagent_get_self_assessment_return` | GET `/users/{user_id}/self_assessment_returns/{period_ends_on}` | R |

### Quirks
- **Returns are keyed by `period_ends_on` (a `YYYY-MM-DD` date) in the path, not by an
  integer ID.** Still `SafeId`-validated — the regex rejects the `.` and `/` that would
  make a date-shaped string dangerous, and accepts `2026-03-31`.
- **The VAT `breakdown` (the 9 boxes) is the payload that matters.**
- **Corporation tax returns give a bottom-line `amount_due` only** — no taxable profit, no
  CT600 breakdown. The actual figures must come from P&L / balance sheet / trial balance.
- Self-assessment returns nest under a user, so they need a `user_id` as well as a period.

### Excluded
`/sales_tax_periods` — the docs say "Only available to US and Universal companies", so
it is useless for a UK Ltd (VAT registration details live on `GET /company` instead). The
EC VAT MOSS rate lookup (post-Brexit N/A). All `mark_as_filed` / `mark_as_paid` verbs —
high-consequence compliance assertions requiring elevated permissions, and not needed for
analysis. Note the two path shapes differ if they are ever added: VAT and self-assessment
use `.../{period_ends_on}/payments/{payment_date}/mark_as_paid`, corporation tax marks
payment at the return level with no `/payments/` segment.

### Unverified
`box_number` has a docs contradiction (attributes table says String, the example JSON
emits a bare integer `1`); the self-assessment JSON root keys are never shown, so
`self_assessment_returns`/`self_assessment_return` is assumed by convention; whether any
of these list endpoints support `updated_since` or date filtering (inferred from absence,
so assume lists must be fetched whole and filtered client-side).

---

## Module: `payroll` — **Confirmed**

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_payroll_periods` | GET `/payroll/{year}` | R |
| `freeagent_get_payroll_period` | GET `/payroll/{year}/{period}` | R |
| `freeagent_list_payroll_profiles` | GET `/payroll_profiles/{year}` | R |

### Quirks
- **`{year}` is the tax year END** — `2026` means April 2025 to March 2026, stated
  verbatim in the docs. **`{period}` is 0-indexed, 0–11.** Both must be spelled out in the
  tool descriptions or the model will get them wrong.
- **The two near-identical paths return different shapes.** `GET /payroll/{year}` → root
  keys `periods` + `payments`. `GET /payroll/{year}/{period}` → root key `period` with
  nested `payslips`.
- **There is no all-payslips-for-the-year call**, so a full tax year is up to 12 requests.
- Payroll is explicitly **read-only** (it is RTI-filed data) and **UK companies only**.
- The "single user" payroll profile endpoint is just a `user` query param on the same
  path, not a separate route.

Payslips are the strong half of the dividends-vs-salary question: `basic_pay`,
`tax_deducted`, `employee_ni`, `employer_ni`, pension fields, and the student-loan fields
(which matter — repayments hit salary but not dividends, shifting the optimum).

### Unverified
`mark_as_unpaid` is documented as **GET** while every sibling verb is PUT — near-certainly
a docs typo, and another reason it stays out of v1. The docs are also internally
inconsistent about `{period}` being 0–11 while offering a `Weekly` frequency.

---

## Module: `year_end` — **Confirmed**

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_final_accounts_reports` | GET `/final_accounts_reports` | R |
| `freeagent_get_final_accounts_report` | GET `/final_accounts_reports/{period_ends_on}` | R |

**Carries no financial figures at all** — it is a filing-status tracker (dates plus
`filing_status`). Useful for "where am I in the year-end process", useless for "what are
the numbers". Say so in the description.

---

## Modules: `projects` · `tasks` · `timeslips` — **Confirmed** · `users` — **Recommended** · `contacts` — **Recommended** · **Shape confirmed**

The business-operations side. Unlike the accounting modules, writes matter here — the
owner will want to log time, not just read it.

| Tool | Method & path | R/W |
|---|---|---|
| `freeagent_list_projects` / `_get_project` | GET `/projects`, `/projects/{id}` | R |
| `freeagent_create_project` / `_update_project` | POST `/projects`, PUT `/projects/{id}` | W |
| `freeagent_list_tasks` / `_get_task` | GET `/tasks`, `/tasks/{id}` | R |
| `freeagent_create_task` | POST `/tasks?project={full project URL}` | W |
| `freeagent_update_task` | PUT `/tasks/{id}` | W |
| `freeagent_list_timeslips` / `_get_timeslip` | GET `/timeslips`, `/timeslips/{id}` | R |
| `freeagent_create_timeslip` / `_update_timeslip` / `_delete_timeslip` | POST/PUT/DELETE `/timeslips[/{id}]` | W |
| `freeagent_start_timer` | POST `/timeslips/{id}/timer` | W |
| `freeagent_stop_timer` | DELETE `/timeslips/{id}/timer` | W |
| `freeagent_get_current_user` | GET `/users/me` | R |
| `freeagent_list_users` | GET `/users` | R |
| `freeagent_list_contacts` / `_get_contact` / `_create_contact` | GET/POST `/contacts` | R/W |

### Quirks
- **Timer verbs are asymmetric on the same path** — start is `POST /timeslips/{id}/timer`,
  stop is `DELETE /timeslips/{id}/timer`. No `/start` or `/stop` sub-path, no PUT form. The
  timer is a sub-resource whose *existence* is the running state. Both need an existing
  timeslip, so "start a timer" is really create-then-POST-timer.
- **`POST /tasks` puts `project` in the query string**, alongside a `task`-rooted body —
  the one endpoint that uses `build_params` and `build_body` together. The value is a full
  URL.
- **`GET /users/me`** is a literal path segment — give it a dedicated hardcoded-path tool
  rather than routing `"me"` through an ID param, so the path stays a constant. It is also
  the cheapest connectivity smoke test, which is why `auth.py` verifies tokens against it.
- **Task deletion is excluded**: the docs show `DELETE /v2/users/:id` in the Delete Task
  section — clearly a copy-paste error, and not worth guessing the real path.
- **Contacts get three tools, not CRUD.** Nothing in the stated goals is advanced by
  editing a client's postcode through Claude; contacts matter as a lookup for
  `contact={url}` on a project. The accounting-relevant fields (`account_balance`,
  `is_cis_subcontractor`, `cis_deduction_rate`) are all readable.

### Unverified
**`hours` typing is genuinely contradictory in FreeAgent's own docs** — the attributes
table says Decimal, every JSON example shows a quoted string `"12.0"`. Recommendation:
accept `str | float` and pass through unchanged. Also: `updated_since` on the projects
list (present on tasks/timeslips/contacts, absent from the projects page);
`current_payroll_profile` shape; `permission_level` integer semantics; `role` allowed
values.

**Contacts — Shape confirmed** (2026-08-17), resolving two docs discrepancies:
`account_balance` is present and is a **string** (the attributes table omits it entirely),
and `active_projects_count` is an **integer**, not the String the attributes table claims.
Also confirmed present: `charge_sales_tax` (string), `status`, and five `emails_*` booleans.

---

## Excluded, with reasons

| Resource | Status | Why |
|---|---|---|
| Invoices, estimates, credit notes, recurring invoices, price list items | Recommended to exclude | Sales-side workflow; not among the stated goals. Invoices remain readable *indirectly* as explanation targets. |
| Attachments | Recommended to exclude | Metadata plus expiring `content_src` URLs to binaries Claude cannot read; the useful metadata already comes back inline on the parent bill or expense. |
| Notes | Recommended to exclude | Attach only to contacts and projects, never to transactions or journals. |
| Sales tax periods | Confirmed not needed for now | US/Universal companies only. |
| Bank feeds (beyond list) | Recommended to exclude | Feed management belongs in FreeAgent's UI. |
| Hire purchases | Confirmed not needed for now | Not used by this company. |
| Stock items, properties, CIS bands, capital asset type writes | Confirmed not needed for now | No connection to the stated goals. |
| `PUT /company`, `mark_as_filed`/`mark_as_paid` | Recommended to exclude | High-consequence state changes; exactly what a prompt injection would target. |
| Accountancy Practice API, account locks writes | Recommended to exclude | Requires elevated permissions this single-tenant tool should not hold. |

## Verification notes

Everything above came from FreeAgent's documentation pages, read during research.
Confidence is marked per module. The items most likely to bite during implementation, in
order:

1. **Split/partial bank transaction explanations** — undocumented, and routine bookkeeping.
2. **The transactions 12-month cap and silent partial-year default.**
3. **Journal set and bill line-item three-way update protocols.**
4. **`/cashflow` returning zeros for future dates.**
5. **Whether bank transaction explanations accept an attachment.**

Answer these against the live API, not by re-reading the docs.

### Resolved so far

- **Pagination mechanics — 2026-08-17, via `/contacts`.** `per_page` is honoured,
  `X-Total-Count` gives the true total, and `Link` carries `rel='next'`/`rel='last'` —
  **with single quotes**, not the RFC-standard double quotes most parsers expect. Still
  open: whether the documented default of 25 and max of 100 hold, and whether every
  endpoint paginates. See [cross-cutting rule 4](#cross-cutting-rules).
- **`/company` shape — 2026-08-17.** `sales_tax_rates` is an array of strings, not of
  objects. `annual_accounting_periods`, `initial_vat_basis` and `initially_on_frs` all
  confirmed present.
- **Contacts field types — 2026-08-17.** `account_balance` present and a string (absent
  from the docs' attribute table); `active_projects_count` an integer, not the String the
  table claims.
