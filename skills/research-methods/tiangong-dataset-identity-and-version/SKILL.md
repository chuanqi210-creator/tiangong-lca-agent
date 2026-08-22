---
name: tiangong-dataset-identity-and-version
description: Use when a TianGong Flow, Process, lifecycle model, LCIA method, or result must be identified, version-pinned, distinguished from a similarly named object, or checked for temporal supersession.
---

# Verify TianGong Dataset Identity and Version

## Core method

Resolve identity before using content.

1. Identify the required TianGong object kind and exact ID/version fields.
2. Retrieve only through a source-locked TianGong capability permitted for the project.
3. Preserve object ID, version, source-lock identity, retrieval receipt, geography, time, and dataset role.
4. Treat names, search similarity, and download-list presence as discovery evidence only.
5. Keep conflicting or superseding versions visible until the study's temporal rule resolves them.

## Acceptance shape

Produce `dataset_identity_confirmation` or a typed identity/version gap. Bind every confirmed identity to TianGong evidence; never merge similarly named artifacts or claim “latest/current” without explicit official support.

## Common mistakes

- Treating a label as a stable object identity.
- Dropping a version or replacing it with retrieval time.
- Calling an accessible candidate official before the authority check.
- Assuming a newer numeric version is applicable to the study period.
