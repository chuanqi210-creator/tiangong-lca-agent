# Archive and LCA package fixtures

The text fixtures in this directory carry the minimum structural signatures
used to classify LCA package *candidates*. They are not scientifically
validated datasets.

Safe and hostile ZIP payloads are generated deterministically in
`tests/test_service_archive_inspection.py`. The tests must mutate central and
local header fields, encryption flags, Unix node metadata, nesting, and size
projections byte-for-byte; generating those payloads at test time keeps the
attack construction reviewable instead of committing opaque binary archives.

No fixture authorizes extraction into a user-named directory, native-tool
execution, or Project Record acceptance.
