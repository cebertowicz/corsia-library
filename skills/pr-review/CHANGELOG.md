1.0.0: First release. Reviews each pull-request head against the repository's own standards; in live mode posts commit statuses, pull-request comments and replies, review-thread resolutions and one summary comment; fetches heads into the configured checkout.
1.0.1: measures.yaml syncs again: the KPI match and corroboration join keys are `on` again (they had become `true`) and quiet KPIs are `alert: off` again; a test reads the file with YAML 1.2 booleans to keep it that way.
1.0.2: Set up now says the owning agent's project must track each reviewed repository, and runs measure_sync before creating the rules that match its qa events.
1.0.3: Set up puts the owning agent in its project before installing: a copy installed before the agent joined a project is no longer found there.
