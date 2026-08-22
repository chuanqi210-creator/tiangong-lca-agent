# Input Case Schema

Use this schema for the local MVP builder. It is intentionally small so research teams can prepare a demo case quickly before moving into full TianGong/TIDAS validation.

## Required Shape

```json
{
  "project": {
    "title": "...",
    "research_object": "...",
    "functional_unit": "...",
    "system_boundary": "...",
    "geography": "...",
    "time_period": "..."
  },
  "sources": [
    {
      "key": "paper-1",
      "title": "...",
      "citation": "...",
      "url": "..."
    }
  ],
  "flows": [
    {
      "name": "...",
      "direction": "input|output",
      "quantity": 1.0,
      "unit": "kg",
      "category": "material|energy|product|emission",
      "evidence": "paper-1"
    }
  ],
  "processes": [
    {
      "name": "...",
      "description": "...",
      "inputs": ["..."],
      "outputs": ["..."],
      "evidence": "paper-1"
    }
  ],
  "assumptions": ["..."]
}
```

## Review Rules

- Every `flows[].evidence` and `processes[].evidence` must match a source `key`.
- Every process input/output must match a declared flow `name`.
- Treat quantities as extracted candidate values, not reviewed final values.
- Add page numbers or table names to `citation` when available.
