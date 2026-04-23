# Integration examples

mclaude itself does not call any task tracker. Integration glue lives
in your own repo so you can use your own credentials, your own auth
flow, and your own conventions.

This directory contains **reference scripts** you can copy into your
team's private repo and adapt.

## What is in here

- `handoff_refs_to_tracker.py` - generic template. Scans
  `.claude/handoffs/` for `provider:id` tokens, calls a provider-specific
  poster function, keeps idempotency state in
  `.claude/handoffs/.link-state.json`. Swap the `_post_to_provider`
  function for your tracker and you are done.

## Typical adaptation (Linear example)

```python
# In your team's private repo, e.g. team-tools/scripts/handoff_to_linear.py

from examples.integrations.handoff_refs_to_tracker import scan_once
import os, urllib.request, json

def post_linear_comment(issue_key: str, body: str) -> None:
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        method="POST",
        headers={
            "Authorization": os.environ["LINEAR_API_KEY"],
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "query": "mutation($id: String!, $body: String!) { "
                     "commentCreate(input: {issueId: $id, body: $body}) "
                     "{ success } }",
            "variables": {"id": issue_key, "body": body},
        }).encode(),
    )
    urllib.request.urlopen(req, timeout=15)

if __name__ == "__main__":
    scan_once(
        project_root=".",
        provider="linear",
        poster=post_linear_comment,
    )
```

Keep this script in your own repo. Never commit credentials. Never
check the script into mclaude itself.

## Why integrations are NOT in the library

Three reasons:

1. **Credentials.** Every team has its own API tokens, OAuth flows, or
   SSO. Hard-coding a provider into mclaude would force everyone to
   deal with auth that is not theirs.

2. **API drift.** Task trackers change their APIs. If Linear deprecates
   an endpoint, mclaude would need a release to keep working. Keeping
   integrations out means your private script can be patched without
   updating the library.

3. **Policy.** Some teams post verbose comments, some post terse ones.
   Some close the task automatically, some never do. That is a policy
   decision; libraries should not make it for you.
