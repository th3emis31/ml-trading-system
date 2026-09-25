---
name: seo-audit
family: market
description: Audit a page or site for search visibility and write a findings report where every finding cites the element it came from. Use when the user says SEO, ranking, search, meta tags, indexing, or asks why a page gets no traffic.
---

# SEO audit

An audit is only worth the citations in it. A finding that says "the page needs better keywords" is an
opinion; one that says "line 14: `<title>` is 94 characters and truncates at 60 in results" is a
defect with an address. Write the second kind only.

## Procedure

1. **Fetch the page as a crawler sees it.** The rendered DOM and the raw HTML can differ, and search
   engines read what the server sent. Note which one each finding came from.
2. **Walk the checklist, recording the element and line for every finding.**
   - `<title>`: present, unique, under ~60 characters, carries the page's actual subject
   - meta description: present, under ~155 characters, describes rather than repeats the title
   - exactly one `<h1>`, and a heading order with no skipped levels
   - every `<img>` has meaningful `alt` (decorative images get `alt=""`, which is a decision not an omission)
   - canonical URL present and self-referential unless deliberately pointing elsewhere
   - internal links use descriptive text, never "click here" or a bare URL
   - structured data present and valid for the page type
   - the page states its subject in the first 100 words
3. **Measure what can be measured.** Page weight, request count, largest contentful paint if
   available. A slow page ranks worse regardless of its tags.
4. **Rank findings by what they cost.** A missing title outranks a missing alt attribute. Say which
   three to fix first.
5. **Write the report to a file**, and re-run after the fixes so the count actually falls. An audit
   nobody re-runs is a document, not a fix.

## What good looks like

- Every finding names the element, and the line or selector where it lives.
- Severity reflects search impact, not how easy it was to spot.
- Nothing is claimed about ranking that was not measured — **"this will rank #1" is never a finding.**
  Search engines do not publish their weights, and a confident claim about them is invention.
- The re-run count is reported: 14 findings → 3 findings is the evidence the work landed.

## Acceptance

```acceptance
file: data/seo contains report
number: findings_with_citation >= 1
ask: does every finding name the element or line it came from, with no general advice?
ask: does the re-run after the fixes show a lower finding count?
```
